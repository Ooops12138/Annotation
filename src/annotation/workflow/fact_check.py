"""A-003: textbook-backed fact checking after content and quiz generation."""

from __future__ import annotations

import json
import hashlib
import logging
import uuid
from collections.abc import Iterable
from typing import Any

from annotation.workflow.concurrency import LockedSearch, ordered_batches

from annotation.domain.artifacts import (
    ContextExcerpt,
    RetrievalCall,
    ContentArtifact,
    ContentTask,
    ContextPack,
    FactCheckArtifact,
    FactCheckAssessment,
    FactCheckClaim,
    FactCheckEvidence,
    FactCheckPolicy,
    FactCheckRoundTrace,
    FactCheckUnitTrace,
    KnowledgeUnit,
    LearningBlueprint,
    QuizArtifact,
    QuizDraft,
    ReviewIssue,
    SourceBlock,
)
from annotation.fact_checking import (
    DisabledWebResourceSearchSkill,
    EvidenceRecord,
    TextbookDatabaseSearchSkill,
    WebResourceSearchSkill,
)
from annotation.fact_checking.contracts import UnifiedRetrievalTool
from annotation.prompt_loader import load_prompt
from annotation.providers import ModelProvider, ProviderError, StructuredGenerationRequest
from annotation.retrieval import UnifiedRetrievalService
from annotation.ingestion.semantic_chunker import build_semantic_blocks
from annotation.workflow.content import render_context_pack
from annotation.workflow.content_support import _content_artifact_from_draft, _content_hard_check, _content_prompt_task
from annotation.workflow.models import (
    ContentDraft, FactCheckAssessmentDraft, FactCheckAssessmentsDraft,
    FactCheckClaimsDraft, FactCheckRetrievalDecision,
)
from annotation.workflow.quiz import validate_quiz_artifact, quiz_draft_issues, accepted_content_context


EXTRACT_CLAIMS_PROMPT_VERSION = "extract_fact_check_claims:v4:quiz-context"
FACT_CHECK_ASSESS_PROMPT_VERSION = "assess_fact_check_claims:v3:optional-subject"
FACT_CHECK_RETRIEVAL_PROMPT_VERSION = "fact_check_retrieval:v4:mode-selection"
REGENERATE_QUIZ_PROMPT_VERSION = "regenerate_quiz_for_fact_check:v5:optional-subject"

_LOGGER = logging.getLogger(__name__)


def _failure(stage: str, exc: Exception) -> str:
    """Keep operational failures distinguishable from content judgements."""
    _LOGGER.warning("%s: %s", stage, exc, exc_info=True)
    return f"{stage}:{getattr(exc, 'category', type(exc).__name__)}:{str(exc)[:200]}"


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _output_limit(provider: ModelProvider, default: int) -> int:
    limit = getattr(getattr(provider, "capabilities", None), "max_output_tokens", None)
    return min(default, int(limit)) if limit else default


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _merge_evidence(*groups: list[FactCheckEvidence]) -> list[FactCheckEvidence]:
    result: list[FactCheckEvidence] = []
    seen = set()
    for group in groups:
        for item in group:
            key = (item.source_kind, item.source_ref or item.url or item.locator, item.text)
            if key not in seen:
                seen.add(key)
                result.append(item)
    return result


def _dedupe_issues(issues: Iterable[ReviewIssue]) -> list[ReviewIssue]:
    result: list[ReviewIssue] = []
    seen: set[str] = set()
    for issue in issues:
        if issue.issue_id not in seen:
            result.append(issue)
            seen.add(issue.issue_id)
    return result


def _evidence(record: EvidenceRecord) -> FactCheckEvidence:
    return FactCheckEvidence(
        evidence_id=record.evidence_id,
        source_kind=record.kind.value,
        provider=record.provider,
        locator=record.locator or "unavailable",
        text=record.text,
        source_ref=record.source_ref,
        source_refs=list(record.source_refs or ([record.source_ref] if record.source_ref else [])),
        url=record.url,
        rank=record.rank,
    )


def _search_reason(prefix: str, result: Any) -> str:
    status = getattr(getattr(result, "status", None), "value", getattr(result, "status", "error"))
    detail = str(getattr(result, "error_message", "") or "")[:200]
    return f"{prefix}:{status}" + (f":{detail}" if detail else "")


def _issue(
    *,
    task: ContentTask,
    suffix: str,
    category: str,
    severity: str,
    layer: str,
    message: str,
    target_id: str | None = None,
    source_refs: Iterable[str] = (),
    suggested_action: str | None = None,
) -> ReviewIssue:
    return ReviewIssue(
        issue_id=f"fact-check-{task.task_id}-{suffix}",
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        layer=layer,  # type: ignore[arg-type]
        message=message,
        target_id=target_id,
        source_refs=_unique(source_refs),
        suggested_action=suggested_action,
    )


def _targets(content: ContentArtifact, quiz: QuizArtifact | None) -> list[dict[str, Any]]:
    targets = [{
        "target_id": content.artifact_id,
        "artifact_id": content.artifact_id,
        "role": "explanation",
        "text": content.content,
        "source_refs": list(content.source_refs),
    }]
    for index, callout in enumerate(content.metadata.get("callouts", []), start=1):
        targets.append({
            "target_id": f"{content.artifact_id}:callout-{index}",
            "artifact_id": content.artifact_id,
            "role": "explanation",
            "text": f"{callout.get('title', '')}\n{callout.get('content', '')}",
            "source_refs": list(content.source_refs),
        })
    if quiz is None:
        return targets
    for question in quiz.questions:
        refs = list(question.source_refs or quiz.source_refs)
        for role, text in (
            ("quiz_question", question.question),
            ("quiz_answer", f"题干：{question.question}\n正确答案：{question.answer}"),
            ("quiz_explanation", f"题干：{question.question}\n正确答案：{question.answer}\n解析：{question.explanation}"),
        ):
            targets.append({
                "target_id": f"{question.question_id}:{role}",
                "artifact_id": quiz.artifact_id,
                "role": role,
                "question_context": question.question,
                "text": text,
                "source_refs": refs,
            })
    return targets


