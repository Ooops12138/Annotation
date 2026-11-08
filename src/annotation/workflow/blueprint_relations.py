"""Budgeted relationship repair, separate from hierarchical group ordering."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from annotation.prompt_loader import load_prompt
from annotation.workflow.budget import PromptBudget, encoded_size, preview_text
from annotation.workflow.blueprint_references import normalized_title, normalize_relationships


class BlueprintToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["read_units", "search_units", "read_sources", "read_boundaries"]
    ids: list[str] = Field(default_factory=list, max_length=8)
    query: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0)


class RelationResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_id: str
    outcome: Literal["internal", "external", "remove"]
    target_id: str | None = None
    reason: str = Field(min_length=1, max_length=240)


class RelationBatchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["read", "apply"] = "apply"
    requests: list[BlueprintToolRequest] = Field(default_factory=list, max_length=3)
    resolutions: list[RelationResolution] = Field(default_factory=list, max_length=32)


class ApplyRelationBatchDecision(RelationBatchDecision):
    action: Literal["apply"] = "apply"


def repair_relationships(engine, unit_ids: list[str]) -> None:
    """Each accepted call consumes pending references; invalid batches are atomic."""
    from annotation.workflow.blueprint_hierarchy import HierarchyStop, _dependency_order
    engine.trace.reference_resolutions.extend(normalize_relationships(engine.units))
    pending = engine._unresolved(unit_ids, include_candidates=False)
    sequence = 0
    while pending:
        sequence += 1
        batch = {}
        views = []
        # Bound both the number of output records and their estimated size.
        limit = min(32, max(1, engine.budget.output_tokens // 512))
        for relation in pending[:limit]:
            key = hashlib.sha256(json.dumps(relation, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
            candidates = engine.tool(BlueprintToolRequest(tool="search_units", query=relation["reference"][:200]))["items"]
            unit = engine.units[relation["unit_id"]]
            view = {**relation, "reference_id": key, "reference": preview_text(relation["reference"], 160),
                "title": preview_text(unit.title, 60), "objectives": [preview_text(s, 80) for s in unit.objective_texts[:3]],
                "source_refs": unit.source_refs[:4],
                "candidates": [c for c in candidates if c["unit_id"] != unit.artifact_id][:3]}
            prompt = load_prompt("resolve_blueprint_relations", RELATIONS=json.dumps([*views, view], ensure_ascii=False), TOOL_RESULT="[]", FEEDBACK="")
            # Keep feedback room; output size is bounded independently from input.
            allowance = PromptBudget(max(0, engine.budget.input_tokens - min(3072, engine.budget.input_tokens // 3)), engine.budget.output_tokens)
            output = {"resolutions": [{"reference_id": k, "outcome": "internal", "target_id": "x" * 80,
                "reason": "说明" * 120} for k in [*batch, key]]}
            if not allowance.fits(prompt, RelationBatchDecision) or encoded_size(json.dumps(output, ensure_ascii=False)) + 128 > engine.budget.output_tokens:
                break
            batch[key] = relation
            views.append(view)
        if not batch:
            raise HierarchyStop(f"relation_batch_over_budget:{pending[0]['unit_id']}:input={engine.budget.input_tokens}:output={engine.budget.output_tokens}")

        def candidate_for(decision):
            candidate = {key: u.model_copy(deep=True) for key, u in engine.units.items()}
            seen = set()
            for resolution in decision.resolutions:
                if not resolution.reason.strip():
                    raise ValueError("relationship resolution requires a substantive reason")
                key = resolution.reference_id
                if key not in batch or key in seen:
                    raise ValueError("resolution must name a unique pending reference_id")
                seen.add(key)
                relation = batch[key]
                owner = candidate[relation["unit_id"]]
                refs = getattr(owner, relation["field"])
                if relation["reference"] not in refs:
                    raise ValueError("reference is no longer pending")
                refs.remove(relation["reference"])
                if resolution.outcome == "internal":
                    target = engine._canonical(resolution.target_id or "")
                    if target not in candidate or target == owner.artifact_id:
                        raise ValueError("internal resolution requires another existing stable unit ID")
                    refs.append(target)
                elif resolution.target_id is not None:
                    raise ValueError("external/remove cannot specify target_id")
                elif resolution.outcome == "external":
                    if relation["field"] != "prerequisites":
                        raise ValueError("only prerequisites may be external")
                    ref = relation["reference"]
                    if ref.startswith(("ku-", "window-", "group-")) or ref in candidate or any(normalized_title(u.title) == normalized_title(ref) for u in candidate.values()):
                        raise ValueError("an existing or ambiguous internal reference cannot be classified as external")
                    owner.external_prerequisites = list(dict.fromkeys([*owner.external_prerequisites, ref]))
                setattr(owner, relation["field"], list(dict.fromkeys(refs)))
            _dependency_order(list(candidate), candidate, allow_unresolved=True)
            return candidate

        def validate(decision):
            if decision.action == "read":
                if not decision.requests or decision.resolutions:
                    raise ValueError("read requires requests and no resolutions")
                if any(r.tool == "read_boundaries" for r in decision.requests):
                    raise ValueError("relationship repair cannot read group boundaries")
            else:
                if not decision.resolutions or decision.requests:
                    raise ValueError("apply requires resolutions and no requests")
                candidate_for(decision)

        history = []
        seen = set()
        for step in range(engine.tool_steps):
            make_prompt = lambda feedback: load_prompt("resolve_blueprint_relations",
                RELATIONS=json.dumps(views, ensure_ascii=False), TOOL_RESULT=json.dumps(history, ensure_ascii=False), FEEDBACK=feedback)
            schema = ApplyRelationBatchDecision if step == engine.tool_steps - 1 else RelationBatchDecision
            decision = engine.generate("resolve_blueprint_relations", f"relations-{len(engine.trace.reference_resolutions)}-{sequence}:step-{step + 1}",
                schema, make_prompt, validate, context={"relations": views})
            if decision.action == "apply":
                break
            history = engine._read_history(decision.requests, history, seen,
                lambda evidence: load_prompt("resolve_blueprint_relations", RELATIONS=json.dumps(views, ensure_ascii=False),
                    TOOL_RESULT=json.dumps(evidence, ensure_ascii=False), FEEDBACK=""),
                RelationBatchDecision, f"relations-{sequence}", step)
        else:
            raise HierarchyStop(f"relation_tool_steps_exhausted:{pending[0]['unit_id']}")
        engine.units = candidate_for(decision)
        engine.context_digest = hashlib.sha256((engine.context_digest + decision.model_dump_json()).encode()).hexdigest()
        for resolution in decision.resolutions:
            relation = batch[resolution.reference_id]
            engine.trace.reference_resolutions.append({"unit_id": relation["unit_id"], "field": relation["field"],
                "original": relation["reference"], "outcome": resolution.outcome,
                "resolved": resolution.target_id or "", "reason": resolution.reason})
        updated = engine._unresolved(unit_ids, include_candidates=False)
        if len(updated) >= len(pending):
            raise HierarchyStop("relation_repair_no_progress")
        pending = updated
