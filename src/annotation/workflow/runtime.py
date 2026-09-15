"""Outer LangGraph orchestration for the workflow POC."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from annotation.config import (
    STORAGE_DIR,
    blueprint_max_attempts as configured_blueprint_max_attempts,
    content_reflection_max_attempts as configured_content_reflection_max_attempts,
)
from annotation.domain.artifacts import (
    BlueprintAttemptTrace,
    BlueprintLoopTrace,
    ContentArtifact,
    ContentAttemptTrace,
    ContentHardCheckResult,
    ContentTask,
    ContentUnitLoopTrace,
    ContextPack,
    KnowledgeUnit,
    LearningBlueprint,
    LearningDocument,
    QuizArtifact,
    ReviewIssue,
)
from annotation.prompt_loader import load_prompt
from annotation.providers import ModelProvider, MockProvider, ProviderError, StructuredGenerationRequest
from annotation.workflow.blueprint_checks import validate_blueprint
from annotation.workflow.content import build_context_pack, write_content_run_artifact
from annotation.workflow.content_reflection import build_content_reflection_subgraph
from annotation.workflow.content_support import (
    _blocked_content_artifact,
    _content_issue,
    _content_loop_summary,
    _merge_messages,
    _plan_content_tasks,
)
from annotation.workflow.graph import (
    _assemble_document_from_artifacts,
    _blueprint_from_draft,
    _default_pdf,
    _fallback_document,
    _metadata,
    _provider_metadata,
    _review_report_for,
    _source_context,
)
from annotation.workflow.models import BlueprintDraft, WorkflowState
from annotation.workflow.persistence import persist_workflow_snapshots
from annotation.workflow.quiz import blocked_quiz_artifact, generate_quiz_artifact, validate_quiz_coverage

def build_minimal_graph(
    provider: ModelProvider | None = None,
    *,
    blueprint_max_attempts: int | None = None,
    content_reflection_max_attempts: int | None = None,
):
    model_provider = provider or MockProvider()
    max_attempts = configured_blueprint_max_attempts(blueprint_max_attempts)
    content_max_attempts = configured_content_reflection_max_attempts(content_reflection_max_attempts)

    def ingest(state: WorkflowState) -> dict[str, Any]:
        # Keep the legacy module-level seam so fixture tests and callers can
        # replace the parser without depending on this internal module.
        import annotation.workflow.graph as graph_module

        run_id = state.get("run_id", f"run-{uuid.uuid4().hex[:10]}")
        pdf_path = Path(state.get("pdf_path") or _default_pdf())
        source_document, blocks = graph_module.parse_pdf(pdf_path, run_id=run_id)
        warnings = graph_module.extraction_warnings(blocks)
        return {"run_id": run_id, "pdf_path": str(pdf_path), "source_document": source_document, "source_blocks": blocks, "source_refs": [block.source_ref for block in blocks], "warnings": warnings}

    def generate_blueprint_attempt(state: WorkflowState) -> dict[str, Any]:
        """Make exactly one Blueprint model call for this graph visit."""

        if state.get("blueprint_preloaded"):
            return {
                "blueprint_attempt": 0,
                "blueprint_generation_status": "succeeded",
                "blueprint_generation_prompt": "",
                "blueprint_generation_raw_output": "",
                "blueprint_generation_parsed_output": None,
                "blueprint_generation_error": "",
                "blueprint_generation_error_category": "",
                "blueprint_generation_retryable": False,
            }
        attempt = int(state.get("blueprint_attempt", 0)) + 1
        previous = state.get("blueprint")
        if attempt == 1:
            prompt = load_prompt(
                "load_or_create_blueprint",
                TEXTBOOK_CONTEXT=_source_context(state["source_blocks"]),
            )
            agent = "load_or_create_blueprint"
        else:
            check = state.get("blueprint_check")
            prompt = load_prompt(
                "revise_blueprint",
                TEXTBOOK_CONTEXT=_source_context(state["source_blocks"]),
                PREVIOUS_BLUEPRINT=json.dumps(previous.model_dump(mode="json") if previous is not None else {}, ensure_ascii=False, indent=2),
                CHECK_RESULT=json.dumps(check.model_dump(mode="json") if check is not None else {}, ensure_ascii=False, indent=2),
                REVIEW_ISSUES=json.dumps({
                    "issues": [issue.model_dump(mode="json") for issue in state.get("blueprint_feedback", [])],
                    "generation_error": state.get("blueprint_generation_error") or None,
                }, ensure_ascii=False, indent=2),
            )
            agent = "revise_blueprint"
        result: dict[str, Any] = {
            "blueprint_attempt": attempt,
            "blueprint_generation_prompt": prompt,
            "blueprint_generation_raw_output": "",
            "blueprint_generation_parsed_output": None,
            "blueprint_generation_error": "",
            "blueprint_generation_error_category": "",
            "blueprint_generation_retryable": False,
        }
        try:
            response = model_provider.generate_structured(StructuredGenerationRequest(
                prompt=prompt,
                schema=BlueprintDraft,
                max_output_tokens=3000,
                metadata={"agent": agent, "run_id": state["run_id"], "attempt": attempt},
            ))
            draft = BlueprintDraft.model_validate(response.value.model_dump(mode="python"))
            fixture_adaptation = (
                getattr(model_provider, "provider", "") == "mock"
                and bool(getattr(model_provider, "fixture_adaptation", False))
                and not bool(getattr(model_provider, "structured_payload", {}))
            )
            blueprint, adapted = _blueprint_from_draft(
                draft=draft,
                state=state,
                provider_name=response.provider,
                fixture_adaptation=fixture_adaptation,
            )
            result.update({
                "blueprint": blueprint,
                "blueprint_generation_status": "succeeded",
                "blueprint_generation_raw_output": response.raw_text,
                "blueprint_generation_parsed_output": draft.model_dump(mode="json"),
                "provider_metadata": {
                    **_metadata(response),
                    **({"fixture_adaptation": "empty_mock_source_refs"} if adapted else {}),
                },
            })
            return result
        except ProviderError as exc:
            category = getattr(exc, "category", "provider")
            parsed_output = getattr(exc, "parsed_output", None)
            if hasattr(parsed_output, "model_dump"):
                parsed_output = parsed_output.model_dump(mode="json")
            elif parsed_output is not None and not isinstance(parsed_output, dict):
                parsed_output = None
            result.update({
                "blueprint_generation_status": "schema_error" if category == "schema" else "provider_error",
                "blueprint_generation_raw_output": str(getattr(exc, "raw_output", "") or ""),
                "blueprint_generation_parsed_output": parsed_output,
                "blueprint_generation_error": str(exc),
                "blueprint_generation_error_category": category,
                "blueprint_generation_retryable": bool(getattr(exc, "retryable", False) or category == "schema"),
                "provider_metadata": _provider_metadata(model_provider),
            })
            return result
        except Exception as exc:
            result.update({
                "blueprint_generation_status": "provider_error",
                "blueprint_generation_error": str(exc),
                "blueprint_generation_error_category": "runtime",
                "provider_metadata": _provider_metadata(model_provider),
            })
            return result

    def check_blueprint_attempt(state: WorkflowState) -> dict[str, Any]:
        if state.get("blueprint_preloaded"):
            blueprint = state.get("blueprint")
            if blueprint is None:
                return {"blueprint_check": None, "blueprint_feedback": []}
            check = validate_blueprint(blueprint, valid_source_refs=set(state["source_refs"]))
        elif state.get("blueprint_generation_status") != "succeeded" or state.get("blueprint") is None:
            return {"blueprint_check": None, "blueprint_feedback": []}
        else:
            blueprint = state["blueprint"]
            check = validate_blueprint(blueprint, valid_source_refs=set(state["source_refs"]))
        blueprint.issues = list(check.issues)
        blueprint.status = "needs_revision" if check.status == "needs_revision" else "accepted"
        return {"blueprint_check": check, "blueprint_feedback": list(check.issues)}

    def route_blueprint(state: WorkflowState) -> dict[str, Any]:
        attempt = int(state.get("blueprint_attempt", 0))
        preloaded = bool(state.get("blueprint_preloaded"))
        status = state.get("blueprint_generation_status", "provider_error")
        check = state.get("blueprint_check")
        if preloaded:
            route = "accept" if check is not None and check.status == "accepted" else "block"
            stop_reason = "preloaded"
            final_status = "accepted" if route == "accept" else "blocked"
        elif status == "provider_error" and not state.get("blueprint_generation_retryable"):
            route, stop_reason, final_status = "fail", "non_retryable_provider_error", "failed"
        elif status != "succeeded" or check is None or check.status == "needs_revision":
            if attempt < max_attempts:
                route, stop_reason, final_status = "revise", None, None
            else:
                route, stop_reason, final_status = "block", "max_attempts", "blocked"
        else:
            route, stop_reason, final_status = "accept", "accepted", "accepted"

        attempts = list(state.get("blueprint_attempts", []))
        if not preloaded:
            attempts.append(BlueprintAttemptTrace(
                attempt=attempt,
                prompt=str(state.get("blueprint_generation_prompt", "")),
                raw_output=str(state.get("blueprint_generation_raw_output", "")),
                parsed_output=state.get("blueprint_generation_parsed_output"),
                generation_status=status if status in {"succeeded", "schema_error", "provider_error"} else "provider_error",
                error=state.get("blueprint_generation_error") or None,
                error_category=state.get("blueprint_generation_error_category") or None,
                provider_metadata=dict(state.get("provider_metadata", {})),
                check=check,
                feedback=list(state.get("blueprint_feedback", [])),
                route=route,
                stop_reason=stop_reason,
            ))
        result: dict[str, Any] = {"blueprint_attempts": attempts, "blueprint_route": route}
        if final_status is not None:
            trace = BlueprintLoopTrace(
                trace_id=str(state.get("blueprint_trace_id") or f"trace-{uuid.uuid4().hex[:12]}"),
                run_id=state["run_id"],
                max_attempts=max_attempts,
                attempts=attempts,
                final_status=final_status,
                final_attempt=attempt,
                stop_reason=stop_reason or "max_attempts",
                final_blueprint_artifact_id=state.get("blueprint").artifact_id if state.get("blueprint") else None,
            )
            result["blueprint_loop_trace"] = trace
            result["workflow_status"] = final_status
        return result

    def record_blocked_run(state: WorkflowState) -> dict[str, Any]:
        return {"workflow_status": "blocked"}

    def record_failed_run(state: WorkflowState) -> dict[str, Any]:
        error = state.get("blueprint_generation_error") or "Blueprint loop failed"
        return {"workflow_status": "failed", "errors": _merge_messages(state.get("errors"), [f"blueprint_loop_failed: {error}"])}

    def next_blueprint_route(state: WorkflowState) -> str:
        return state.get("blueprint_route", "fail")

    def plan_content_tasks(state: WorkflowState) -> dict[str, Any]:
        return {"content_tasks": _plan_content_tasks(state["run_id"], state["blueprint"])}

    def build_context_packs(state: WorkflowState) -> dict[str, Any]:
        blueprint = state["blueprint"]
        units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
        capabilities = getattr(model_provider, "capabilities", None)
        context_window = getattr(capabilities, "context_window", None)
        packs: list[ContextPack] = []
        warnings: list[str] = []
        for task in state.get("content_tasks", []):
            unit = units.get(task.knowledge_unit_id)
            if not unit:
                warnings.append(f"content_task_missing_unit: {task.task_id}")
                continue
            pack = build_context_pack(task, unit, blueprint, state["source_blocks"], context_window=context_window)
            packs.append(pack)
            if pack.omitted_source_refs:
                warnings.append(f"context_pack_omitted_sources:{task.task_id}:{','.join(pack.omitted_source_refs)}")
        return {"context_packs": packs, "warnings": _merge_messages(state.get("warnings"), warnings)}

    def content_reflection_loops(state: WorkflowState) -> dict[str, Any]:
        """Run one isolated reflection subgraph per knowledge unit in order."""

        blueprint = state["blueprint"]
        units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
        packs = {pack.task_id: pack for pack in state.get("context_packs", [])}
        subgraph = build_content_reflection_subgraph(
            model_provider,
            max_attempts=content_max_attempts,
        )
        artifacts: list[ContentArtifact] = []
        traces: list[ContentUnitLoopTrace] = []
        checks: dict[str, str] = {}
        warnings: list[str] = []
        provider_metadata: dict[str, Any] = dict(state.get("provider_metadata", {}))
        upstream_failed = False

        def preflight_trace(
            *,
            task: ContentTask,
            unit: KnowledgeUnit | None,
            pack: ContextPack | None,
            reason: Literal["context_pack_missing", "context_pack_source_over_budget", "upstream_failure"],
            message: str,
            final_status: Literal["blocked", "skipped"],
        ) -> tuple[ContentUnitLoopTrace, list[ContentArtifact]]:
            category: Literal["coverage", "source"] = "source" if reason == "context_pack_source_over_budget" else "coverage"
            issue = _content_issue(
                task=task,
                suffix=reason,
                category=category,
                severity="blocking",
                message=message,
                target_id=unit.artifact_id if unit is not None else task.knowledge_unit_id,
                source_refs=list(pack.omitted_source_refs) if pack is not None and reason == "context_pack_source_over_budget" else [],
            )
            candidate_artifacts: list[ContentArtifact] = []
            if unit is not None:
                candidate_artifacts.append(_blocked_content_artifact(
                    task=task,
                    unit=unit,
                    context_pack=pack,
                    run_id=state["run_id"],
                    provider_name=getattr(model_provider, "provider", "unknown"),
                    attempt=0,
                    error=message,
                    issues=[issue],
                ))
            check = ContentHardCheckResult(
                status="blocked",
                issues=[issue],
                checked_artifact_id=candidate_artifacts[0].artifact_id if candidate_artifacts else None,
            )
            trace_attempt = ContentAttemptTrace(
                attempt=0,
                generation_stage="skipped",
                generation_status="skipped",
                candidate_artifacts=candidate_artifacts,
                hard_check=check,
                critic_status="skipped",
                feedback=[issue],
                route="block" if final_status == "blocked" else "fail",
                stop_reason=reason,
            )
            trace = ContentUnitLoopTrace(
                trace_id=f"content-trace-{uuid.uuid4().hex[:12]}",
                run_id=state["run_id"],
                task_id=task.task_id,
                knowledge_unit_id=unit.artifact_id if unit is not None else task.knowledge_unit_id,
                context_pack_id=pack.context_pack_id if pack is not None else None,
                max_attempts=content_max_attempts,
                attempts=[trace_attempt],
                final_status=final_status,
                final_attempt=0,
                stop_reason=reason,
                final_content_artifact_ids=[artifact.artifact_id for artifact in candidate_artifacts],
            )
            return trace, candidate_artifacts

        for task in state.get("content_tasks", []):
            unit = units.get(task.knowledge_unit_id)
            pack = packs.get(task.task_id)
            task.status = "generating"
            if upstream_failed:
                trace, final_artifacts = preflight_trace(
                    task=task,
                    unit=unit,
                    pack=pack,
                    reason="upstream_failure",
                    message="前一知识单元发生不可重试 provider 错误，当前单元未执行。",
                    final_status="skipped",
                )
                task.status = "blocked"
                checks[task.task_id] = "failed"
                traces.append(trace)
                artifacts.extend(final_artifacts)
                continue
            if unit is None or pack is None:
                trace, final_artifacts = preflight_trace(
                    task=task,
                    unit=unit,
                    pack=pack,
                    reason="context_pack_missing",
                    message="当前知识单元缺少 ContextPack，未调用模型。",
                    final_status="blocked",
                )
                task.status = "blocked"
                checks[task.task_id] = "blocked"
                warnings.append(f"content_context_pack_missing:{task.task_id}")
                traces.append(trace)
                artifacts.extend(final_artifacts)
                continue
            if pack.omitted_source_refs:
                trace, final_artifacts = preflight_trace(
                    task=task,
                    unit=unit,
                    pack=pack,
                    reason="context_pack_source_over_budget",
                    message=f"ContextPack 缺少必需来源：{', '.join(pack.omitted_source_refs)}，未调用模型。",
                    final_status="blocked",
                )
                task.status = "blocked"
                checks[task.task_id] = "blocked"
                warnings.append(f"content_context_pack_source_over_budget:{task.task_id}:{','.join(pack.omitted_source_refs)}")
                traces.append(trace)
                artifacts.extend(final_artifacts)
                continue

            result = subgraph.invoke({
                "run_id": state["run_id"],
                "task": task,
                "unit": unit,
                "context_pack": pack,
                "valid_source_refs": list(state.get("source_refs", [])),
                "max_attempts": content_max_attempts,
                "fixture_adaptation": getattr(model_provider, "provider", "") == "mock",
                "attempts": [],
            })
            trace = result["content_loop_trace"]
            final_artifacts = list(result.get("final_artifacts", []))
            traces.append(trace)
            artifacts.extend(final_artifacts)
            if trace.final_status == "accepted":
                task.status = "accepted"
                checks[task.task_id] = "accepted"
            else:
                task.status = "blocked"
                checks[task.task_id] = "failed" if trace.final_status == "failed" else "blocked"
                warnings.append(f"content_reflection_{trace.final_status}:{task.task_id}:{trace.stop_reason}")
            if trace.final_status == "failed":
                upstream_failed = True
            if trace.attempts:
                last_attempt = trace.attempts[-1]
                metadata = last_attempt.generation_provider_metadata or last_attempt.critic_provider_metadata
                if metadata:
                    provider_metadata = dict(metadata)

        summary = _content_loop_summary(traces)
        status = summary["final_status"]
        result: dict[str, Any] = {
            "content_tasks": state.get("content_tasks", []),
            "content_artifacts": artifacts,
            "content_loop_traces": traces,
            "content_loop_summary": summary,
            "content_loop_status": status,
            "content_artifact_checks": checks,
            "provider_metadata": provider_metadata,
            "warnings": _merge_messages(state.get("warnings"), warnings),
        }
        # A blocked/failed content loop must be independently reconstructible;
        # successful runs write the same v2 content snapshot after quiz output
        # is available below.
        if status != "accepted":
            artifact_path = write_content_run_artifact(
                run_id=state["run_id"],
                blueprint_version=f"{blueprint.artifact_id}:v{blueprint.version}",
                tasks=state.get("content_tasks", []),
                context_packs=state.get("context_packs", []),
                artifacts=artifacts,
                quiz_artifacts=[],
                quiz_coverage=None,
                checks=checks,
                provider_metadata=provider_metadata,
                content_loop_traces=traces,
                content_loop_summary=summary,
                root=STORAGE_DIR / "artifacts" / "content",
            )
            result["content_artifact_path"] = str(artifact_path.resolve())
            result["content_loop_trace_path"] = str(artifact_path.resolve())
        return result

    def next_content_reflection_route(state: WorkflowState) -> str:
        status = state.get("content_loop_status", "failed")
        return "accept" if status == "accepted" else "block" if status == "blocked" else "fail"

    def record_content_blocked_run(state: WorkflowState) -> dict[str, Any]:
        return {"workflow_status": "blocked"}

    def record_content_failed_run(state: WorkflowState) -> dict[str, Any]:
        failed = [trace.task_id for trace in state.get("content_loop_traces", []) if trace.final_status == "failed"]
        message = f"content_reflection_failed: {', '.join(failed) or 'unknown'}"
        return {"workflow_status": "failed", "errors": _merge_messages(state.get("errors"), [message])}

    def generate_quiz_artifacts(state: WorkflowState) -> dict[str, Any]:
        """Retain T-007 as a separate post-content artifact boundary."""

        blueprint = state["blueprint"]
        units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
        packs = {pack.task_id: pack for pack in state.get("context_packs", [])}
        quiz_artifacts: list[QuizArtifact] = []
        checks = dict(state.get("content_artifact_checks", {}))
        warnings: list[str] = []
        for task in state.get("content_tasks", []):
            unit = units.get(task.knowledge_unit_id)
            pack = packs.get(task.task_id)
            if unit is None or pack is None:
                # This is unreachable after an accepted content-loop route,
                # but preserving a blocked quiz artifact keeps the independent
                # quiz contract auditable under malformed injected state.
                if unit is not None:
                    missing_pack = pack or ContextPack(
                        context_pack_id=f"ctx-{task.task_id}",
                        run_id=task.run_id,
                        task_id=task.task_id,
                        knowledge_unit_id=unit.artifact_id,
                        source_refs=[],
                        selected_source_refs=[],
                        excerpts=[],
                        estimated_input_tokens=0,
                        input_budget_tokens=0,
                    )
                    quiz_artifacts.append(blocked_quiz_artifact(
                        task=task,
                        unit=unit,
                        context_pack=missing_pack,
                        run_id=state["run_id"],
                        provider_name=getattr(model_provider, "provider", "unknown"),
                        error="ContextPack missing for quiz generation",
                    ))
                checks[task.task_id] = "blocked"
                task.status = "blocked"
                continue
            quiz_artifact = generate_quiz_artifact(
                model_provider,
                task=task,
                unit=unit,
                context_pack=pack,
                run_id=state["run_id"],
            )
            quiz_artifacts.append(quiz_artifact)
            if quiz_artifact.status != "accepted":
                task.status = "blocked"
                checks[task.task_id] = "blocked"
                warnings.append(f"quiz_generation_blocked:{task.task_id}")
        quiz_coverage = validate_quiz_coverage(
            quiz_artifacts,
            blueprint,
            context_packs={pack.context_pack_id: pack for pack in state.get("context_packs", [])},
            valid_source_refs=state.get("source_refs", []),
        )
        summary = dict(state.get("content_loop_summary", {}))
        artifact_path = write_content_run_artifact(
            run_id=state["run_id"],
            blueprint_version=f"{blueprint.artifact_id}:v{blueprint.version}",
            tasks=state.get("content_tasks", []),
            context_packs=state.get("context_packs", []),
            artifacts=state.get("content_artifacts", []),
            quiz_artifacts=quiz_artifacts,
            quiz_coverage=quiz_coverage,
            checks=checks,
            provider_metadata=state.get("provider_metadata", {}),
            content_loop_traces=state.get("content_loop_traces", []),
            content_loop_summary=summary,
            root=STORAGE_DIR / "artifacts" / "content",
        )
        return {
            "content_tasks": state.get("content_tasks", []),
            "quiz_artifacts": quiz_artifacts,
            "quiz_coverage_report": quiz_coverage,
            "content_artifact_checks": checks,
            "content_loop_summary": summary,
            "content_artifact_path": str(artifact_path.resolve()),
            "content_loop_trace_path": str(artifact_path.resolve()),
            "warnings": _merge_messages(state.get("warnings"), warnings),
        }

    def assemble_document_ir(state: WorkflowState) -> dict[str, Any]:
        artifacts = [artifact for artifact in state.get("content_artifacts", []) if artifact.status == "accepted"]
        quiz_artifacts = [artifact for artifact in state.get("quiz_artifacts", []) if artifact.status == "accepted"]
        if not artifacts and not quiz_artifacts:
            document = _fallback_document(state["run_id"], state["blueprint"], state["source_document"], state["source_blocks"], model_provider.provider)
            if state.get("document_id"):
                document.document_id = state["document_id"]
            return {"document": document}
        # Assemble from the accepted per-unit artifacts for both real and
        # deterministic providers.  The rich fixture remains available through
        # the dedicated demo endpoint, while workflow output now includes all
        # generated quiz items and their unit mapping.
        document = _assemble_document_from_artifacts(
            state["run_id"],
            state["blueprint"],
            artifacts,
            model_provider.provider,
            quiz_artifacts=quiz_artifacts,
        )
        if state.get("document_id"):
            document.document_id = state["document_id"]
        return {"document": document}

    def validate_document_ir(state: WorkflowState) -> dict[str, Any]:
        try:
            LearningDocument.model_validate(state["document"].model_dump())
            return {}
        except Exception as exc:
            return {"errors": [f"document_validation: {exc}"]}

    def review(state: WorkflowState) -> dict[str, Any]:
        document = state["document"]
        issues = list(document.issues)
        valid_refs = set(state["source_refs"])
        tasks = state.get("content_tasks", [])
        artifacts = state.get("content_artifacts", [])
        quiz_artifacts = state.get("quiz_artifacts", [])
        accepted_task_ids = {artifact.task_id for artifact in artifacts if artifact.status == "accepted" and artifact.task_id}
        if tasks and len(accepted_task_ids) != len(tasks):
            missing = [task.task_id for task in tasks if task.task_id not in accepted_task_ids]
            issues.append(ReviewIssue(issue_id="review-content-task-coverage", category="coverage", severity="blocking", message=f"有内容任务未产出可接受 artifact：{', '.join(missing)}", target_id=document.document_id))
        for artifact in artifacts:
            # Pedagogical Critic warnings are preserved on the accepted
            # artifact.  Bring them into the versioned review report instead
            # of treating a successful hard check as a clean release verdict.
            issues.extend(list(artifact.issues))
            if artifact.status == "blocked":
                issues.append(ReviewIssue(issue_id=f"review-blocked-content-{artifact.artifact_id}", category="coverage", severity="blocking", message="内容 artifact 被阻塞，不能进入发布文档。", target_id=artifact.artifact_id))
            if not set(artifact.source_refs).issubset(valid_refs):
                issues.append(ReviewIssue(issue_id=f"review-invalid-content-sources-{artifact.artifact_id}", category="source", severity="blocking", message="内容 artifact 包含无法回溯到本次教材导入的来源引用。", target_id=artifact.artifact_id))
        for artifact in quiz_artifacts:
            if artifact.status == "blocked":
                issues.append(ReviewIssue(
                    issue_id=f"review-blocked-quiz-{artifact.artifact_id}",
                    category="coverage",
                    severity="blocking",
                    message="题目 artifact 被阻塞，不能进入发布文档。",
                    target_id=artifact.artifact_id,
                    suggested_action="修订题目后重新生成并审核该知识单元。",
                ))
            if not set(artifact.source_refs).issubset(valid_refs):
                issues.append(ReviewIssue(
                    issue_id=f"review-invalid-quiz-sources-{artifact.artifact_id}",
                    category="source",
                    severity="blocking",
                    message="题目 artifact 包含无法回溯到本次教材导入的来源引用。",
                    target_id=artifact.artifact_id,
                    source_refs=[ref for ref in artifact.source_refs if ref not in valid_refs],
                ))
        quiz_coverage = state.get("quiz_coverage_report")
        if quiz_coverage is not None:
            issues.extend(list(getattr(quiz_coverage, "issues", []) or []))
        if not document.source_refs:
            issues.append(ReviewIssue(issue_id="review-missing-sources", category="source", severity="blocking", message="文档缺少教材来源引用。", target_id=document.document_id))
        elif not set(document.source_refs).issubset(valid_refs):
            issues.append(ReviewIssue(issue_id="review-invalid-sources", category="source", severity="blocking", message="文档包含无法回溯到本次教材导入的来源引用。", target_id=document.document_id))
        if not document.sections or not any(section.children for section in document.sections):
            issues.append(ReviewIssue(issue_id="review-empty-document", category="coverage", severity="blocking", message="文档没有可呈现内容。", target_id=document.document_id))
        if state.get("warnings"):
            issues.append(ReviewIssue(
                issue_id="review-generation-warning",
                category="uncertainty",
                severity="warning",
                layer="fact",
                message="；".join(state["warnings"]),
                target_id=document.document_id,
                suggested_action="对涉及的教材片段、公式候选和生成内容进行人工抽样复核。",
            ))
        # Keep one issue record per stable id when a quiz artifact and its
        # coverage report identify the same failure independently.
        deduped_issues: list[ReviewIssue] = []
        seen_issue_ids: set[str] = set()
        for issue in issues:
            if issue.issue_id in seen_issue_ids:
                continue
            seen_issue_ids.add(issue.issue_id)
            deduped_issues.append(issue)
        document.issues = deduped_issues
        document.status = "blocked" if any(issue.severity == "blocking" for issue in issues) else "accepted"
        report = _review_report_for(document, {**state, "source_refs": list(valid_refs)})
        document.review_report_id = report.report_id
        return {"document": document, "review_report": report, "review_report_path": report.artifact_path or ""}

    def assemble(state: WorkflowState) -> dict[str, Any]:
        document = state["document"]
        report = state.get("review_report")
        # Publication is authorized only by a passed review.  Warning-only
        # reports remain readable previews with the accepted document status;
        # blocked reports keep the document hidden by the API projection.
        if report is not None and report.status == "blocked":
            document.status = "blocked"
        elif not state.get("errors") and document.status == "accepted" and report is not None and report.status == "passed":
            document.status = "published"
        return {"document": document}

    graph = StateGraph(WorkflowState)
    for name, node in {
        "ingest": ingest,
        "generate_blueprint_attempt": generate_blueprint_attempt,
        "check_blueprint_attempt": check_blueprint_attempt,
        "route_blueprint": route_blueprint,
        "record_blocked_run": record_blocked_run,
        "record_failed_run": record_failed_run,
        "plan_content_tasks": plan_content_tasks,
        "build_context_packs": build_context_packs,
        "content_reflection_loops": content_reflection_loops,
        "record_content_blocked_run": record_content_blocked_run,
        "record_content_failed_run": record_content_failed_run,
        "generate_quiz_artifacts": generate_quiz_artifacts,
        "assemble_document_ir": assemble_document_ir,
        "validate_document_ir": validate_document_ir,
        "review": review,
        "assemble": assemble,
    }.items():
        graph.add_node(name, node)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "generate_blueprint_attempt")
    graph.add_edge("generate_blueprint_attempt", "check_blueprint_attempt")
    graph.add_edge("check_blueprint_attempt", "route_blueprint")
    graph.add_conditional_edges(
        "route_blueprint",
        next_blueprint_route,
        {
            "accept": "plan_content_tasks",
            "revise": "generate_blueprint_attempt",
            "block": "record_blocked_run",
            "fail": "record_failed_run",
        },
    )
    graph.add_edge("record_blocked_run", END)
    graph.add_edge("record_failed_run", END)
    graph.add_edge("plan_content_tasks", "build_context_packs")
    graph.add_edge("build_context_packs", "content_reflection_loops")
    graph.add_conditional_edges(
        "content_reflection_loops",
        next_content_reflection_route,
        {
            "accept": "generate_quiz_artifacts",
            "block": "record_content_blocked_run",
            "fail": "record_content_failed_run",
        },
    )
    graph.add_edge("record_content_blocked_run", END)
    graph.add_edge("record_content_failed_run", END)
    graph.add_edge("generate_quiz_artifacts", "assemble_document_ir")
    graph.add_edge("assemble_document_ir", "validate_document_ir")
    graph.add_edge("validate_document_ir", "review")
    graph.add_edge("review", "assemble")
    graph.add_edge("assemble", END)
    return graph.compile()


def run_minimal_workflow(
    *,
    provider: ModelProvider | None = None,
    run_id: str = "run-demo-001",
    blueprint: LearningBlueprint | None = None,
    pdf_path: str | Path | None = None,
    document_id: str | None = None,
    blueprint_max_attempts: int | None = None,
    content_reflection_max_attempts: int | None = None,
) -> WorkflowState:
    initial: WorkflowState = {"run_id": run_id}
    if blueprint:
        initial["blueprint"] = blueprint
        initial["blueprint_preloaded"] = True
    initial["blueprint_attempts"] = []
    initial["blueprint_trace_id"] = f"trace-{uuid.uuid4().hex[:12]}"
    if pdf_path:
        initial["pdf_path"] = str(pdf_path)
    if document_id:
        initial["document_id"] = document_id
    state = build_minimal_graph(
        provider,
        blueprint_max_attempts=blueprint_max_attempts,
        content_reflection_max_attempts=content_reflection_max_attempts,
    ).invoke(initial)
    # Keep the graph independently testable while ensuring API/CLI callers
    # can reconstruct the result without relying on process memory.
    persist_workflow_snapshots(state)
    return state