def _extract_claims(
    provider: ModelProvider,
    *,
    unit: KnowledgeUnit,
    content: ContentArtifact,
    quiz: QuizArtifact | None,
    policy: FactCheckPolicy,
    round_number: int,
) -> tuple[list[FactCheckClaim], str | None]:
    """Extract facts and viewpoints; quiz distractors are not supplied as targets."""

    targets = _targets(content, quiz)
    by_id = {target["target_id"]: target for target in targets}
    prompt = load_prompt(
        "extract_fact_check_claims",
        MAX_CLAIMS=policy.max_claims_per_unit,
        KNOWLEDGE_UNIT=json.dumps(_dump(unit), ensure_ascii=False, indent=2),
        TARGETS=json.dumps(targets, ensure_ascii=False, indent=2),
    )
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=FactCheckClaimsDraft,
            max_output_tokens=_output_limit(provider, 2200),
            metadata={
                "agent": "extract_fact_check_claims",
                "prompt_version": EXTRACT_CLAIMS_PROMPT_VERSION,
                "run_id": content.run_id,
                "knowledge_unit_id": unit.artifact_id,
                "round": round_number,
            },
        ))
        draft = FactCheckClaimsDraft.model_validate(_dump(response.value))
    except ProviderError as exc:
        return [], _failure("claim_extraction_error", exc)
    except Exception as exc:
        return [], _failure("claim_extraction_error", exc)

    claims: list[FactCheckClaim] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(draft.claims, start=1):
        if len(claims) >= policy.max_claims_per_unit:
            break
        target = by_id.get(item.target_id)
        text = item.text.strip()
        query = item.query.strip()
        if target is None or not text or not query or (item.target_id, text) in seen:
            continue
        seen.add((item.target_id, text))
        claims.append(FactCheckClaim(
            claim_id=f"fact-claim-{content.artifact_id}-{round_number}-{index}",
            artifact_id=target["artifact_id"],
            knowledge_unit_id=unit.artifact_id,
            target_id=item.target_id,
            role=target["role"],
            kind=item.kind,
            text=(f"题干：{target['question_context']}\n待核查陈述：{text[:1600]}"
                  if target.get("question_context") else text[:1600]),
            query=query[:500],
            source_refs=list(target["source_refs"]),
        ))
    return claims, None


def _assess_claims(
    provider: ModelProvider,
    *,
    claims: list[FactCheckClaim],
    evidence_by_claim: dict[str, tuple[list[FactCheckEvidence], list[FactCheckEvidence], list[str]]],
    content_artifact_id: str,
    quiz_artifact_id: str | None,
    round_number: int,
    run_id: str = "",
) -> tuple[list[FactCheckAssessment], str | None]:
    if not claims:
        return [], None

    # Bound the structured response and avoid repeating a large ContextPack
    # once for every assertion in a unit.
    if len(claims) > 5:
        assessments: list[FactCheckAssessment] = []
        errors: list[str] = []
        for offset in range(0, len(claims), 5):
            checked, error = _assess_claims(
                provider, claims=claims[offset:offset + 5], evidence_by_claim=evidence_by_claim,
                content_artifact_id=content_artifact_id, quiz_artifact_id=quiz_artifact_id,
                round_number=round_number, run_id=run_id,
            )
            assessments.extend(checked)
            if error:
                errors.append(error)
        return assessments, ";".join(_unique(errors)) or None

    def evidence_table(kind: int) -> str:
        records = {
            item.evidence_id: _dump(item)
            for claim in claims for item in evidence_by_claim[claim.claim_id][kind]
        }
        return json.dumps({
            "records": list(records.values()),
            "claims": [{"claim_id": claim.claim_id, "evidence_ids": [
                item.evidence_id for item in evidence_by_claim[claim.claim_id][kind]
            ]} for claim in claims],
        }, ensure_ascii=False, indent=2)

    prompt = load_prompt(
        "assess_fact_check_claims",
        CLAIMS=json.dumps([
            {"claim_id": claim.claim_id, "target_id": claim.target_id, "role": claim.role, "text": claim.text, "query": claim.query}
            for claim in claims
        ], ensure_ascii=False, indent=2),
        TEXTBOOK_EVIDENCE=evidence_table(0),
        EXTERNAL_EVIDENCE=evidence_table(1),
    )
    drafts: dict[str, FactCheckAssessmentDraft] = {}
    error: str | None = None
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=FactCheckAssessmentsDraft,
            max_output_tokens=_output_limit(provider, 3200),
            metadata={"agent": "assess_fact_check_claims", "prompt_version": FACT_CHECK_ASSESS_PROMPT_VERSION, "round": round_number, "run_id": run_id,
                      "claim_count": len(claims)},
        ))
        output = FactCheckAssessmentsDraft.model_validate(_dump(response.value))
        valid_ids = {claim.claim_id for claim in claims}
        drafts = {item.claim_id: item for item in output.assessments if item.claim_id in valid_ids}
    except ProviderError as exc:
        error = _failure("assessment_error", exc)
    except Exception as exc:
        error = _failure("assessment_error", exc)

    assessments: list[FactCheckAssessment] = []
    for claim in claims:
        textbook, external, reasons = evidence_by_claim[claim.claim_id]
        item = drafts.get(claim.claim_id)
        if item is None:
            if error is None:
                error = _failure("assessment_error", ValueError(f"assessment_missing:{claim.claim_id}"))
            assessments.append(FactCheckAssessment(
                assessment_id=f"assessment-{claim.claim_id}",
                claim_id=claim.claim_id,
                verdict="error",
                judgement="事实核查模型未返回该陈述的判断。",
                route="annotate",
                textbook_evidence=textbook,
                external_evidence=external,
                stop_reason=";".join([*reasons, error or "assessment_missing"]),
            ))
            continue

        verdict = item.verdict
        if verdict == "contradicted" and not (item.sufficient_textbook_evidence and textbook):
            verdict = "insufficient"
        elif verdict == "supported" and not textbook:
            verdict = "insufficient"
        elif verdict == "external_conflict" and not external:
            verdict = "insufficient"

        direct_contradiction = verdict == "contradicted" and bool(textbook) and item.sufficient_textbook_evidence
        route = "accept" if verdict == "supported" else "annotate"
        if direct_contradiction and claim.artifact_id == content_artifact_id:
            route = "revise_content"
        elif direct_contradiction and claim.artifact_id == quiz_artifact_id:
            route = "regenerate_quiz"
        assessments.append(FactCheckAssessment(
            assessment_id=f"assessment-{claim.claim_id}",
            claim_id=claim.claim_id,
            verdict=verdict,
            judgement=item.judgement.strip() or "未给出判断说明。",
            route=route,
            textbook_evidence=textbook,
            external_evidence=external,
            stop_reason=";".join(reasons) or None,
        ))
    return assessments, error


