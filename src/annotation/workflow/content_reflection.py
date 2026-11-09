"""Bounded per-unit content reflection subgraph."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from annotation.domain.artifacts import (
    ContentArtifact,
    ContentAttemptTrace,
    ContentCritiqueDraft,
    ContentTask,
    ContentUnitLoopTrace,
    KnowledgeUnit,
)
from annotation.prompt_loader import load_prompt
from annotation.providers import ModelProvider, ProviderError, StructuredGenerationRequest
from annotation.workflow.content import render_context_pack, expand_source_aliases, candidate_prompt_data
from annotation.workflow.content_agent import author_with_tools
from annotation.workflow.budget import PromptBudget
from annotation.workflow.content_support import (
    blocked_content_artifact,
    _content_prompt_task,
    _content_prompt_unit,
    _content_artifact_from_draft,
    _content_hard_check,
    content_issue,
    _critic_review_issues,
    _dedupe_review_issues,
    _unit_context,
)
from annotation.workflow.helpers import response_metadata, provider_metadata
from annotation.workflow.models import ContentDraft, ContentReflectionState

CONTENT_CRITIC_PROMPT_VERSION = "critique_content_artifact:v4:compact-sources"


def build_content_reflection_subgraph(
    provider: ModelProvider,
    *,
    max_attempts: int,
    retrieval_service: Any | None = None,
):
    """Build the bounded per-unit explanation reflection subgraph.

    The main graph invokes this graph once per unit in sequence.  Keeping the
    loop state local prevents a failed or revised candidate from becoming
    feedback for a different knowledge unit.
    """

    provider_name = str(getattr(provider, "provider", "unknown"))

    def response_limit(default: int) -> int:
        limit = getattr(getattr(provider, "capabilities", None), "max_output_tokens", None)
        return min(default, int(limit)) if limit else default

    def artifact_from_draft(
        *,
        draft: ContentDraft,
        state: ContentReflectionState,
        attempt: int,
        prompt_version: str,
    ) -> ContentArtifact:
        task = state["task"]
        unit = state["unit"]
        pack = state["context_pack"]
        primary = _content_artifact_from_draft(
            draft=draft,
            task=task,
            unit=unit,
            pack=pack,
            run_id=state["run_id"],
            provider_name=provider_name,
            attempt=attempt,
            prompt_version=prompt_version,
        )
        return primary

    def generate_candidate(state: ContentReflectionState) -> dict[str, Any]:
        retrieval_result: dict[str, Any] = {}
        try:
            response, pack, decision, prompt = author_with_tools(provider, state, retrieval_service)
            state = {**state, "context_pack": pack, "retrieval_decision": decision}
            retrieval_result = {"context_pack": pack, "retrieval_decision": decision}
        except ProviderError as exc:
            return {
                "attempt": int(state.get("attempt", 0)) + 1,
                "generation_stage": "revision" if state.get("candidate_artifact") else "initial",
                "generation_prompt": getattr(exc, "prompt", ""),
                "generation_status": "provider_error",
                "generation_error": str(exc),
                "generation_raw_output": str(getattr(exc, "raw_output", "") or ""),
                "generation_error_category": exc.category,
                "generation_retryable": bool(exc.retryable or exc.category == "schema"),
                "generation_provider_metadata": {**provider_metadata(provider), "agent": "content_agent_step"},
                "context_pack": getattr(exc, "context_pack", state["context_pack"]),
            }

        attempt = int(state.get("attempt", 0)) + 1
        task = state["task"]
        unit = state["unit"]
        pack = state["context_pack"]
        is_revision = state.get("candidate_artifact") is not None
        agent = "content_agent_step"
        prompt_version = "content_agent_step:v1"
        result: dict[str, Any] = {
            "attempt": attempt,
            "generation_stage": "revision" if is_revision else "initial",
            "generation_prompt": prompt,
            "revision_prompt": prompt if is_revision else None,
            "generation_raw_output": "",
            "generation_parsed_output": None,
            "generation_status": "provider_error",
            "generation_error": None,
            "generation_error_category": None,
            "generation_retryable": False,
            "generation_provider_metadata": {},
            "candidate_draft": None,
            "candidate_artifact": None,
            "hard_check": None,
            "critic_prompt": None,
            "critic_raw_output": "",
            "critic_parsed_output": None,
            "critic_status": "skipped",
            "critic_attempt": 0,
            "critic_error": None,
            "critic_error_category": None,
            "critic_retryable": False,
            "critic_provider_metadata": {},
            "critic_feedback": [],
            **retrieval_result,
        }
        try:
            if not pack.excerpts:
                result.update(generation_error="教材检索未返回可用证据，未生成正文。",
                              generation_error_category="no_evidence")
                return result
            draft = ContentDraft.model_validate(response.value.model_dump(mode="python"))
            draft.source_refs = expand_source_aliases(draft.source_refs, pack)
            for note in draft.evidence_notes:
                note.source_refs = expand_source_aliases(note.source_refs, pack)
            metadata = response_metadata(response)
            metadata["agent"] = agent
            result.update({
                "generation_status": "succeeded",
                "generation_raw_output": response.raw_text,
                "generation_parsed_output": draft.model_dump(mode="json"),
                "generation_provider_metadata": metadata,
                "candidate_draft": draft,
            })
            result["candidate_artifact"] = artifact_from_draft(
                draft=draft, state={**state, **result},
                attempt=attempt, prompt_version=prompt_version,
            )
        except ProviderError as exc:
            category = getattr(exc, "category", "provider")
            parsed_output = getattr(exc, "parsed_output", None)
            if hasattr(parsed_output, "model_dump"):
                parsed_output = parsed_output.model_dump(mode="json")
            elif not isinstance(parsed_output, dict):
                parsed_output = None
            result.update({
                "generation_status": "schema_error" if category == "schema" else "provider_error",
                "generation_raw_output": str(getattr(exc, "raw_output", "") or ""),
                "generation_parsed_output": parsed_output,
                "generation_error": str(exc),
                "generation_error_category": category,
                "generation_retryable": bool(getattr(exc, "retryable", False) or category == "schema"),
                "generation_provider_metadata": {**provider_metadata(provider), "agent": agent},
            })
        except Exception as exc:
            logging.getLogger(__name__).exception("Unexpected content reflection failure")
            result.update({
                "generation_status": "provider_error",
                "generation_error": str(exc),
                "generation_error_category": "runtime",
                "generation_provider_metadata": {**provider_metadata(provider), "agent": agent},
            })
        return result


    def hard_check_candidate(state: ContentReflectionState) -> dict[str, Any]:
        if state.get("generation_status") != "succeeded":
            return {"hard_check": None}
        check = _content_hard_check(
            task=state["task"],
            unit=state["unit"],
            context_pack=state["context_pack"],
            valid_source_refs=set(state.get("valid_source_refs", [])),
            candidate_artifact=state.get("candidate_artifact"),
        )
        return {"hard_check": check}

    def after_hard_check(state: ContentReflectionState) -> str:
        check = state.get("hard_check")
        return "critic" if check is not None and check.status == "accepted" else "route"

    def critic_candidate(state: ContentReflectionState) -> dict[str, Any]:
        task = state["task"]
        unit = state["unit"]
        candidate = state.get("candidate_artifact")
        prompt = load_prompt(
            "critique_content_artifact",
            KNOWLEDGE_UNIT_CONTEXT=_content_prompt_unit(unit),
            DEPTH_GUIDANCE=task.depth_guidance,
            ACCEPTANCE_CRITERIA=json.dumps(task.acceptance_criteria, ensure_ascii=False),
            CONTEXT_PACK=render_context_pack(state["context_pack"], aliases=True),
            CANDIDATE_ARTIFACT=json.dumps(
                candidate_prompt_data(candidate, state["context_pack"]) or {},
                ensure_ascii=False,
                indent=2,
            ),
        )
        result: dict[str, Any] = {
            "critic_attempt": int(state.get("critic_attempt", 0)) + 1,
            "critic_prompt": prompt,
            "critic_raw_output": "",
            "critic_parsed_output": None,
            "critic_status": "provider_error",
            "critic_error": None,
            "critic_error_category": None,
            "critic_retryable": False,
            "critic_provider_metadata": {},
            "critic_feedback": [],
        }
        try:
            PromptBudget.for_provider(provider, output_limit=1200).check(prompt, ContentCritiqueDraft)
            response = provider.generate_structured(StructuredGenerationRequest(
                prompt=prompt,
                schema=ContentCritiqueDraft,
                max_output_tokens=response_limit(1200),
                metadata={
                    "agent": "critique_content_artifact",
                    "prompt_version": CONTENT_CRITIC_PROMPT_VERSION,
                    "run_id": state["run_id"],
                    "task_id": task.task_id,
                    "knowledge_unit_id": unit.artifact_id,
                    "context_pack_id": state["context_pack"].context_pack_id,
                    "attempt": state["attempt"],
                },
            ))
            critique = ContentCritiqueDraft.model_validate(response.value.model_dump(mode="python"))
            metadata = response_metadata(response)
            metadata["agent"] = "critique_content_artifact"
            metadata["prompt_version"] = CONTENT_CRITIC_PROMPT_VERSION
            raw_output = response.raw_text
            feedback = _critic_review_issues(
                critique,
                task=task,
                primary_artifact=candidate,
                attempt=int(state["attempt"]),
            )
            result.update({
                "critic_status": "succeeded",
                "critic_raw_output": raw_output,
                "critic_parsed_output": critique.model_dump(mode="json"),
                "critic_provider_metadata": metadata,
                "critic_feedback": feedback,
            })
        except ProviderError as exc:
            category = getattr(exc, "category", "provider")
            parsed_output = getattr(exc, "parsed_output", None)
            if hasattr(parsed_output, "model_dump"):
                parsed_output = parsed_output.model_dump(mode="json")
            elif not isinstance(parsed_output, dict):
                parsed_output = None
            result.update({
                "critic_status": "schema_error" if category == "schema" else "provider_error",
                "critic_raw_output": str(getattr(exc, "raw_output", "") or ""),
                "critic_parsed_output": parsed_output,
                "critic_error": str(exc),
                "critic_error_category": category,
                "critic_retryable": bool(getattr(exc, "retryable", False) or category == "schema"),
                "critic_provider_metadata": {**provider_metadata(provider), "agent": "critique_content_artifact"},
            })
        except Exception as exc:
            logging.getLogger(__name__).exception("Unexpected content reflection failure")
            result.update({
                "critic_status": "provider_error",
                "critic_error": str(exc),
                "critic_error_category": "runtime",
                "critic_provider_metadata": {**provider_metadata(provider), "agent": "critique_content_artifact"},
            })
        result["critic_provider_metadata"]["critic_attempts"] = result["critic_attempt"]
        return result

    def after_critic(state: ContentReflectionState) -> str:
        if (state.get("critic_status") != "succeeded"
                and state.get("critic_retryable")
                and int(state.get("critic_attempt", 0)) < max_attempts):
            return "retry"
        return "route"

    def route_candidate(state: ContentReflectionState) -> dict[str, Any]:
        attempt = int(state.get("attempt", 0))
        generation_status = state.get("generation_status", "provider_error")
        critic_status = state.get("critic_status", "skipped")
        hard_check = state.get("hard_check")
        feedback = list(getattr(hard_check, "issues", []) or []) + list(state.get("critic_feedback", []))
        has_critic_block = any(issue.severity == "blocking" for issue in state.get("critic_feedback", []))
        non_retryable_error = (
            generation_status == "provider_error" and not state.get("generation_retryable")
        )
        requires_revision = (
            generation_status != "succeeded"
            or hard_check is None
            or hard_check.status != "accepted"
            or has_critic_block
        )
        if state.get("generation_error_category") in {"no_evidence", "retrieval_budget"}:
            route, stop_reason, final_status = "block", ("retrieval_budget" if state.get("generation_error_category") == "retrieval_budget" else "context_pack_empty"), "blocked"
        elif non_retryable_error:
            route, stop_reason, final_status = "fail", "non_retryable_provider_error", "failed"
        elif requires_revision and attempt < max_attempts:
            route, stop_reason, final_status = "revise", None, None
        elif requires_revision:
            route, stop_reason, final_status = "block", "max_attempts", "blocked"
        else:
            route, stop_reason, final_status = "accept", "accepted", "accepted"

        if route == "accept" and critic_status != "succeeded":
            feedback.append(content_issue(
                task=state["task"], suffix=f"critic-incomplete-{attempt}",
                category="coverage", severity="warning",
                message="教学评审未完成：" + str(state.get("critic_error") or "Critic 未返回有效结果。"),
                suggested_action="稍后重新进行教学评审。",
            ))
        candidate = state.get("candidate_artifact")
        if final_status is not None and candidate is None:
            error = state.get("generation_error") or state.get("critic_error") or "内容反思 loop 未生成候选。"
            if not feedback:
                feedback.append(content_issue(
                    task=state["task"],
                    suffix=f"generation-error-{attempt}",
                    category="coverage",
                    severity="blocking",
                    message=error,
                    suggested_action="检查模型调用或修订当前知识单元。",
                ))
            candidate = blocked_content_artifact(
                task=state["task"],
                unit=state["unit"],
                context_pack=state.get("context_pack"),
                run_id=state["run_id"],
                provider_name=provider_name,
                attempt=attempt,
                error=error,
                issues=feedback,
            )
        if route == "accept" and candidate is not None:
            candidate.status = "accepted"
            candidate.issues = _dedupe_review_issues([*candidate.issues, *feedback])
        elif final_status is not None and candidate is not None:
            candidate.status = "blocked"
            candidate.issues = _dedupe_review_issues([*candidate.issues, *feedback])

        trace_attempt = ContentAttemptTrace(
            attempt=attempt,
            generation_stage=state.get("generation_stage", "initial"),
            generation_prompt=state.get("generation_prompt", ""),
            revision_prompt=state.get("revision_prompt"),
            generation_raw_output=state.get("generation_raw_output", ""),
            generation_parsed_output=state.get("generation_parsed_output"),
            candidate_artifact=candidate,
            generation_status=generation_status,
            generation_error=state.get("generation_error"),
            generation_error_category=state.get("generation_error_category"),
            generation_provider_metadata=dict(state.get("generation_provider_metadata", {})),
            hard_check=hard_check,
            critic_prompt=state.get("critic_prompt"),
            critic_raw_output=state.get("critic_raw_output", ""),
            critic_parsed_output=state.get("critic_parsed_output"),
            critic_status=critic_status,
            critic_error=state.get("critic_error"),
            critic_error_category=state.get("critic_error_category"),
            critic_provider_metadata=dict(state.get("critic_provider_metadata", {})),
            retrieval_decision=(
                state.get("retrieval_decision").model_dump(mode="json")
                if state.get("retrieval_decision") is not None else None
            ),
            retrieval_calls=list(state.get("context_pack").retrieval_calls if state.get("context_pack") is not None else []),
            feedback=_dedupe_review_issues(feedback),
            route=route,
            stop_reason=stop_reason,
        )
        attempts = [*state.get("attempts", []), trace_attempt]
        result: dict[str, Any] = {"attempts": attempts, "route": route}
        if final_status is not None:
            trace = ContentUnitLoopTrace(
                trace_id=f"content-trace-{uuid.uuid4().hex[:12]}",
                run_id=state["run_id"],
                task_id=state["task"].task_id,
                knowledge_unit_id=state["unit"].artifact_id,
                context_pack_id=state["context_pack"].context_pack_id,
                max_attempts=max_attempts,
                attempts=attempts,
                final_status=final_status,
                final_attempt=attempt,
                stop_reason=stop_reason or "max_attempts",
                final_content_artifact_id=candidate.artifact_id if candidate is not None else None,
            )
            result.update({
                "content_loop_trace": trace,
                "final_artifacts": [candidate] if candidate is not None else [],
                "final_draft": state.get("candidate_draft") if final_status == "accepted" else None,
            })
        return result

    def next_route(state: ContentReflectionState) -> str:
        return state.get("route", "fail")

    graph = StateGraph(ContentReflectionState)
    graph.add_node("generate_candidate", generate_candidate)
    graph.add_node("hard_check_candidate", hard_check_candidate)
    graph.add_node("critic_candidate", critic_candidate)
    graph.add_node("route_candidate", route_candidate)
    graph.add_edge(START, "generate_candidate")
    graph.add_edge("generate_candidate", "hard_check_candidate")
    graph.add_conditional_edges(
        "hard_check_candidate",
        after_hard_check,
        {"critic": "critic_candidate", "route": "route_candidate"},
    )
    graph.add_conditional_edges(
        "critic_candidate", after_critic,
        {"retry": "critic_candidate", "route": "route_candidate"},
    )
    graph.add_conditional_edges(
        "route_candidate",
        next_route,
        {"accept": END, "revise": "generate_candidate", "block": END, "fail": END},
    )
    # The outer node is the durable recovery boundary, including all its workers.
    return graph.compile(checkpointer=False)


