"""A-003: textbook-backed fact checking after content and quiz generation."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from typing import Any

from annotation.domain.artifacts import (
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
    SQLiteFts5TextbookDatabaseSearchSkill,
    TextbookDatabaseSearchSkill,
    WebResourceSearchSkill,
)
from annotation.prompt_loader import load_prompt
from annotation.providers import ModelProvider, ProviderError, StructuredGenerationRequest
from annotation.retrieval import index_source_blocks
from annotation.workflow.content import render_context_pack
from annotation.workflow.content_support import _content_artifact_from_draft, _content_hard_check
from annotation.workflow.models import ContentDraft, FactCheckAssessmentDraft, FactCheckAssessmentsDraft, FactCheckClaimsDraft
from annotation.workflow.quiz import validate_quiz_artifact


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _output_limit(provider: ModelProvider, default: int) -> int:
    limit = getattr(getattr(provider, "capabilities", None), "max_output_tokens", None)
    return min(default, int(limit)) if limit else default


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


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
        url=record.url,
        rank=record.rank,
    )


def _search_reason(prefix: str, result: Any) -> str:
    status = getattr(getattr(result, "status", None), "value", getattr(result, "status", "error"))
    return f"{prefix}:{status}"


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
    if quiz is None:
        return targets
    for question in quiz.questions:
        refs = list(question.source_refs or quiz.source_refs)
        for role, text in (
            ("quiz_question", question.question),
            ("quiz_answer", question.answer),
            ("quiz_explanation", question.explanation),
        ):
            targets.append({
                "target_id": f"{question.question_id}:{role}",
                "artifact_id": quiz.artifact_id,
                "role": role,
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

    if str(getattr(provider, "provider", "unknown")) == "mock":
        return [], None

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
                "run_id": content.run_id,
                "knowledge_unit_id": unit.artifact_id,
                "round": round_number,
            },
        ))
        draft = FactCheckClaimsDraft.model_validate(_dump(response.value))
    except ProviderError as exc:
        return [], f"claim_extraction_error:{getattr(exc, 'category', 'provider')}"
    except Exception:
        return [], "claim_extraction_error"

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
            text=text[:1600],
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
) -> tuple[list[FactCheckAssessment], str | None]:
    if not claims:
        return [], None

    prompt = load_prompt(
        "assess_fact_check_claims",
        CLAIMS=json.dumps([
            {"claim_id": claim.claim_id, "target_id": claim.target_id, "role": claim.role, "text": claim.text, "query": claim.query}
            for claim in claims
        ], ensure_ascii=False, indent=2),
        TEXTBOOK_EVIDENCE=json.dumps([
            {"claim_id": claim.claim_id, "evidence": [_dump(item) for item in evidence_by_claim[claim.claim_id][0]]}
            for claim in claims
        ], ensure_ascii=False, indent=2),
        EXTERNAL_EVIDENCE=json.dumps([
            {"claim_id": claim.claim_id, "evidence": [_dump(item) for item in evidence_by_claim[claim.claim_id][1]]}
            for claim in claims
        ], ensure_ascii=False, indent=2),
    )
    drafts: dict[str, FactCheckAssessmentDraft] = {}
    error: str | None = None
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=FactCheckAssessmentsDraft,
            max_output_tokens=_output_limit(provider, 3200),
            metadata={"agent": "assess_fact_check_claims", "round": round_number},
        ))
        output = FactCheckAssessmentsDraft.model_validate(_dump(response.value))
        valid_ids = {claim.claim_id for claim in claims}
        drafts = {item.claim_id: item for item in output.assessments if item.claim_id in valid_ids}
    except ProviderError as exc:
        error = f"assessment_error:{getattr(exc, 'category', 'provider')}"
    except Exception:
        error = "assessment_error"

    assessments: list[FactCheckAssessment] = []
    for claim in claims:
        textbook, external, reasons = evidence_by_claim[claim.claim_id]
        item = drafts.get(claim.claim_id)
        if item is None:
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
            verdict = "external_conflict" if external else "insufficient"
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
    )
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=ContentDraft,
            max_output_tokens=_output_limit(provider, 2400),
            metadata={"agent": "revise_content_for_fact_check", "task_id": task.task_id, "revision_of": content.artifact_id},
        ))
        draft = ContentDraft.model_validate(_dump(response.value))
    except ProviderError as exc:
        return None, f"content_correction_error:{getattr(exc, 'category', 'provider')}"
    except Exception:
        return None, "content_correction_error"

    candidate = _content_artifact_from_draft(
        draft=draft,
        task=task,
        unit=unit,
        pack=context_pack,
        run_id=content.run_id,
        provider_name=str(getattr(provider, "provider", "unknown")),
        attempt=content.version + 1,
        prompt_version="revise_content_for_fact_check:v1",
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
) -> tuple[QuizArtifact | None, str | None]:
    prompt = load_prompt(
        "regenerate_quiz_for_fact_check",
        KNOWLEDGE_UNIT_CONTEXT=json.dumps(_dump(unit), ensure_ascii=False, indent=2),
        CONTEXT_PACK=render_context_pack(context_pack),
        QUIZ_ARTIFACT=json.dumps(_dump(quiz), ensure_ascii=False, indent=2),
        FACT_CHECK_FEEDBACK=json.dumps(feedback, ensure_ascii=False, indent=2),
    )
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=QuizDraft,
            max_output_tokens=_output_limit(provider, 2400),
            metadata={"agent": "regenerate_quiz_for_fact_check", "task_id": task.task_id, "revision_of": quiz.artifact_id},
        ))
        draft = QuizDraft.model_validate(_dump(response.value))
    except ProviderError as exc:
        return None, f"quiz_correction_error:{getattr(exc, 'category', 'provider')}"
    except Exception:
        return None, "quiz_correction_error"

    count = draft.question_count if draft.question_count is not None else len(draft.questions)
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
        question_count=count,
        task_id=task.task_id,
        context_pack_id=context_pack.context_pack_id,
        prompt_version="regenerate_quiz_for_fact_check:v1",
        generation_metadata={"agent": "regenerate_quiz_for_fact_check", "revision_of": quiz.artifact_id},
        raw_response=str(getattr(response, "raw_text", "") or draft.model_dump_json()),
    )
    check = validate_quiz_artifact(candidate, unit=unit, context_pack=context_pack, valid_source_refs=valid_source_refs)
    candidate.issues = list(check.issues)
    candidate.status = "accepted" if check.status == "accepted" else "needs_revision"
    return candidate, None if candidate.status == "accepted" else "quiz_correction_structure_check_failed"


def _search_textbook(
    skill: TextbookDatabaseSearchSkill,
    claim: FactCheckClaim,
    valid_source_refs: set[str],
    limit: int,
) -> tuple[list[FactCheckEvidence], str]:
    try:
        result = skill.search(claim.query, allowed_source_refs=valid_source_refs, limit=limit)
        return [_evidence(item) for item in result.evidence], _search_reason("textbook", result)
    except Exception:
        return [], "textbook:error"


def _search_web(skill: WebResourceSearchSkill, claim: FactCheckClaim) -> tuple[list[FactCheckEvidence], str]:
    try:
        result = skill.search(claim.query, limit=3)
        return [_evidence(item) for item in result.evidence], _search_reason("web", result)
    except Exception:
        return [], "web:error"


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
    textbook_skill: TextbookDatabaseSearchSkill,
    web_skill: WebResourceSearchSkill,
) -> tuple[ContentArtifact, QuizArtifact | None, FactCheckUnitTrace, list[ReviewIssue], bool]:
    current_content = content
    current_quiz = quiz
    rounds: list[FactCheckRoundTrace] = []
    final_issues: list[ReviewIssue] = []
    corrections = 0
    quiz_removed = False

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
            textbook, reason = _search_textbook(textbook_skill, claim, valid_source_refs, policy.textbook_result_limit)
            evidence[claim.claim_id] = (textbook, [], [reason])
            factual_claims.append(claim)

        checked, assessment_error = _assess_claims(
            provider,
            claims=factual_claims,
            evidence_by_claim=evidence,
            content_artifact_id=current_content.artifact_id,
            quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
            round_number=round_number,
        )
        assessments.extend(checked)

        if policy.web_enabled and not assessment_error:
            follow_up = [
                claim for claim in factual_claims
                if next((item for item in checked if item.claim_id == claim.claim_id), None)
                and next(item for item in checked if item.claim_id == claim.claim_id).verdict == "insufficient"
            ][:policy.web_query_limit]
            if follow_up:
                for claim in follow_up:
                    textbook, _external, reasons = evidence[claim.claim_id]
                    external, reason = _search_web(web_skill, claim)
                    evidence[claim.claim_id] = (textbook, external, [*reasons, reason])
                reassessed, reassessment_error = _assess_claims(
                    provider,
                    claims=follow_up,
                    evidence_by_claim=evidence,
                    content_artifact_id=current_content.artifact_id,
                    quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
                    round_number=round_number,
                )
                if reassessment_error is None:
                    replacement = {item.claim_id: item for item in reassessed}
                    assessments = [replacement.get(item.claim_id, item) for item in assessments]

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
            if corrections < policy.max_corrections_per_unit:
                candidate, correction_error = _correct_quiz(
                    provider, task=task, unit=unit, context_pack=context_pack, quiz=current_quiz,
                    feedback=_feedback(claims, assessments, "regenerate_quiz"), valid_source_refs=valid_source_refs,
                    correction_number=corrections + 1,
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
                    continue
            else:
                rounds.append(FactCheckRoundTrace(
                    round=round_number, content_artifact_id=current_content.artifact_id,
                    quiz_artifact_id=current_quiz.artifact_id, claims=claims, assessments=assessments,
                    route="exclude_quiz", stop_reason="max_corrections_exhausted",
                ))
            current_quiz = None
            quiz_removed = True
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
        final_status="at_risk" if any(issue.severity == "warning" for issue in final_issues) else "accepted",
        corrections_used=corrections,
        stop_reason=rounds[-1].stop_reason or "accepted",
        final_content_artifact_id=current_content.artifact_id,
        final_quiz_artifact_id=current_quiz.artifact_id if current_quiz else None,
    )
    return current_content, current_quiz, trace, final_issues, quiz_removed


def _transient_textbook_skill(source_blocks: list[SourceBlock]) -> tuple[TextbookDatabaseSearchSkill, sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    index_source_blocks(connection, source_blocks)
    return SQLiteFts5TextbookDatabaseSearchSkill(connection), connection


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
    web_search_skill: WebResourceSearchSkill | None = None,
) -> dict[str, Any]:
    """Check accepted artifacts against the current textbook and return replacements."""

    valid_refs = set(_unique(valid_source_refs))
    connection: sqlite3.Connection | None = None
    setup_error: ReviewIssue | None = None
    if textbook_search_skill is None:
        try:
            textbook_search_skill, connection = _transient_textbook_skill(source_blocks)
        except Exception:
            setup_error = ReviewIssue(
                issue_id=f"fact-check-{run_id}-textbook-index",
                category="uncertainty",
                severity="warning",
                layer="fact",
                message="教材检索索引不可用，未执行自动事实纠正。",
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

    try:
        for task in tasks:
            position = content_index.get(task.task_id)
            unit = units.get(task.knowledge_unit_id)
            pack = packs.get(task.task_id)
            if position is None or unit is None or pack is None:
                continue
            content = final_content[position]
            if content.status != "accepted":
                continue
            quiz_position = quiz_index.get(task.task_id)
            quiz = final_quiz[quiz_position] if quiz_position is not None and final_quiz[quiz_position].status == "accepted" else None
            if textbook_search_skill is None:
                traces.append(FactCheckUnitTrace(
                    trace_id=f"fact-check-trace-{uuid.uuid4().hex[:12]}", run_id=run_id, task_id=task.task_id,
                    knowledge_unit_id=unit.artifact_id, max_corrections=policy.max_corrections_per_unit,
                    rounds=[FactCheckRoundTrace(
                        round=0, content_artifact_id=content.artifact_id,
                        quiz_artifact_id=quiz.artifact_id if quiz else None, route="annotate", stop_reason="textbook_index_error",
                    )],
                    final_status="at_risk", corrections_used=0, stop_reason="textbook_index_error",
                    final_content_artifact_id=content.artifact_id, final_quiz_artifact_id=quiz.artifact_id if quiz else None,
                ))
                continue
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
            )
            final_content[position] = checked_content
            if quiz_position is not None and checked_quiz is not None:
                final_quiz[quiz_position] = checked_quiz
            if quiz_removed:
                removed_quiz_tasks.add(task.task_id)
            traces.append(trace)
            issues.extend(unit_issues)
    finally:
        if connection is not None:
            connection.close()

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