def _issue_for_assessment(task: ContentTask, claim: FactCheckClaim, assessment: FactCheckAssessment) -> ReviewIssue | None:
    refs = [item.source_ref for item in assessment.textbook_evidence if item.source_ref]
    if assessment.verdict == "supported":
        return None
    if assessment.verdict == "error":
        return _issue(
            task=task, suffix=f"{claim.claim_id}-error", category="uncertainty", severity="warning", layer="fact",
            message="事实核查未完成：模型或检索调用失败，尚不能判断该陈述。", target_id=claim.target_id,
            suggested_action="检查核查 trace 中的故障原因后重试。",
        )
    if assessment.verdict == "stance":
        return _issue(
            task=task, suffix=f"{claim.claim_id}-stance", category="stance", severity="info", layer="stance",
            message="该表述属于解释性或评价性观点，保留为中性提示。", target_id=claim.target_id, source_refs=refs,
        )
    if assessment.verdict == "external_conflict":
        return _issue(
            task=task, suffix=f"{claim.claim_id}-external", category="conflict", severity="warning", layer="stance",
            message="外部资料与当前教材存在差异，未自动改写教材主线。", target_id=claim.target_id,
            source_refs=refs, suggested_action="以当前教材为主，必要时人工复核。",
        )
    if assessment.verdict == "contradicted":
        return _issue(
            task=task, suffix=f"{claim.claim_id}-contradiction", category="fact", severity="warning", layer="fact",
            message="当前教材存在可定位的直接矛盾，但未能完成自动纠正。", target_id=claim.target_id,
            source_refs=refs, suggested_action="依据记录的教材证据人工修订。",
        )
    return _issue(
        task=task, suffix=f"{claim.claim_id}-uncertain", category="uncertainty", severity="warning", layer="fact",
        message="当前教材证据不足以确认该陈述，已保留为中性核实提示。", target_id=claim.target_id,
        source_refs=refs, suggested_action="补充教材依据或人工复核。",
    )


def _assessment_issues(task: ContentTask, claims: list[FactCheckClaim], assessments: list[FactCheckAssessment]) -> list[ReviewIssue]:
    by_id = {claim.claim_id: claim for claim in claims}
    return [
        issue
        for assessment in assessments
        for claim in [by_id.get(assessment.claim_id)]
        if claim is not None
        for issue in [_issue_for_assessment(task, claim, assessment)]
        if issue is not None
    ]


def _feedback(claims: list[FactCheckClaim], assessments: list[FactCheckAssessment], route: str) -> list[dict[str, Any]]:
    by_id = {claim.claim_id: claim for claim in claims}
    return [
        {
            "claim_id": claim.claim_id,
            "target_id": claim.target_id,
            "role": claim.role,
            "claim": claim.text,
            "judgement": assessment.judgement,
            "textbook_evidence": [_dump(item) for item in assessment.textbook_evidence],
        }
        for assessment in assessments
        if assessment.route == route
        for claim in [by_id.get(assessment.claim_id)]
        if claim is not None
    ]


