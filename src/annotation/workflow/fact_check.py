"""A-003 post-generation fact checking and stance annotation.

This module deliberately runs after the independent content and quiz gates.
It keeps textbook evidence, optional external evidence, decisions, and bounded
correction attempts in a separate artifact rather than weakening either gate.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from annotation.domain.artifacts import (
    ContentArtifact,
    ContentTask,
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
from annotation.workflow.models import (
    ContentDraft,
    FactCheckAssessmentDraft,
    FactCheckAssessmentsDraft,
    FactCheckClaimsDraft,
)
from annotation.workflow.quiz import validate_quiz_artifact


_PROMPT_VERSION = "fact_check:v1"


def _model_dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _provider_metadata(response: Any, *, agent: str) -> dict[str, Any]:
    return {
        "agent": agent,
        "provider": getattr(response, "provider", "unknown"),
        "model": getattr(response, "model", "unknown"),
        "base_url": getattr(response, "base_url", None),
        "config_version": getattr(response, "config_version", "unknown"),
        "duration_ms": getattr(response, "duration_ms", 0),
        "usage": dict(getattr(response, "usage", {}) or {}),
    }


def _response_limit(provider: ModelProvider, default: int) -> int:
    limit = getattr(getattr(provider, "capabilities", None), "max_output_tokens", None)
    return min(default, int(limit)) if limit else default


def _dedupe_issues(issues: Iterable[ReviewIssue]) -> list[ReviewIssue]:
    result: list[ReviewIssue] = []
    seen: set[str] = set()
    for issue in issues:
        if issue.issue_id not in seen:
            result.append(issue)
            seen.add(issue.issue_id)
    return result


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _evidence_from_record(record: EvidenceRecord) -> FactCheckEvidence:
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


def _search_stop_reason(prefix: str, result: Any) -> str:
    status = getattr(getattr(result, "status", None), "value", getattr(result, "status", "error"))
    error = getattr(result, "error_code", None)
    error_value = getattr(error, "value", error)
    return f"{prefix}:{status}" + (f":{error_value}" if error_value else "")


def _fact_issue(
    *,
    task: ContentTask,
    assessment: FactCheckAssessment | None,
    message: str,
    category: str = "uncertainty",
    severity: str = "warning",
    layer: str = "fact",
    target_id: str | None = None,
    source_refs: Iterable[str] = (),
    suggested_action: str | None = None,
    suffix: str | None = None,
) -> ReviewIssue:
    suffix = suffix or (assessment.claim_id if assessment is not None else "runtime")
    return ReviewIssue(
        issue_id=f"fact-check-{task.task_id}-{suffix}",
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        layer=layer,  # type: ignore[arg-type]
        message=message,
        target_id=target_id,
        source_refs=_dedupe_strings(source_refs),
        suggested_action=suggested_action,
    )


def _target_records(content: ContentArtifact, quiz: QuizArtifact | None) -> list[dict[str, Any]]:
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
        targets.extend([
            {
                "target_id": question.question_id,
                "artifact_id": quiz.artifact_id,
                "role": "quiz_question",
                "text": question.question,
                "source_refs": refs,
            },
            {
                "target_id": question.question_id,
                "artifact_id": quiz.artifact_id,
                "role": "quiz_answer",
                "text": question.answer,
                "source_refs": refs,
            },
            {
                "target_id": question.question_id,
                "artifact_id": quiz.artifact_id,
                "role": "quiz_explanation",
                "text": question.explanation,
                "source_refs": refs,
            },
        ])
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
    """Extract a bounded set of assertions without treating distractors as facts."""

    if str(getattr(provider, "provider", "unknown")) == "mock":
        # The built-in fixture is deliberately generic prose rather than a
        # textbook semantic oracle. Keep mock workflow regressions offline and
        # represent its no-claim result explicitly in the trace.
        return [], None

    targets = _target_records(content, quiz)
    target_ids = {item["target_id"] for item in targets}
    prompt = load_prompt(
        "extract_fact_check_claims",
        MAX_CLAIMS=policy.max_claims_per_unit,
        KNOWLEDGE_UNIT=json.dumps(_model_dump(unit), ensure_ascii=False, indent=2),
        TARGETS=json.dumps(targets, ensure_ascii=False, indent=2),
    )
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=FactCheckClaimsDraft,
            max_output_tokens=_response_limit(provider, 2200),
            metadata={
                "agent": "extract_fact_check_claims",
                "run_id": content.run_id,
                "knowledge_unit_id": unit.artifact_id,
                "content_artifact_id": content.artifact_id,
                "quiz_artifact_id": quiz.artifact_id if quiz is not None else None,
                "round": round_number,
            },
        ))
        raw_value = _model_dump(response.value)
        draft = FactCheckClaimsDraft.model_validate(raw_value)
    except ProviderError as exc:
        return [], f"claim_extraction_provider_error:{getattr(exc, 'category', 'provider')}:{exc}"
    except Exception as exc:
        return [], f"claim_extraction_error:{exc}"

    claims: list[FactCheckClaim] = []
    seen: set[tuple[str, str, str]] = set()
    for index, candidate in enumerate(draft.claims, start=1):
        if len(claims) >= policy.max_claims_per_unit:
            break
        matching_targets = [
            item for item in targets
            if item["target_id"] == candidate.target_id
        ]
        if candidate.target_id not in target_ids or not matching_targets:
            continue
        # A target id may describe a quiz question, answer and explanation.
        # Infer the role only if the model copied an exact target role through
        # its text; otherwise prefer the first target to avoid invented IDs.
        target = next(
            (
                item for item in matching_targets
                if candidate.text.strip() and candidate.text.strip() in item["text"]
            ),
            matching_targets[0],
        )
        key = (candidate.target_id, target["role"], candidate.text.strip())
        if key in seen:
            continue
        seen.add(key)
        claims.append(FactCheckClaim(
            claim_id=f"fact-claim-{content.artifact_id}-{round_number}-{index}",
            artifact_id=target["artifact_id"],
            knowledge_unit_id=unit.artifact_id,
            target_id=candidate.target_id,
            role=target["role"],
            kind=candidate.kind,
            text=candidate.text.strip()[:1600],
            query=candidate.query.strip()[:500],
            source_refs=list(target["source_refs"]),
        ))
    return claims, None


def _fallback_assessment(
    claim: FactCheckClaim,
    *,
    textbook: list[FactCheckEvidence],
    external: list[FactCheckEvidence],
    stop_reason: str,
) -> FactCheckAssessment:
    return FactCheckAssessment(
        assessment_id=f"assessment-{claim.claim_id}",
        claim_id=claim.claim_id,
        verdict="stance" if claim.kind == "stance" else "insufficient",
        judgement=(
            "该陈述属于解释性或评价性观点，保留为中性标注。"
            if claim.kind == "stance"
            else "自动核查未得到可用于定向修订的充分教材判断。"
        ),
        route="annotate",
        textbook_evidence=textbook,
        external_evidence=external,
        stop_reason=stop_reason,
    )


def _assessment_prompt_payload(
    claims: list[FactCheckClaim],
    evidence_by_claim: Mapping[str, tuple[list[FactCheckEvidence], list[FactCheckEvidence], list[str]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    claim_payload = [
        {
            "claim_id": claim.claim_id,
            "target_id": claim.target_id,
            "role": claim.role,
            "text": claim.text,
            "query": claim.query,
        }
        for claim in claims
    ]
    textbook_payload = []
    external_payload = []
    for claim in claims:
        textbook, external, _ = evidence_by_claim[claim.claim_id]
        textbook_payload.append({"claim_id": claim.claim_id, "evidence": [_model_dump(item) for item in textbook]})
        external_payload.append({"claim_id": claim.claim_id, "evidence": [_model_dump(item) for item in external]})
    return claim_payload, textbook_payload, external_payload


def _assess_claims(
    provider: ModelProvider,
    *,
    claims: list[FactCheckClaim],
    evidence_by_claim: Mapping[str, tuple[list[FactCheckEvidence], list[FactCheckEvidence], list[str]]],
    content_artifact_id: str,
    quiz_artifact_id: str | None,
    round_number: int,
) -> tuple[list[FactCheckAssessment], str | None]:
    if not claims:
        return [], None
    claim_payload, textbook_payload, external_payload = _assessment_prompt_payload(claims, evidence_by_claim)
    prompt = load_prompt(
        "assess_fact_check_claims",
        CLAIMS=json.dumps(claim_payload, ensure_ascii=False, indent=2),
        TEXTBOOK_EVIDENCE=json.dumps(textbook_payload, ensure_ascii=False, indent=2),
        EXTERNAL_EVIDENCE=json.dumps(external_payload, ensure_ascii=False, indent=2),
    )
    drafts: dict[str, FactCheckAssessmentDraft] = {}
    error: str | None = None
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=FactCheckAssessmentsDraft,
            max_output_tokens=_response_limit(provider, 3200),
            metadata={
                "agent": "assess_fact_check_claims",
                "content_artifact_id": content_artifact_id,
                "quiz_artifact_id": quiz_artifact_id,
                "round": round_number,
            },
        ))
        draft = FactCheckAssessmentsDraft.model_validate(_model_dump(response.value))
        allowed = {claim.claim_id for claim in claims}
        for item in draft.assessments:
            if item.claim_id in allowed and item.claim_id not in drafts:
                drafts[item.claim_id] = item
    except ProviderError as exc:
        error = f"assessment_provider_error:{getattr(exc, 'category', 'provider')}:{exc}"
    except Exception as exc:
        error = f"assessment_error:{exc}"

    assessments: list[FactCheckAssessment] = []
    for claim in claims:
        textbook, external, reasons = evidence_by_claim[claim.claim_id]
        stop_reason = ";".join(reasons)
        item = drafts.get(claim.claim_id)
        if item is None:
            assessments.append(_fallback_assessment(
                claim,
                textbook=textbook,
                external=external,
                stop_reason=";".join(filter(None, [stop_reason, error or "assessment_missing"])),
            ))
            continue

        verdict = item.verdict
        sufficient = bool(item.sufficient_textbook_evidence)
        if claim.kind == "stance":
            verdict = "stance"
        elif verdict == "contradicted" and not (sufficient and textbook):
            verdict = "external_conflict" if external else "insufficient"
        elif verdict == "supported" and not textbook:
            verdict = "insufficient"
        elif verdict == "external_conflict" and not external:
            verdict = "insufficient"

        direct_textbook_contradiction = verdict == "contradicted" and sufficient and bool(textbook)
        route = (
            "revise_content"
            if direct_textbook_contradiction and claim.artifact_id == content_artifact_id
            else "regenerate_quiz"
            if direct_textbook_contradiction and claim.artifact_id == quiz_artifact_id
            else "annotate"
            if verdict != "supported"
            else "accept"
        )
        assessments.append(FactCheckAssessment(
            assessment_id=f"assessment-{claim.claim_id}",
            claim_id=claim.claim_id,
            verdict=verdict,
            judgement=item.judgement.strip(),
            route=route,
            textbook_evidence=textbook,
            external_evidence=external,
            stop_reason=stop_reason or None,
        ))
    return assessments, error


def _neutral_issue_for_assessment(
    *,
    task: ContentTask,
    claim: FactCheckClaim,
    assessment: FactCheckAssessment,
    exhausted: bool = False,
) -> ReviewIssue | None:
    refs = [item.source_ref for item in assessment.textbook_evidence if item.source_ref]
    if assessment.verdict == "supported":
        return None
    if assessment.verdict == "stance":
        return _fact_issue(
            task=task,
            assessment=assessment,
            suffix=f"{claim.claim_id}-stance",
            category="stance",
            severity="info",
            layer="stance",
            message="该表述属于解释性或评价性观点，保留为多角度理解提示。",
            target_id=claim.target_id,
            source_refs=refs,
        )
    if assessment.verdict == "external_conflict":
        return _fact_issue(
            task=task,
            assessment=assessment,
            suffix=f"{claim.claim_id}-external-conflict",
            category="conflict",
            severity="warning",
            layer="stance",
            message="外部资料与当前教材存在差异，已保留为中性核实提示，未自动改写教材主线。",
            target_id=claim.target_id,
            source_refs=refs,
            suggested_action="以当前教材为主，并在需要时进行人工复核。",
        )
    if assessment.verdict == "contradicted" and exhausted:
        return _fact_issue(
            task=task,
            assessment=assessment,
            suffix=f"{claim.claim_id}-textbook-contradiction",
            category="fact",
            severity="warning",
            layer="fact",
            message="当前教材存在可定位的直接矛盾，但定向纠错次数已耗尽；文档保留为带风险预览。",
            target_id=claim.target_id,
            source_refs=refs,
            suggested_action="依据已记录的教材证据人工复核并修订该陈述。",
        )
    return _fact_issue(
        task=task,
        assessment=assessment,
        suffix=f"{claim.claim_id}-insufficient",
        category="uncertainty",
        severity="warning",
        layer="fact",
        message="当前教材证据不足以确认该陈述，已保留为中性核实提示。",
        target_id=claim.target_id,
        source_refs=refs,
        suggested_action="补充可定位的教材依据或进行人工复核。",
    )


def _fact_feedback(
    claims: Iterable[FactCheckClaim], assessments: Iterable[FactCheckAssessment]) -> list[dict[str, Any]]:
    claims_by_id = {claim.claim_id: claim for claim in claims}
    feedback: list[dict[str, Any]] = []
    for assessment in assessments:
        if assessment.verdict != "contradicted":
            continue
        claim = claims_by_id.get(assessment.claim_id)
        if claim is None:
            continue
        feedback.append({
            "claim_id": claim.claim_id,
            "target_id": claim.target_id,
            "role": claim.role,
            "claim": claim.text,
            "judgement": assessment.judgement,
            "textbook_evidence": [_model_dump(item) for item in assessment.textbook_evidence],
        })
    return feedback


def _correct_content(
    provider: ModelProvider,
    *,
    task: ContentTask,
    unit: KnowledgeUnit,
    context_pack: Any,
    content: ContentArtifact,
    feedback: list[dict[str, Any]],
    valid_source_refs: set[str],
) -> tuple[ContentArtifact | None, str | None]:
    prompt = load_prompt(
        "revise_content_for_fact_check",
        KNOWLEDGE_UNIT_CONTEXT=json.dumps(_model_dump(unit), ensure_ascii=False, indent=2),
        CONTEXT_PACK=render_context_pack(context_pack),
        CANDIDATE_ARTIFACT=json.dumps(_model_dump(content), ensure_ascii=False, indent=2),
        FACT_CHECK_FEEDBACK=json.dumps(feedback, ensure_ascii=False, indent=2),
    )
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=ContentDraft,
            max_output_tokens=_response_limit(provider, 2400),
            metadata={
                "agent": "revise_content_for_fact_check",
                "run_id": content.run_id,
                "task_id": task.task_id,
                "knowledge_unit_id": unit.artifact_id,
                "revision_of": content.artifact_id,
            },
        ))
        draft = ContentDraft.model_validate(_model_dump(response.value))
    except ProviderError as exc:
        return None, f"content_correction_provider_error:{getattr(exc, 'category', 'provider')}:{exc}"
    except Exception as exc:
        return None, f"content_correction_error:{exc}"

    candidate = _content_artifact_from_draft(
        draft=draft,
        task=task,
        unit=unit,
        pack=context_pack,
        run_id=content.run_id,
        provider_name=str(getattr(provider, "provider", "unknown")),
        fixture_adaptation=False,
        attempt=content.version + 1,
        prompt_version="revise_content_for_fact_check:v1",
    )
    candidate.metadata = {
        **candidate.metadata,
        "fact_check_correction": {
            "revision_of": content.artifact_id,
            "feedback": feedback,
            "provider_metadata": _provider_metadata(response, agent="revise_content_for_fact_check"),
        },
    }
    check = _content_hard_check(
        task=task,
        unit=unit,
        context_pack=context_pack,
        valid_source_refs=valid_source_refs,
        candidate_artifact=candidate,
    )
    candidate.issues = list(check.issues)
    candidate.status = "accepted" if check.status == "accepted" else "needs_revision"
    return candidate, None if candidate.status == "accepted" else "content_correction_hard_check_failed"


def _correct_quiz(
    provider: ModelProvider,
    *,
    task: ContentTask,
    unit: KnowledgeUnit,
    context_pack: Any,
    quiz: QuizArtifact,
    feedback: list[dict[str, Any]],
    valid_source_refs: set[str],
    correction_number: int,
) -> tuple[QuizArtifact | None, str | None]:
    prompt = load_prompt(
        "regenerate_quiz_for_fact_check",
        KNOWLEDGE_UNIT_CONTEXT=json.dumps(_model_dump(unit), ensure_ascii=False, indent=2),
        CONTEXT_PACK=render_context_pack(context_pack),
        QUIZ_ARTIFACT=json.dumps(_model_dump(quiz), ensure_ascii=False, indent=2),
        FACT_CHECK_FEEDBACK=json.dumps(feedback, ensure_ascii=False, indent=2),
    )
    raw_response = ""
    try:
        response = provider.generate_structured(StructuredGenerationRequest(
            prompt=prompt,
            schema=QuizDraft,
            max_output_tokens=_response_limit(provider, 2400),
            metadata={
                "agent": "regenerate_quiz_for_fact_check",
                "run_id": quiz.run_id,
                "task_id": task.task_id,
                "knowledge_unit_id": unit.artifact_id,
                "revision_of": quiz.artifact_id,
            },
        ))
        raw_response = str(getattr(response, "raw_text", "") or "")
        draft = QuizDraft.model_validate(_model_dump(response.value))
    except ProviderError as exc:
        return None, f"quiz_correction_provider_error:{getattr(exc, 'category', 'provider')}:{exc}"
    except Exception as exc:
        return None, f"quiz_correction_error:{exc}"

    selected_count = draft.question_count if draft.question_count is not None else len(draft.questions)
    artifact = QuizArtifact(
        artifact_id=f"quiz-{task.task_id}-fact-{correction_number}",
        run_id=quiz.run_id,
        version=quiz.version + 1,
        status="draft",
        source_refs=_dedupe_strings([
            *context_pack.selected_source_refs,
            *context_pack.source_refs,
            *(excerpt.source_ref for excerpt in context_pack.excerpts),
        ]),
        created_by=f"provider:{getattr(response, 'provider', getattr(provider, 'provider', 'unknown'))}",
        knowledge_unit_ids=[unit.artifact_id],
        target_objectives=_dedupe_strings([
            *draft.target_objectives,
            *(objective for question in draft.questions for objective in question.target_objectives),
        ]),
        questions=list(draft.questions),
        question_count=selected_count,
        task_id=task.task_id,
        context_pack_id=context_pack.context_pack_id,
        prompt_version="regenerate_quiz_for_fact_check:v1",
        generation_metadata={
            "agent": "regenerate_quiz_for_fact_check",
            "prompt": prompt,
            "question_count": selected_count,
            "question_count_source": "agent_declared" if draft.question_count is not None else "inferred_from_questions",
            "revision_of": quiz.artifact_id,
            "fact_check_feedback": feedback,
            **_provider_metadata(response, agent="regenerate_quiz_for_fact_check"),
        },
        raw_response=raw_response or draft.model_dump_json(),
    )
    if draft.knowledge_unit_id != unit.artifact_id:
        artifact.issues.append(ReviewIssue(
            issue_id=f"fact-check-quiz-unit-mismatch-{artifact.artifact_id}",
            category="coverage",
            severity="blocking",
            message="事实纠错后的题目响应绑定到了错误的知识单元。",
            target_id=artifact.artifact_id,
            suggested_action="仅使用当前知识单元的稳定 ID 重新生成题目。",
        ))
    check = validate_quiz_artifact(
        artifact,
        unit=unit,
        context_pack=context_pack,
        valid_source_refs=valid_source_refs,
    )
    artifact.issues = list(check.issues)
    artifact.status = "accepted" if check.status == "accepted" else "needs_revision"
    return artifact, None if artifact.status == "accepted" else "quiz_correction_structure_check_failed"


def _search_textbook(
    skill: TextbookDatabaseSearchSkill,
    *,
    claim: FactCheckClaim,
    valid_source_refs: set[str],
    limit: int,
) -> tuple[list[FactCheckEvidence], str]:
    try:
        result = skill.search(claim.query, allowed_source_refs=valid_source_refs, limit=limit)
    except Exception as exc:
        return [], f"textbook:error:{exc}"
    return [_evidence_from_record(item) for item in result.evidence], _search_stop_reason("textbook", result)


def _search_web(
    skill: WebResourceSearchSkill,
    *,
    claim: FactCheckClaim,
    limit: int,
) -> tuple[list[FactCheckEvidence], str]:
    try:
        result = skill.search(claim.query, limit=limit)
    except Exception as exc:
        return [], f"web:error:{exc}"
    return [_evidence_from_record(item) for item in result.evidence], _search_stop_reason("web", result)


def _run_unit_loop(
    provider: ModelProvider,
    *,
    task: ContentTask,
    unit: KnowledgeUnit,
    context_pack: Any,
    content: ContentArtifact,
    quiz: QuizArtifact | None,
    policy: FactCheckPolicy,
    valid_source_refs: set[str],
    textbook_search_skill: TextbookDatabaseSearchSkill,
    web_search_skill: WebResourceSearchSkill,
) -> tuple[ContentArtifact, QuizArtifact | None, FactCheckUnitTrace, list[ReviewIssue], bool]:
    current_content = content
    current_quiz = quiz
    rounds: list[FactCheckRoundTrace] = []
    final_issues: list[ReviewIssue] = []
    corrections_used = 0
    quiz_excluded = False

    while True:
        round_number = corrections_used
        claims, extraction_error = _extract_claims(
            provider,
            unit=unit,
            content=current_content,
            quiz=current_quiz,
            policy=policy,
            round_number=round_number,
        )
        if extraction_error:
            rounds.append(FactCheckRoundTrace(
                round=round_number,
                content_artifact_id=current_content.artifact_id,
                quiz_artifact_id=current_quiz.artifact_id if current_quiz is not None else None,
                claims=[],
                assessments=[],
                route="annotate",
                stop_reason=extraction_error,
            ))
            final_issues.append(_fact_issue(
                task=task,
                assessment=None,
                suffix=f"claim-extraction-{round_number}",
                category="uncertainty",
                severity="warning",
                layer="fact",
                message="事实核查未能完成陈述抽取，已保留为中性核实提示。",
                target_id=current_content.artifact_id,
                suggested_action="检查核查模型输出并进行人工复核。",
            ))
            break

        evidence_by_claim: dict[str, tuple[list[FactCheckEvidence], list[FactCheckEvidence], list[str]]] = {}
        stance_claims: list[FactCheckClaim] = []
        fact_claims: list[FactCheckClaim] = []
        for claim in claims:
            if claim.kind == "stance":
                stance_claims.append(claim)
                evidence_by_claim[claim.claim_id] = ([], [], ["stance:no_textbook_search"])
                continue
            fact_claims.append(claim)
            textbook, textbook_reason = _search_textbook(
                textbook_search_skill,
                claim=claim,
                valid_source_refs=valid_source_refs,
                limit=policy.textbook_result_limit,
            )
            evidence_by_claim[claim.claim_id] = (textbook, [], [textbook_reason])

        assessments, assessment_error = _assess_claims(
            provider,
            claims=fact_claims,
            evidence_by_claim=evidence_by_claim,
            content_artifact_id=current_content.artifact_id,
            quiz_artifact_id=current_quiz.artifact_id if current_quiz is not None else None,
            round_number=round_number,
        )
        for claim in stance_claims:
            assessments.append(_fallback_assessment(
                claim,
                textbook=[],
                external=[],
                stop_reason="stance:no_textbook_search",
            ))

        # External search is a second-pass supplement only after the assessor
        # reports textbook evidence as insufficient. It is disabled by default.
        if policy.web_enabled and policy.web_query_limit > 0 and not assessment_error:
            follow_up_claims: list[FactCheckClaim] = []
            for assessment in assessments:
                if assessment.verdict != "insufficient":
                    continue
                claim = next((item for item in fact_claims if item.claim_id == assessment.claim_id), None)
                if claim is not None and len(follow_up_claims) < policy.web_query_limit:
                    follow_up_claims.append(claim)
            if follow_up_claims:
                for claim in follow_up_claims:
                    textbook, _external, reasons = evidence_by_claim[claim.claim_id]
                    external, web_reason = _search_web(web_search_skill, claim=claim, limit=3)
                    evidence_by_claim[claim.claim_id] = (textbook, external, [*reasons, web_reason])
                reassessed, reassessment_error = _assess_claims(
                    provider,
                    claims=follow_up_claims,
                    evidence_by_claim=evidence_by_claim,
                    content_artifact_id=current_content.artifact_id,
                    quiz_artifact_id=current_quiz.artifact_id if current_quiz is not None else None,
                    round_number=round_number,
                )
                if not reassessment_error:
                    replacement = {item.claim_id: item for item in reassessed}
                    assessments = [replacement.get(item.claim_id, item) for item in assessments]

        claims_by_id = {claim.claim_id: claim for claim in claims}
        content_contradictions = [
            item for item in assessments
            if item.route == "revise_content"
        ]
        quiz_contradictions = [
            item for item in assessments
            if item.route == "regenerate_quiz"
        ]

        if content_contradictions and corrections_used < policy.max_corrections_per_unit:
            feedback = _fact_feedback(claims, content_contradictions)
            corrected, correction_error = _correct_content(
                provider,
                task=task,
                unit=unit,
                context_pack=context_pack,
                content=current_content,
                feedback=feedback,
                valid_source_refs=valid_source_refs,
            )
            corrections_used += 1
            rounds.append(FactCheckRoundTrace(
                round=round_number,
                content_artifact_id=current_content.artifact_id,
                quiz_artifact_id=current_quiz.artifact_id if current_quiz is not None else None,
                claims=claims,
                assessments=assessments,
                route="revise_content",
                correction_artifact_id=corrected.artifact_id if corrected is not None else None,
                stop_reason=correction_error or "targeted_content_correction",
            ))
            if corrected is not None and corrected.status == "accepted":
                current_content = corrected
            if corrections_used <= policy.max_corrections_per_unit:
                continue

        elif quiz_contradictions and current_quiz is not None and corrections_used < policy.max_corrections_per_unit:
            feedback = _fact_feedback(claims, quiz_contradictions)
            corrected, correction_error = _correct_quiz(
                provider,
                task=task,
                unit=unit,
                context_pack=context_pack,
                quiz=current_quiz,
                feedback=feedback,
                valid_source_refs=valid_source_refs,
                correction_number=corrections_used + 1,
            )
            corrections_used += 1
            rounds.append(FactCheckRoundTrace(
                round=round_number,
                content_artifact_id=current_content.artifact_id,
                quiz_artifact_id=current_quiz.artifact_id,
                claims=claims,
                assessments=assessments,
                route="regenerate_quiz",
                correction_artifact_id=corrected.artifact_id if corrected is not None else None,
                stop_reason=correction_error or "targeted_quiz_regeneration",
            ))
            if corrected is not None and corrected.status == "accepted":
                current_quiz = corrected
            if corrections_used <= policy.max_corrections_per_unit:
                continue

        exhausted_content = bool(content_contradictions)
        exhausted_quiz = bool(quiz_contradictions)
        route = (
            "exclude_quiz" if exhausted_quiz else
            "revise_content" if exhausted_content else
            "annotate" if any(item.verdict != "supported" for item in assessments) else
            "accept"
        )
        stop_reason = (
            "max_corrections_exhausted" if (exhausted_content or exhausted_quiz) else
            assessment_error or
            "annotated" if route == "annotate" else
            "accepted"
        )
        rounds.append(FactCheckRoundTrace(
            round=round_number,
            content_artifact_id=current_content.artifact_id,
            quiz_artifact_id=current_quiz.artifact_id if current_quiz is not None else None,
            claims=claims,
            assessments=assessments,
            route=route,
            stop_reason=stop_reason,
        ))
        for assessment in assessments:
            claim = claims_by_id.get(assessment.claim_id)
            if claim is None:
                continue
            issue = _neutral_issue_for_assessment(
                task=task,
                claim=claim,
                assessment=assessment,
                exhausted=assessment.verdict == "contradicted" and (exhausted_content or exhausted_quiz),
            )
            if issue is not None:
                final_issues.append(issue)
        if assessment_error:
            final_issues.append(_fact_issue(
                task=task,
                assessment=None,
                suffix=f"assessment-{round_number}",
                category="uncertainty",
                severity="warning",
                layer="fact",
                message="事实核查未能完成证据判断，已保留为中性核实提示。",
                target_id=current_content.artifact_id,
                suggested_action="检查核查模型输出并进行人工复核。",
            ))
        if exhausted_quiz:
            current_quiz = None
            quiz_excluded = True
        break

    final_issues = _dedupe_issues(final_issues)
    has_warning = any(issue.severity == "warning" for issue in final_issues)
    trace = FactCheckUnitTrace(
        trace_id=f"fact-check-trace-{uuid.uuid4().hex[:12]}",
        run_id=content.run_id,
        task_id=task.task_id,
        knowledge_unit_id=unit.artifact_id,
        max_corrections=policy.max_corrections_per_unit,
        rounds=rounds,
        final_status="at_risk" if has_warning else "accepted",
        corrections_used=corrections_used,
        stop_reason=rounds[-1].stop_reason or "accepted",
        final_content_artifact_id=current_content.artifact_id,
        final_quiz_artifact_id=current_quiz.artifact_id if current_quiz is not None else None,
    )
    return current_content, current_quiz, trace, final_issues, quiz_excluded


def _transient_textbook_skill(source_blocks: list[SourceBlock]) -> tuple[TextbookDatabaseSearchSkill, sqlite3.Connection]:
    """Build a run-scoped FTS5 index so prior books cannot leak into evidence."""

    connection = sqlite3.connect(":memory:")
    index_source_blocks(connection, source_blocks)
    return SQLiteFts5TextbookDatabaseSearchSkill(connection), connection


def run_fact_check_loop(
    provider: ModelProvider,
    *,
    run_id: str,
    blueprint: LearningBlueprint,
    tasks: list[ContentTask],
    context_packs: list[Any],
    content_artifacts: list[ContentArtifact],
    quiz_artifacts: list[QuizArtifact],
    source_blocks: list[SourceBlock],
    valid_source_refs: Iterable[str],
    policy: FactCheckPolicy,
    textbook_search_skill: TextbookDatabaseSearchSkill | None = None,
    web_search_skill: WebResourceSearchSkill | None = None,
) -> dict[str, Any]:
    """Run A-003 over accepted artifacts and return final replacements plus trace."""

    valid_refs = {str(ref).strip() for ref in valid_source_refs if str(ref).strip()}
    web_skill = web_search_skill or DisabledWebResourceSearchSkill()
    connection: sqlite3.Connection | None = None
    setup_issue: ReviewIssue | None = None
    if textbook_search_skill is None:
        try:
            textbook_search_skill, connection = _transient_textbook_skill(source_blocks)
        except Exception as exc:
            setup_issue = ReviewIssue(
                issue_id=f"fact-check-textbook-index-{run_id}",
                category="uncertainty",
                severity="warning",
                layer="fact",
                message="当前教材的事实核查索引不可用，已保留为中性核实提示。",
                suggested_action="检查教材索引并进行人工复核。",
            )
            textbook_search_skill = None

    units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
    packs = {pack.task_id: pack for pack in context_packs}
    final_content = list(content_artifacts)
    final_quiz = list(quiz_artifacts)
    content_index = {artifact.task_id: index for index, artifact in enumerate(final_content) if artifact.task_id}
    quiz_index = {artifact.task_id: index for index, artifact in enumerate(final_quiz) if artifact.task_id}
    traces: list[FactCheckUnitTrace] = []
    issues: list[ReviewIssue] = [setup_issue] if setup_issue is not None else []
    excluded_quiz_count = 0

    try:
        if textbook_search_skill is not None:
            for task in tasks:
                content_position = content_index.get(task.task_id)
                unit = units.get(task.knowledge_unit_id)
                pack = packs.get(task.task_id)
                if content_position is None or unit is None or pack is None:
                    continue
                content = final_content[content_position]
                if content.status != "accepted":
                    continue
                quiz_position = quiz_index.get(task.task_id)
                quiz = final_quiz[quiz_position] if quiz_position is not None and final_quiz[quiz_position].status == "accepted" else None
                corrected_content, corrected_quiz, trace, unit_issues, quiz_excluded = _run_unit_loop(
                    provider,
                    task=task,
                    unit=unit,
                    context_pack=pack,
                    content=content,
                    quiz=quiz,
                    policy=policy,
                    valid_source_refs=valid_refs,
                    textbook_search_skill=textbook_search_skill,
                    web_search_skill=web_skill,
                )
                final_content[content_position] = corrected_content
                if quiz_position is not None:
                    if quiz_excluded:
                        final_quiz[quiz_position] = None  # type: ignore[list-item]
                        excluded_quiz_count += 1
                    elif corrected_quiz is not None:
                        final_quiz[quiz_position] = corrected_quiz
                traces.append(trace)
                issues.extend(unit_issues)
    finally:
        if connection is not None:
            connection.close()

    final_quiz = [artifact for artifact in final_quiz if artifact is not None]
    issues = _dedupe_issues(issues)
    summary = {
        "unit_count": len(traces),
        "accepted_count": sum(trace.final_status == "accepted" for trace in traces),
        "at_risk_count": sum(trace.final_status == "at_risk" for trace in traces),
        "claim_count": sum(len(round_trace.claims) for trace in traces for round_trace in trace.rounds),
        "correction_count": sum(trace.corrections_used for trace in traces),
        "excluded_quiz_count": excluded_quiz_count,
        "web_enabled": policy.web_enabled,
        "final_status": "at_risk" if any(issue.severity == "warning" for issue in issues) else "accepted",
    }
    artifact = FactCheckArtifact(
        artifact_id=f"fact-check-{run_id}",
        run_id=run_id,
        version=1,
        status=summary["final_status"],
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
