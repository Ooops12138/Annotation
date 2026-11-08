"""Replay saved local outputs through current budgets; never call a network provider.

Relationship and coordination decisions are explicit synthetic fixtures. This
checks workload, coverage, pagination and convergence, not pedagogical correctness.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from annotation.domain.artifacts import SourceDocument, SourceBlock
from annotation.domain.textbook import TextbookStructure, BlueprintWindow
from annotation.prompt_archive import read_artifact_json
from annotation.prompt_loader import prompt_profile_scope
from annotation.providers.models import StructuredGenerationResponse
from annotation.workflow.blueprint_hierarchy import BlueprintEngine
from annotation.workflow.budget import PromptBudget


class ReplayProvider:
    provider = "offline-budget-replay"
    model = "saved-local-outputs"
    base_url = None
    config_version = "budget-replay-v1"

    def __init__(self, calls):
        self.locals = {c["scope_id"]: c["parsed_output"] for c in calls
            if c["phase"] == "generate_local_blueprint" and not c["error"]}

    def generate_structured(self, request):
        phase = request.metadata["agent"]
        if phase == "generate_local_blueprint":
            payload = self.locals[request.metadata["scope_id"]]
        elif phase == "resolve_blueprint_relations":
            # Deliberately synthetic: test bounded repair without claiming these
            # deletions are semantically valid or publishing this blueprint.
            payload = {"resolutions": [{"reference_id": r["reference_id"], "outcome": "remove",
                "reason": "仅验证预算和收敛的离线替身，不代表教材关系判断"}
                for r in request.context["relations"]]}
        elif phase == "coordinate_blueprint":
            payload = {"action": "finalize", "group_order": [g["group_id"] for g in request.context["groups"]],
                "summary": "离线预算回放：保留单元及顺序"}
        else:
            raise AssertionError(f"Unexpected phase: {phase}")
        value = request.schema.model_validate(payload)
        return StructuredGenerationResponse(value=value, raw_text=value.model_dump_json(),
            provider=self.provider, model=self.model, base_url=None, config_version=self.config_version,
            duration_ms=0, usage={})


def replay(directory: Path):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    saved = read_artifact_json(Path(manifest["paths"]["blueprint"]))
    source = json.loads(Path(manifest["paths"]["source_json"]).read_text(encoding="utf-8"))
    document = SourceDocument.model_validate(source["document"])
    document.run_id = "offline-budget-replay"
    blocks = [SourceBlock.model_validate(b) for b in source["blocks"]]
    structure = TextbookStructure.model_validate(saved["textbook_structure"])
    windows = [BlueprintWindow.model_validate(w["window"]) for w in saved["hierarchy_trace"]["windows"]]
    engine = BlueprintEngine(ReplayProvider(saved["hierarchy_trace"]["calls"]), document, blocks,
        structure, max_attempts=1, budget=PromptBudget(16000, 8000))
    engine.cache_enabled = False
    with prompt_profile_scope("mathematics"):
        blueprint = engine.run(windows)
    summary = {"kind": "offline-budget-replay-synthetic-relations", "network_calls": 0,
        "input_budget": engine.budget.input_tokens, "output_budget": engine.budget.output_tokens,
        "source_run": manifest["run_id"], "status": engine.trace.final_status,
        "stop_reason": engine.trace.stop_reason, "window_count": len(windows),
        "unit_count": len(blueprint.knowledge_units) if blueprint else 0,
        "relation_decisions": len([r for r in engine.trace.reference_resolutions if "outcome" in r]),
        "relation_calls": len([c for c in engine.trace.calls if c.phase == "resolve_blueprint_relations"]),
        "coordination_calls": len([c for c in engine.trace.calls if c.phase == "coordinate_blueprint"]),
        "max_estimated_input_tokens": max(c.estimated_input_tokens for c in engine.trace.calls),
        "budget_violations": sum(c.estimated_input_tokens > c.input_budget_tokens for c in engine.trace.calls),
        "limitation": "真实局部输出，关系与归并决策为替身；不证明模型教学判断质量，不发布文档"}
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay(args.directory)
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "accepted" and result["budget_violations"] == 0 else 1)