def _correct_content(
    provider: ModelProvider,
    *,
    task: ContentTask,
    unit: KnowledgeUnit,
    context_pack: ContextPack,
    content: ContentArtifact,
    feedback: list[dict[str, Any]],
    valid_source_refs: set[str],
) -> tuple[ContentArtifact | None, str | None]:
    prompt = load_prompt(
        "revise_content_for_fact_check",
        KNOWLEDGE_UNIT_CONTEXT=json.dumps(_dump(unit), ensure_ascii=False, indent=2),
        CONTEXT_PACK=render_context_pack(context_pack),
        CANDIDATE_ARTIFACT=json.dumps(_dump(content), ensure_ascii=False, indent=2),
        FACT_CHECK_FEEDBACK=json.dumps(feedback, ensure_ascii=False, indent=2),
        CONTENT_TASK=_content_prompt_task(task),
    )
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=ContentDraft,
            max_output_tokens=_output_limit(provider, 2400),
            metadata={"agent": "revise_content_for_fact_check", "prompt_version": "revise_content_for_fact_check:v5:optional-subject", "task_id": task.task_id, "revision_of": content.artifact_id},
        ))
        draft = ContentDraft.model_validate(_dump(response.value))
    except ProviderError as exc:
        return None, _failure("content_correction_error", exc)
    except Exception as exc:
        return None, _failure("content_correction_error", exc)

    candidate = _content_artifact_from_draft(
        draft=draft,
        task=task,
        unit=unit,
        pack=context_pack,
        run_id=content.run_id,
        provider_name=str(getattr(provider, "provider", "unknown")),
        attempt=content.version + 1,
        prompt_version="revise_content_for_fact_check:v5:optional-subject",
    )
    check = _content_hard_check(
        task=task,
        unit=unit,
        context_pack=context_pack,
        valid_source_refs=valid_source_refs,
        candidate_artifact=candidate,
    )
    candidate.issues = list(check.issues)
    candidate.status = "accepted" if check.status == "accepted" else "needs_revision"
    return candidate, None if candidate.status == "accepted" else "content_correction_structure_check_failed"


def _correct_quiz(
    provider: ModelProvider,
    *,
    task: ContentTask,
    unit: KnowledgeUnit,
    context_pack: ContextPack,
    quiz: QuizArtifact,
    feedback: list[dict[str, Any]],
    valid_source_refs: set[str],
    correction_number: int,
    accepted_content: ContentArtifact | None = None,
) -> tuple[QuizArtifact | None, str | None]:
    prompt = load_prompt(
        "regenerate_quiz_for_fact_check",
        KNOWLEDGE_UNIT_CONTEXT=json.dumps(_dump(unit), ensure_ascii=False, indent=2),
        CONTEXT_PACK=render_context_pack(context_pack),
        QUIZ_ARTIFACT=json.dumps(_dump(quiz), ensure_ascii=False, indent=2),
        FACT_CHECK_FEEDBACK=json.dumps(feedback, ensure_ascii=False, indent=2),
        CONTENT_TASK=_content_prompt_task(task),
        COURSE_ARCHITECT_DECISION=json.dumps({"quiz_count": task.quiz_count, "acceptance_criteria": task.acceptance_criteria}),
        ACCEPTED_CONTENT=accepted_content_context(accepted_content),
    )
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=QuizDraft,
            max_output_tokens=_output_limit(provider, 2400),
            metadata={"agent": "regenerate_quiz_for_fact_check", "prompt_version": REGENERATE_QUIZ_PROMPT_VERSION, "task_id": task.task_id, "revision_of": quiz.artifact_id},
        ))
        draft = QuizDraft.model_validate(_dump(response.value))
    except ProviderError as exc:
        stage = "quiz_correction_retryable_error" if exc.retryable or exc.category == "schema" else "quiz_correction_error"
        return None, _failure(stage, exc)
    except Exception as exc:
        return None, _failure("quiz_correction_error", exc)

    candidate = QuizArtifact(
        artifact_id=f"quiz-{task.task_id}-fact-{correction_number}",
        run_id=quiz.run_id,
        version=quiz.version + 1,
        status="draft",
        source_refs=_unique([*context_pack.source_refs, *context_pack.selected_source_refs]),
        created_by=f"provider:{getattr(provider, 'provider', 'unknown')}",
        knowledge_unit_ids=[unit.artifact_id],
        target_objectives=_unique([*draft.target_objectives, *(value for question in draft.questions for value in question.target_objectives)]),
        questions=list(draft.questions),
        omitted_questions=list(draft.omitted_questions),
        task_id=task.task_id,
        context_pack_id=context_pack.context_pack_id,
        prompt_version=REGENERATE_QUIZ_PROMPT_VERSION,
        generation_metadata={"agent": "regenerate_quiz_for_fact_check", "revision_of": quiz.artifact_id, "prompt": prompt},
        raw_response=str(getattr(response, "raw_text", "") or draft.model_dump_json()),
    )
    check = validate_quiz_artifact(candidate, unit=unit, context_pack=context_pack, valid_source_refs=valid_source_refs)
    candidate.issues = [*quiz_draft_issues(draft, task, unit, candidate.artifact_id), *check.issues]
    candidate.status = "needs_revision" if any(issue.severity == "blocking" for issue in candidate.issues) else "accepted"
    return candidate, None if candidate.status == "accepted" else "quiz_correction_structure_check_failed"


def _search_textbook(
    skill: UnifiedRetrievalTool,
    claim: FactCheckClaim,
    valid_source_refs: set[str],
    limit: int,
    mode: str = "hybrid",
    calls: list[RetrievalCall] | None = None,
) -> tuple[list[FactCheckEvidence], str]:
    try:
        result = skill.search(claim.query, allowed_source_refs=valid_source_refs, mode=mode, limit=limit)
        if calls is not None:
            calls.append(RetrievalCall(
                call_id=f"fact-{claim.claim_id}-{len(calls) + 1}", tool_name=result.skill_name,
                tool_version=result.skill_version, query=claim.query, limit=limit,
                status=result.status.value, error_code=result.error_code.value if result.error_code else None,
                metadata={**result.metadata, "claim_id": claim.claim_id},
                returned_excerpts=[ContextExcerpt(source_ref=e.source_ref, source_refs=list(e.source_refs or [e.source_ref]),
                    page_number=e.page_number or 1, block_index=e.block_index or 0, text=e.text)
                    for e in result.evidence if e.source_ref],
            ))
        return [_evidence(item) for item in result.evidence], _search_reason("textbook", result)
    except Exception as exc:
        return [], _failure("textbook:error", exc)


def _search_web(skill: WebResourceSearchSkill, claim: FactCheckClaim) -> tuple[list[FactCheckEvidence], str]:
    try:
        result = skill.search(claim.query, limit=3)
        return [_evidence(item) for item in result.evidence], _search_reason("web", result)
    except Exception as exc:
        return [], _failure("web:error", exc)


def _context_pack_evidence(context_pack: ContextPack) -> tuple[list[FactCheckEvidence], str]:
    """Use evidence already recorded by content generation first."""
    allowed = set(context_pack.source_refs)
    excerpts = [excerpt for excerpt in context_pack.excerpts if excerpt.source_ref in allowed and excerpt.text.strip()]
    evidence = [
        FactCheckEvidence(
            evidence_id=hashlib.sha256(f"context:{excerpt.source_ref}:{excerpt.text}".encode()).hexdigest()[:24],
            source_kind="textbook",
            provider="context-pack",
            locator=f"page:{excerpt.page_number};block:{excerpt.block_index}",
            text=excerpt.text,
            source_ref=excerpt.source_ref,
            source_refs=list(excerpt.source_refs or [excerpt.source_ref]),
        )
        for excerpt in excerpts
    ]
    return evidence, "context_pack:used" if evidence else "context_pack:empty"


def _fact_check_retrieval_decision(
    provider: ModelProvider,
    *,
    claim: FactCheckClaim,
    context_pack: ContextPack,
    web_enabled: bool,
    available_modes: list[str] | None = None,
) -> FactCheckRetrievalDecision:
    """Let the fact-check agent choose whether and where to retrieve evidence."""

    prompt = load_prompt(
        "fact_check_retrieval",
        RETRIEVAL_CAPABILITIES=json.dumps(available_modes or ["keyword", "hybrid"]),
        CLAIM=json.dumps(_dump(claim), ensure_ascii=False, indent=2),
        CONTEXT=json.dumps([_dump(item) for item in _context_pack_evidence(context_pack)[0]], ensure_ascii=False),
    )
    response = provider.generate_structured(StructuredGenerationRequest(
        prompt=prompt,
        schema=FactCheckRetrievalDecision,
        max_output_tokens=_output_limit(provider, 700),
        metadata={"agent": "fact_check_retrieval", "prompt_version": FACT_CHECK_RETRIEVAL_PROMPT_VERSION, "claim_id": claim.claim_id},
    ))
    decision = FactCheckRetrievalDecision.model_validate(response.value.model_dump(mode="python"))
    if not web_enabled and decision.action in {"search_web", "search_both"}:
        decision = decision.model_copy(update={
            "action": "search_textbook",
            "web_queries": [],
            "textbook_queries": decision.textbook_queries or [claim.query],
        })
    return decision


def _check_unit(
    provider: ModelProvider,
    *,
    task: ContentTask,
    unit: KnowledgeUnit,
    context_pack: ContextPack,
    content: ContentArtifact,
    quiz: QuizArtifact | None,
    policy: FactCheckPolicy,
    valid_source_refs: set[str],
    textbook_skill: UnifiedRetrievalTool,
    web_skill: WebResourceSearchSkill,
    additional_retrieval_enabled: bool,
) -> tuple[ContentArtifact, QuizArtifact | None, FactCheckUnitTrace, list[ReviewIssue], bool]:
    current_content = content
    current_quiz = quiz
    rounds: list[FactCheckRoundTrace] = []
    final_issues: list[ReviewIssue] = []
    retrieval_calls: list[RetrievalCall] = []
    corrections = 0
    quiz_removed = False
    web_queries_used = 0

    def search_web(claim: FactCheckClaim) -> tuple[list[FactCheckEvidence], str]:
        nonlocal web_queries_used
        if web_queries_used >= policy.web_query_limit:
            return [], "web:budget_exhausted"
        # Failed requests consume the same per-unit budget as successful ones.
        web_queries_used += 1
        return _search_web(web_skill, claim)

    for round_number in range(policy.max_corrections_per_unit + 1):
        claims, extraction_error = _extract_claims(
            provider, unit=unit, content=current_content, quiz=current_quiz, policy=policy, round_number=round_number,
        )
        if extraction_error:
            rounds.append(FactCheckRoundTrace(
                round=round_number, content_artifact_id=current_content.artifact_id,
                quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
                route="annotate", stop_reason=extraction_error,
            ))
            final_issues.append(_issue(
                task=task, suffix=f"extract-{round_number}", category="uncertainty", severity="warning", layer="fact",
                message="事实核查未能抽取陈述，已保留为中性核实提示。", target_id=current_content.artifact_id,
            ))
            break

        claim_modes: dict[str, str] = {}
        evidence: dict[str, tuple[list[FactCheckEvidence], list[FactCheckEvidence], list[str]]] = {}
        factual_claims: list[FactCheckClaim] = []
        assessments: list[FactCheckAssessment] = []
        for claim in claims:
            if claim.kind == "stance":
                assessments.append(FactCheckAssessment(
                    assessment_id=f"assessment-{claim.claim_id}", claim_id=claim.claim_id, verdict="stance",
                    judgement="该陈述属于观点，不作为教材事实纠正。", route="annotate", stop_reason="stance",
                ))
                continue
            textbook, context_reason = _context_pack_evidence(context_pack)
            decision = FactCheckRetrievalDecision(action="use_context")
            reasons = [context_reason]
            if additional_retrieval_enabled:
                try:
                    decision = _fact_check_retrieval_decision(
                        provider, claim=claim, context_pack=context_pack,
                        web_enabled=policy.web_enabled,
                        available_modes=getattr(textbook_skill, "available_modes", None),
                    )
                except Exception as exc:
                    # A failed planning call must not discard usable context.
                    reasons.append(_failure("retrieval_decision_error", exc))
                    decision = FactCheckRetrievalDecision(action="use_context")
            claim_modes[claim.claim_id] = decision.textbook_mode
            if decision.action in {"search_textbook", "search_both"}:
                for query in _unique(decision.textbook_queries):
                    query_claim = claim.model_copy(update={"query": query})
                    extra, reason = _search_textbook(textbook_skill, query_claim, valid_source_refs, policy.textbook_result_limit, decision.textbook_mode, retrieval_calls)
                    textbook = _merge_evidence(textbook, extra)
                    reasons.append(reason)
            external: list[FactCheckEvidence] = []
            if decision.action in {"search_web", "search_both"} and policy.web_enabled:
                for query in _unique(decision.web_queries):
                    query_claim = claim.model_copy(update={"query": query})
                    extra, reason = search_web(query_claim)
                    external = _merge_evidence(external, extra)
                    reasons.append(reason)
                    if reason == "web:budget_exhausted":
                        break
            evidence[claim.claim_id] = (textbook, external, reasons)
            factual_claims.append(claim)

        checked, assessment_error = _assess_claims(
            provider,
            claims=factual_claims,
            evidence_by_claim=evidence,
            content_artifact_id=current_content.artifact_id,
            quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
            round_number=round_number,
            run_id=content.run_id,
        )
        assessments.extend(checked)

        # Existing excerpts can be unrelated headings. Supplement assertions
        # judged insufficient even when the snapshot was nonempty.
        # An insufficient assessment may trigger bounded supplemental retrieval
        # when the caller explicitly enables it.
        if additional_retrieval_enabled and not assessment_error:
            insufficient_ids = {item.claim_id for item in checked if item.verdict == "insufficient"}
            follow_up = []
            for claim in factual_claims:
                if claim.claim_id not in insufficient_ids or "context_pack:used" not in evidence[claim.claim_id][2]:
                    continue
                extra, reason = _search_textbook(textbook_skill, claim, valid_source_refs, policy.textbook_result_limit, claim_modes[claim.claim_id], retrieval_calls)
                textbook, external, reasons = evidence[claim.claim_id]
                existing_excerpts = {(item.source_ref, item.text) for item in textbook}
                extra = [item for item in extra if (item.source_ref, item.text) not in existing_excerpts]
                evidence[claim.claim_id] = (textbook + extra, external, [*reasons, reason])
                if extra:
                    follow_up.append(claim)
            if follow_up:
                reassessed, reassessment_error = _assess_claims(
                    provider, claims=follow_up, evidence_by_claim=evidence,
                    content_artifact_id=current_content.artifact_id,
                    quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
                    round_number=round_number, run_id=content.run_id,
                )
                replacement = {item.claim_id: item for item in reassessed if item.verdict != "error"}
                if reassessment_error:
                    for item in assessments:
                        if item.claim_id in {claim.claim_id for claim in follow_up}:
                            item.stop_reason = ";".join(filter(None, [item.stop_reason, reassessment_error]))
                assessments = [replacement.get(item.claim_id, item) for item in assessments]
                checked = [replacement.get(item.claim_id, item) for item in checked]

        if policy.web_enabled and additional_retrieval_enabled and not assessment_error:
            follow_up = [
                claim for claim in factual_claims
                if next((item for item in checked if item.claim_id == claim.claim_id), None)
                and next(item for item in checked if item.claim_id == claim.claim_id).verdict == "insufficient"
            ]
            searched = []
            for claim in follow_up:
                textbook, external, reasons = evidence[claim.claim_id]
                extra, reason = search_web(claim)
                evidence[claim.claim_id] = (textbook, _merge_evidence(external, extra), [*reasons, reason])
                if reason == "web:budget_exhausted":
                    for item in assessments:
                        if item.claim_id == claim.claim_id:
                            item.stop_reason = ";".join(filter(None, [item.stop_reason, reason]))
                else:
                    searched.append(claim)
            follow_up = searched
            if follow_up:
                reassessed, reassessment_error = _assess_claims(
                    provider,
                    claims=follow_up,
                    evidence_by_claim=evidence,
                    content_artifact_id=current_content.artifact_id,
                    quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
                    round_number=round_number,
                    run_id=content.run_id,
                )
                if reassessment_error is None:
                    replacement = {item.claim_id: item for item in reassessed}
                    assessments = [replacement.get(item.claim_id, item) for item in assessments]
                else:
                    for item in assessments:
                        if item.claim_id in {claim.claim_id for claim in follow_up}:
                            item.stop_reason = ";".join(filter(None, [item.stop_reason, reassessment_error]))

        content_conflicts = [item for item in assessments if item.route == "revise_content"]
        quiz_conflicts = [item for item in assessments if item.route == "regenerate_quiz"]
        if content_conflicts:
            if corrections < policy.max_corrections_per_unit:
                candidate, correction_error = _correct_content(
                    provider, task=task, unit=unit, context_pack=context_pack, content=current_content,
                    feedback=_feedback(claims, assessments, "revise_content"), valid_source_refs=valid_source_refs,
                )
                corrections += 1
                rounds.append(FactCheckRoundTrace(
                    round=round_number, content_artifact_id=current_content.artifact_id,
                    quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
                    claims=claims, assessments=assessments, route="revise_content",
                    correction_artifact_id=candidate.artifact_id if candidate else None,
                    stop_reason=correction_error or "content_corrected",
                ))
                if candidate is not None and candidate.status == "accepted":
                    current_content = candidate
                    continue
            else:
                rounds.append(FactCheckRoundTrace(
                    round=round_number, content_artifact_id=current_content.artifact_id,
                    quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
                    claims=claims, assessments=assessments, route="annotate", stop_reason="max_corrections_exhausted",
                ))
            final_issues.extend(_assessment_issues(task, claims, assessments))
            break

        if quiz_conflicts and current_quiz is not None:
            candidate = None
            while corrections < policy.max_corrections_per_unit:
                candidate, correction_error = _correct_quiz(
                    provider, task=task, unit=unit, context_pack=context_pack, quiz=current_quiz,
                    feedback=_feedback(claims, assessments, "regenerate_quiz"), valid_source_refs=valid_source_refs,
                    correction_number=corrections + 1,
                    accepted_content=current_content,
                )
                corrections += 1
                rounds.append(FactCheckRoundTrace(
                    round=round_number, content_artifact_id=current_content.artifact_id,
                    quiz_artifact_id=current_quiz.artifact_id, claims=claims, assessments=assessments,
                    route="regenerate_quiz", correction_artifact_id=candidate.artifact_id if candidate else None,
                    stop_reason=correction_error or "quiz_corrected",
                ))
                if candidate is not None and candidate.status == "accepted":
                    current_quiz = candidate
                    break
                if not (correction_error or "").startswith("quiz_correction_retryable_error:"):
                    break
            if candidate is not None and candidate.status == "accepted":
                continue
            conflict_claim_ids = {item.claim_id for item in quiz_conflicts}
            conflict_targets = {claim.target_id for claim in claims if claim.claim_id in conflict_claim_ids}
            remaining = [question for question in current_quiz.questions if not any(
                target == question.question_id or target in {
                    f"{question.question_id}:{role}" for role in ("quiz_question", "quiz_answer", "quiz_explanation")
                }
                for target in conflict_targets
            )]
            old_quiz = current_quiz
            current_quiz = old_quiz.model_copy(deep=True, update={
                "artifact_id": f"quiz-{task.task_id}-fact-filtered-{corrections}",
                "version": old_quiz.version + 1, "questions": remaining,
                "generation_metadata": {**old_quiz.generation_metadata, "revision_of": old_quiz.artifact_id,
                                        "excluded_question_ids": [q.question_id for q in old_quiz.questions if q not in remaining]},
            }) if remaining else None
            quiz_removed = current_quiz is None
            rounds.append(FactCheckRoundTrace(
                round=round_number, content_artifact_id=current_content.artifact_id,
                quiz_artifact_id=old_quiz.artifact_id, claims=claims, assessments=assessments,
                route="exclude_quiz", stop_reason="quiz_correction_failed_conflicting_questions_excluded",
                correction_artifact_id=current_quiz.artifact_id if current_quiz else None,
            ))
            final_issues.append(_issue(
                task=task, suffix="quiz-count-after-exclusion", category="coverage", severity="warning", layer="fact",
                message=f"测验修正未完成，已剔除确认存在矛盾的题目；保留 {len(remaining)} 题，课程架构师指定 {task.quiz_count} 题。",
                target_id=current_quiz.artifact_id if current_quiz else old_quiz.artifact_id,
            ))
            final_issues.extend(_assessment_issues(task, claims, assessments))
            break

        route = "annotate" if any(item.verdict != "supported" for item in assessments) else "accept"
        rounds.append(FactCheckRoundTrace(
            round=round_number, content_artifact_id=current_content.artifact_id,
            quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
            claims=claims, assessments=assessments, route=route,
            stop_reason=assessment_error or ("annotated" if route == "annotate" else "accepted"),
        ))
        final_issues.extend(_assessment_issues(task, claims, assessments))
        break

    final_issues = _dedupe_issues(final_issues)
    trace = FactCheckUnitTrace(
        trace_id=f"fact-check-trace-{uuid.uuid4().hex[:12]}",
        run_id=content.run_id,
        task_id=task.task_id,
        knowledge_unit_id=unit.artifact_id,
        max_corrections=policy.max_corrections_per_unit,
        rounds=rounds,
        retrieval_calls=retrieval_calls,
        final_status="at_risk" if any(issue.severity == "warning" for issue in final_issues) else "accepted",
        corrections_used=corrections,
        stop_reason=rounds[-1].stop_reason or "accepted",
        final_content_artifact_id=current_content.artifact_id,
        final_quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
    )
    return current_content, current_quiz, trace, final_issues, quiz_removed


def run_fact_check_loop(
    provider: ModelProvider,
    *,
    run_id: str,
    blueprint: LearningBlueprint,
    tasks: list[ContentTask],
    context_packs: list[ContextPack],
    content_artifacts: list[ContentArtifact],
    quiz_artifacts: list[QuizArtifact],
    source_blocks: list[SourceBlock],
    valid_source_refs: Iterable[str],
    policy: FactCheckPolicy,
    textbook_search_skill: TextbookDatabaseSearchSkill | None = None,
    retrieval_service: UnifiedRetrievalTool | None = None,
    web_search_skill: WebResourceSearchSkill | None = None,
    # Direct callers retain the historical fallback behavior; the runtime
    # reads FACT_CHECK_ADDITIONAL_RETRIEVAL_ENABLED and passes the explicit
    # production policy.
    additional_retrieval_enabled: bool = True,
    max_workers: int = 1,
) -> dict[str, Any]:
    """Check accepted artifacts against the current textbook and return replacements."""

    valid_refs = set(_unique(valid_source_refs))
    resources = None
    setup_error: ReviewIssue | None = None
    if retrieval_service is not None:
        textbook_search_skill = retrieval_service
    elif textbook_search_skill is not None:
        textbook_search_skill = UnifiedRetrievalService(LockedSearch(textbook_search_skill), blocks=source_blocks)
    else:
        try:
            from annotation.workflow.context import RunResources
            resources = RunResources()
            textbook_search_skill = resources.retrieval(build_semantic_blocks(source_blocks))
        except Exception as exc:
            detail = _failure("textbook_index_error", exc)
            setup_error = ReviewIssue(
                issue_id=f"fact-check-{run_id}-textbook-index",
                category="uncertainty",
                severity="warning",
                layer="fact",
                message=f"教材检索索引不可用，未执行自动事实纠正。{detail}",
            )

    units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
    packs = {pack.task_id: pack for pack in context_packs}
    final_content = list(content_artifacts)
    final_quiz = list(quiz_artifacts)
    content_index = {artifact.task_id: index for index, artifact in enumerate(final_content) if artifact.task_id}
    quiz_index = {artifact.task_id: index for index, artifact in enumerate(final_quiz) if artifact.task_id}
    removed_quiz_tasks: set[str] = set()
    traces: list[FactCheckUnitTrace] = []
    issues: list[ReviewIssue] = [setup_error] if setup_error else []
    web_skill = web_search_skill or DisabledWebResourceSearchSkill()

    def check_task(task):
        position = content_index.get(task.task_id)
        unit = units.get(task.knowledge_unit_id)
        pack = packs.get(task.task_id)
        if position is None or unit is None or pack is None:
            return None
        content = final_content[position]
        if content.status != "accepted":
            return None
        quiz_position = quiz_index.get(task.task_id)
        quiz = final_quiz[quiz_position] if quiz_position is not None and final_quiz[quiz_position].status == "accepted" else None
        if textbook_search_skill is None and not pack.excerpts:
            trace = FactCheckUnitTrace(
                trace_id=f"fact-check-trace-{uuid.uuid4().hex[:12]}", run_id=run_id, task_id=task.task_id,
                knowledge_unit_id=unit.artifact_id, max_corrections=policy.max_corrections_per_unit,
                rounds=[FactCheckRoundTrace(
                    round=0, content_artifact_id=content.artifact_id,
                    quiz_artifact_id=quiz.artifact_id if quiz else None, route="annotate", stop_reason=setup_error.message if setup_error else "textbook_index_error",
                )],
                final_status="at_risk", corrections_used=0, stop_reason=setup_error.message if setup_error else "textbook_index_error",
                final_content_artifact_id=content.artifact_id, final_quiz_artifact_id=quiz.artifact_id if quiz else None,
            )
            return position, quiz_position, content, quiz, trace, [], False
        checked_content, checked_quiz, trace, unit_issues, quiz_removed = _check_unit(
            provider,
            task=task,
            unit=unit,
            context_pack=pack,
            content=content,
            quiz=quiz,
            policy=policy,
            valid_source_refs=valid_refs,
            textbook_skill=textbook_search_skill,
            web_skill=web_skill,
            additional_retrieval_enabled=additional_retrieval_enabled,
        )
        return position, quiz_position, checked_content, checked_quiz, trace, unit_issues, quiz_removed

    try:
        results = ordered_batches(check_task, tasks, workers=max_workers)
        for task, result in zip(tasks, results):
            if result is None:
                continue
            position, quiz_position, checked_content, checked_quiz, trace, unit_issues, quiz_removed = result
            final_content[position] = checked_content
            if quiz_position is not None and checked_quiz is not None:
                final_quiz[quiz_position] = checked_quiz
            if quiz_removed:
                removed_quiz_tasks.add(task.task_id)
            traces.append(trace)
            issues.extend(unit_issues)
    finally:
        if resources is not None:
            resources.close()

    final_quiz = [artifact for artifact in final_quiz if artifact.task_id not in removed_quiz_tasks]
    issues = _dedupe_issues(issues)
    final_status = "at_risk" if any(trace.final_status == "at_risk" for trace in traces) or any(
        issue.severity == "warning" for issue in issues
    ) else "accepted"
    summary = {
        "unit_count": len(traces),
        "accepted_count": sum(trace.final_status == "accepted" for trace in traces),
        "at_risk_count": sum(trace.final_status == "at_risk" for trace in traces),
        "claim_count": sum(len(round_trace.claims) for trace in traces for round_trace in trace.rounds),
        "correction_count": sum(trace.corrections_used for trace in traces),
        "excluded_quiz_count": len(removed_quiz_tasks),
        "web_enabled": policy.web_enabled,
        "final_status": final_status,
    }
    artifact = FactCheckArtifact(
        artifact_id=f"fact-check-{run_id}",
        run_id=run_id,
        version=1,
        status=final_status,
        created_by=f"provider:{getattr(provider, 'provider', 'unknown')}",
        policy=policy,
        unit_traces=traces,
        issues=issues,
        summary=summary,
    )
    return {
        "content_artifacts": final_content,
        "quiz_artifacts": final_quiz,
        "fact_check_artifact": artifact,
        "fact_check_issues": issues,
        "fact_check_summary": summary,
        "fact_check_status": artifact.status,
    }


__all__ = ["run_fact_check_loop"]
