"""Bounded local Blueprint agents and a hierarchical coordination/tool loop."""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from annotation.config import STORAGE_DIR
from annotation.domain.artifacts import KnowledgeUnit, LearningBlueprint, SourceBlock, SourceDocument
from annotation.domain.textbook import (
    BlueprintCallTrace, BlueprintWindow, HierarchicalBlueprintTrace,
    SourceSlice, TextbookStructure, WindowResult,
)
from annotation.prompt_loader import load_prompt
from annotation.providers import ModelProvider, ProviderError, StructuredGenerationRequest
from annotation.workflow.blueprint_checks import validate_blueprint
from annotation.workflow.budget import PromptBudget, positive_setting
from annotation.workflow.models import BlueprintDraft

COORDINATION_PROMPT_VERSION = "coordinate_blueprint:v3:optional-subject"
LOCAL_BLUEPRINT_PROMPT_VERSION = "generate_local_blueprint:v4:program-unit-ids"

class LocalBlueprintDraft(BlueprintDraft):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(default="", max_length=600)
    representative_unit_ids: list[str] = Field(default_factory=list, max_length=6)


class BlueprintToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["read_units", "search_units", "read_sources"]
    ids: list[str] = Field(default_factory=list, max_length=8)
    query: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0)


class UnitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str
    prerequisites: list[str] | None = None
    related_unit_ids: list[str] | None = None
    before_id: str | None = None


class UnitMerge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duplicate_id: str
    canonical_id: str
    reason: str = Field(min_length=1, max_length=600)


class CoordinationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["read", "finalize"]
    requests: list[BlueprintToolRequest] = Field(default_factory=list, max_length=3)
    group_order: list[str] = Field(default_factory=list)
    unit_updates: list[UnitUpdate] = Field(default_factory=list)
    merges: list[UnitMerge] = Field(default_factory=list)
    summary: str = Field(default="", max_length=600)
    representative_unit_ids: list[str] = Field(default_factory=list, max_length=6)


class PlanGroup(BaseModel):
    group_id: str
    title: str
    summary: str
    unit_ids: list[str]
    representative_unit_ids: list[str]


class HierarchyStop(RuntimeError):
    def __init__(self, message: str, *, failed: bool = False):
        super().__init__(message)
        self.failed = failed


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render_slices(slices: list[SourceSlice], blocks: dict[str, SourceBlock]) -> list[dict[str, Any]]:
    return [{"source_ref": s.source_ref, "start": s.start, "end": s.end,
             "text": blocks[s.source_ref].text[s.start:s.end]} for s in slices]


def window_prompt(window: BlueprintWindow, blocks: dict[str, SourceBlock], feedback: str = "") -> str:
    return load_prompt("generate_local_blueprint", WINDOW=_json({
        "window_id": window.window_id, "section_id": window.node_id, "path": window.path,
        "owned": render_slices(window.slices, blocks),
        "context_only": render_slices(window.context_slices, blocks),
    }), FEEDBACK=feedback)


def build_blueprint_windows(structure: TextbookStructure, blocks: list[SourceBlock], budget: PromptBudget) -> list[BlueprintWindow]:
    by_ref = {b.source_ref: b for b in blocks}
    nodes = {n.node_id: n for n in structure.nodes}
    windows: list[BlueprintWindow] = []
    # Reserve room for feedback and boundary context; the final request is
    # checked again including the actual schema. No original text is truncated.
    packing_budget = PromptBudget(max(0, budget.input_tokens - 3000), budget.output_tokens)
    context_budget = PromptBudget(max(0, budget.input_tokens - 1800), budget.output_tokens)
    for node in structure.nodes:
        path = [node.title]
        parent = node.parent_id
        while parent:
            path.insert(0, nodes[parent].title)
            parent = nodes[parent].parent_id
        current = BlueprintWindow(window_id="window-0000000000000000", node_id=node.node_id, path=path, slices=[])

        def emit() -> None:
            nonlocal current
            if not current.slices:
                return
            current.window_id = "window-" + hashlib.sha256(_json([node.node_id, [s.model_dump() for s in current.slices]]).encode()).hexdigest()[:16]
            windows.append(current)
            current = BlueprintWindow(window_id="window-0000000000000000", node_id=node.node_id, path=path, slices=[])

        for span in node.owned_ranges:
            for ref in structure.ordered_source_refs[span.start:span.end]:
                text = by_ref[ref].text
                start = 0
                while start < len(text):
                    low, high = start, len(text)
                    while low < high:
                        end = (low + high + 1) // 2
                        candidate = current.model_copy(update={"slices": [*current.slices, SourceSlice(source_ref=ref, start=start, end=end)]})
                        if packing_budget.fits(window_prompt(candidate, by_ref), LocalBlueprintDraft):
                            low = end
                        else:
                            high = end - 1
                    if low == start:
                        if current.slices:
                            emit()
                            continue
                        raise ProviderError("input budget cannot hold a source slice and local Blueprint schema", category="budget")
                    current.slices.append(SourceSlice(source_ref=ref, start=start, end=low))
                    start = low
                    if start < len(text):
                        emit()
        emit()
    # Windows stay in textbook order even when a parent owns an introduction.
    positions = {ref: i for i, ref in enumerate(structure.ordered_source_refs)}
    windows.sort(key=lambda w: (positions[w.slices[0].source_ref], w.slices[0].start))
    for index, window in enumerate(windows):
        neighbors: list[SourceSlice] = []
        if index:
            s = windows[index - 1].slices[-1]
            neighbors.append(SourceSlice(source_ref=s.source_ref, start=max(s.start, s.end - 160), end=s.end))
        if index + 1 < len(windows):
            s = windows[index + 1].slices[0]
            neighbors.append(SourceSlice(source_ref=s.source_ref, start=s.start, end=min(s.end, s.start + 160)))
        for s in neighbors:
            candidate = window.model_copy(update={"context_slices": [*window.context_slices, s]})
            if context_budget.fits(window_prompt(candidate, by_ref), LocalBlueprintDraft):
                window.context_slices.append(s)
    return windows


class BlueprintEngine:
    def __init__(self, provider: ModelProvider, document: SourceDocument, blocks: list[SourceBlock],
                 structure: TextbookStructure, *, max_attempts: int, budget: PromptBudget | None = None):
        self.provider = provider
        self.document = document
        self.blocks = {b.source_ref: b for b in blocks}
        self.structure = structure
        self.max_attempts = max_attempts
        self.budget = budget or PromptBudget.for_provider(provider)
        self.trace = HierarchicalBlueprintTrace()
        self.units: dict[str, KnowledgeUnit] = {}
        self.max_calls = positive_setting("BLUEPRINT_MAX_CALLS", 1000)
        self.request_count = 0
        self.tool_steps = positive_setting("BLUEPRINT_COORDINATION_MAX_STEPS", 6)
        self.cache_dir = Path(os.getenv("BLUEPRINT_CACHE_PATH", str(STORAGE_DIR / "cache" / "blueprint")))
        self.cache_enabled = os.getenv("BLUEPRINT_CACHE_ENABLED", "true").lower() not in {"false", "0", "no"}
        self.context_digest = hashlib.sha256(_json({ref: hashlib.sha256(b.text.encode()).hexdigest() for ref, b in self.blocks.items()}).encode()).hexdigest()

    def generate(self, phase: str, scope_id: str, schema: type[BaseModel],
                 make_prompt: Callable[[str], str], validate: Callable[[Any], None]) -> Any:
        feedback = ""
        for attempt in range(1, self.max_attempts + 1):
            prompt = make_prompt(feedback)
            record = BlueprintCallTrace(phase=phase, scope_id=scope_id, attempt=attempt, prompt=prompt,
                system_prompt="Return only a JSON object conforming to this schema:\n" + json.dumps(schema.model_json_schema()),
                estimated_input_tokens=self.budget.estimate(prompt, schema), input_budget_tokens=self.budget.input_tokens,
                output_budget_tokens=self.budget.output_tokens)
            self.trace.calls.append(record)
            response = None
            try:
                self.budget.check(prompt, schema)
                identity = {key: getattr(self.provider, key, None) for key in ("provider", "model", "base_url", "config_version")}
                context_fingerprint = None
                if phase == "coordinate_blueprint":
                    context_fingerprint = self.context_digest
                def cache_key(candidate_prompt: str) -> str:
                    return hashlib.sha256(_json({"version": 1, "identity": identity,
                        "prompt": candidate_prompt, "schema": schema.model_json_schema(), "output": self.budget.output_tokens,
                        "input_budget": self.budget.input_tokens,
                        "context": context_fingerprint}).encode()).hexdigest()
                record.cache_key = cache_key(prompt)
                cache = self.cache_dir / f"{record.cache_key}.json"
                value = None
                if self.cache_enabled:
                    try:
                        cached = json.loads(cache.read_text(encoding="utf-8"))
                        if cached["key"] != record.cache_key:
                            raise ValueError("cache key mismatch")
                        value = schema.model_validate(cached["value"])
                        validate(value)
                        record.raw_output = cached["raw_output"]
                        record.provider_metadata = {**cached["provider_metadata"], "usage": {}, "reused_usage": cached["provider_metadata"].get("usage", {})}
                        record.cache_hit = True
                        record.cached_prompt = cached.get("prompt")
                    except (OSError, ValueError, KeyError, TypeError, AttributeError):
                        value = None
                if value is None:
                    if self.request_count >= self.max_calls:
                        raise ProviderError("Blueprint call budget exhausted", category="budget")
                    self.request_count += 1
                    record.model_called = True
                    response = self.provider.generate_structured(StructuredGenerationRequest(
                        prompt=prompt, system_prompt=record.system_prompt, schema=schema, max_output_tokens=self.budget.output_tokens,
                        metadata={"agent": phase, "prompt_version": {
                            "generate_local_blueprint": LOCAL_BLUEPRINT_PROMPT_VERSION,
                            "coordinate_blueprint": COORDINATION_PROMPT_VERSION,
                        }.get(phase), "run_id": self.document.run_id, "scope_id": scope_id, "attempt": attempt}))
                    record.raw_output = response.raw_text
                    record.provider_metadata = {**identity, "duration_ms": response.duration_ms, "usage": dict(response.usage)}
                    value = schema.model_validate(response.value.model_dump(mode="python"))
                    record.parsed_output = value.model_dump(mode="json")
                    validate(value)
                    if self.cache_enabled:
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        # A corrected response also satisfies the original request.
                        # Cache it under that request so a resumed run need not
                        # repeat the initial invalid call just to recover feedback.
                        keys = {record.cache_key, cache_key(make_prompt(""))}
                        for key in keys:
                            destination = self.cache_dir / f"{key}.json"
                            temporary = destination.with_suffix(f".{uuid.uuid4().hex}.tmp")
                            temporary.write_text(_json({"key": key, "value": value.model_dump(mode="json"), "prompt": prompt,
                                "raw_output": record.raw_output, "provider_metadata": record.provider_metadata}), encoding="utf-8")
                            temporary.replace(destination)
                record.parsed_output = value.model_dump(mode="json")
                return value
            except Exception as exc:
                category = getattr(exc, "category", "schema" if isinstance(exc, (ValueError, ValidationError)) else "storage" if isinstance(exc, OSError) else "runtime")
                record.error = str(exc)[:1600]
                record.error_category = category
                if not record.raw_output:
                    record.raw_output = getattr(exc, "raw_output", "")
                retryable = category == "schema" or bool(getattr(exc, "retryable", False))
                record.route = "revise" if retryable and attempt < self.max_attempts else "block" if retryable or category == "budget" else "fail"
                if record.route != "revise":
                    raise HierarchyStop(f"{phase}:{scope_id}:{category}:{record.error}", failed=record.route == "fail") from exc
                feedback = record.error
        raise HierarchyStop("attempts exhausted")

    def _canonical(self, unit_id: str) -> str:
        while unit_id in self.trace.merges:
            unit_id = self.trace.merges[unit_id]
        return unit_id

    def local(self, window: BlueprintWindow) -> PlanGroup:
        def check(draft: LocalBlueprintDraft) -> None:
            if not draft.knowledge_units:
                if not draft.skip_reason or not draft.skip_reason.strip():
                    raise ValueError("empty local Blueprint requires a substantive skip_reason")
                return
            if draft.skip_reason:
                raise ValueError("skip_reason and knowledge_units are mutually exclusive")
            ids = [u.knowledge_unit_id or u.title for u in draft.knowledge_units]
            if len(ids) != len(set(ids)):
                raise ValueError("local unit IDs must be unique")
            evidence = {s.source_ref for s in [*window.slices, *window.context_slices]}
            if any(set(u.source_refs) - evidence for u in draft.knowledge_units):
                raise ValueError("local source_refs must come from this source window")
            if set(draft.representative_unit_ids) - set(ids):
                raise ValueError("unknown representative unit ID")
            if any(set(u.related_unit_ids) - set(ids) for u in draft.knowledge_units):
                raise ValueError("unknown local related unit ID")
            if any(not u.title.strip() or not u.learning_objectives for u in draft.knowledge_units):
                raise ValueError("units require a title and learning objectives")
            if any(not isinstance(o, str) for u in draft.knowledge_units for o in u.learning_objectives):
                raise ValueError("new local objectives must be text; the program assigns objective IDs")
            title_ids = {u.title: u.knowledge_unit_id or u.title for u in draft.knowledge_units}
            dependencies = {u.knowledge_unit_id or u.title: {title_ids.get(r, r) for r in u.prerequisites} & set(ids) for u in draft.knowledge_units}
            _topological_ids(ids, dependencies)

        draft = self.generate("generate_local_blueprint", window.window_id, LocalBlueprintDraft,
            lambda feedback: window_prompt(window, self.blocks, feedback), check)
        self.context_digest = hashlib.sha256((self.context_digest + _json(draft.model_dump())).encode()).hexdigest()
        namespace = window.window_id.removeprefix("window-")
        ids = {(u.knowledge_unit_id or u.title): f"ku-{namespace}-{index:04d}"
               for index, u in enumerate(draft.knowledge_units, start=1)}
        titles = {u.title: ids[u.knowledge_unit_id or u.title] for u in draft.knowledge_units}
        unit_ids: list[str] = []
        for item in draft.knowledge_units:
            unit_id = ids[item.knowledge_unit_id or item.title]
            self.units[unit_id] = KnowledgeUnit(artifact_id=unit_id, run_id=self.document.run_id,
                version=1, status="accepted", created_by=f"provider:{self.provider.provider}", title=item.title,
                kind=item.kind, learning_objectives=item.learning_objectives, source_refs=item.source_refs,
                prerequisites=[ids.get(r, titles.get(r, r)) for r in item.prerequisites],
                related_unit_ids=[ids.get(r, titles.get(r, r)) for r in item.related_unit_ids],
                structure_node_ids=[window.node_id])
            unit_ids.append(unit_id)
        record = next(r for r in self.trace.windows if r.window.window_id == window.window_id)
        record.status = "accepted" if unit_ids else "skipped"
        record.unit_ids = unit_ids
        record.skip_reason = draft.skip_reason
        representatives = [ids[r] for r in draft.representative_unit_ids]
        return PlanGroup(group_id=window.window_id, title=" / ".join(window.path),
            summary=draft.summary or draft.title, unit_ids=unit_ids,
            representative_unit_ids=representatives or unit_ids[:1])

    def _card(self, group: PlanGroup) -> dict[str, Any]:
        representatives = list(dict.fromkeys(self._canonical(u) for u in group.representative_unit_ids))
        return {"group_id": group.group_id, "title": group.title, "summary": group.summary,
            "unit_count": len(group.unit_ids), "representatives": [{"unit_id": u, "title": self.units[u].title}
            for u in representatives if u in self.units]}

    def tool(self, request: BlueprintToolRequest) -> dict[str, Any]:
        result: dict[str, Any] = {"tool": request.tool, "items": []}
        if request.tool == "search_units":
            query = request.query.casefold().strip()
            if not query:
                return {**result, "error": "query is required"}
            terms = query.split()
            ranked = sorted(self.units.values(), key=lambda u: sum(t in (u.title + " " + " ".join(u.objective_texts)).casefold() for t in terms), reverse=True)
            matching = [u for u in ranked if any(t in (u.title + " " + " ".join(u.objective_texts)).casefold() for t in terms)]
            selected = matching[request.offset:request.offset + 5]
            result["items"] = [{"unit_id": u.artifact_id, "title": u.title, "structure_node_ids": u.structure_node_ids} for u in selected]
            result["next_offset"] = request.offset + len(selected) if request.offset + len(selected) < len(matching) else None
        elif request.tool == "read_units":
            for unit_id in request.ids:
                unit_id = self._canonical(unit_id)
                result["items"].append(self.units[unit_id].model_dump(mode="json") if unit_id in self.units else {"unit_id": unit_id, "error": "unknown_unit"})
        else:
            for ref in request.ids:
                if ref not in self.blocks:
                    result["items"].append({"source_ref": ref, "error": "unknown_source"})
                    continue
                text = self.blocks[ref].text
                end = min(len(text), request.offset + 600)
                result["items"].append({"source_ref": ref, "start": request.offset, "text": text[request.offset:end],
                    "next_offset": end if end < len(text) else None})
        return result

    def coordinate(self, groups: list[PlanGroup], scope_id: str) -> PlanGroup:
        cards = [self._card(g) for g in groups]
        scope_units = {self._canonical(u) for g in groups for u in g.unit_ids}
        tool_result: Any = []
        history: list[dict[str, Any]] = []

        def check(decision: CoordinationDecision) -> None:
            if decision.action == "read":
                if not decision.requests:
                    raise ValueError("read requires tool requests")
                return
            expected = [g.group_id for g in groups]
            if len(decision.group_order) != len(expected) or set(decision.group_order) != set(expected):
                raise ValueError(f"group_order must be an exact permutation of {expected}")
            if set(decision.representative_unit_ids) - scope_units:
                raise ValueError("representative units must belong to this scope")
            if len({u.unit_id for u in decision.unit_updates}) != len(decision.unit_updates):
                raise ValueError("duplicate unit update")
            for update in decision.unit_updates:
                if update.unit_id not in scope_units:
                    raise ValueError("update target outside this scope")
                if update.before_id and (update.before_id not in scope_units or update.before_id == update.unit_id):
                    raise ValueError("before_id must be another unit in this scope")
                for ref in [*(update.prerequisites or []), *(update.related_unit_ids or [])]:
                    if self._canonical(ref) not in self.units:
                        raise ValueError(f"unknown relationship target: {ref}; use tools to resolve it")
                if update.prerequisites and update.unit_id in update.prerequisites:
                    raise ValueError("a unit cannot be its own prerequisite")
            removed: set[str] = set()
            for merge in decision.merges:
                if merge.duplicate_id not in scope_units or merge.canonical_id not in scope_units or merge.duplicate_id == merge.canonical_id:
                    raise ValueError("merge must name two different units in this scope")
                if merge.duplicate_id in removed or merge.canonical_id in removed or any(m.canonical_id == merge.duplicate_id for m in decision.merges):
                    raise ValueError("merge chains/duplicate targets require separate coordination steps")
                removed.add(merge.duplicate_id)
            # Validate the candidate transaction before any mutation or cache write.
            candidate = {k: v.model_copy(deep=True) for k, v in self.units.items()}
            for update in decision.unit_updates:
                if update.prerequisites is not None:
                    candidate[update.unit_id].prerequisites = [self._canonical(r) for r in update.prerequisites]
                if update.related_unit_ids is not None:
                    candidate[update.unit_id].related_unit_ids = [self._canonical(r) for r in update.related_unit_ids]
            aliases = {m.duplicate_id: m.canonical_id for m in decision.merges}
            for merge in decision.merges:
                canonical = candidate[merge.canonical_id]
                duplicate = candidate.pop(merge.duplicate_id)
                canonical.prerequisites = list(dict.fromkeys([*canonical.prerequisites, *duplicate.prerequisites]))
                canonical.related_unit_ids = list(dict.fromkeys([*canonical.related_unit_ids, *duplicate.related_unit_ids]))
            for unit in candidate.values():
                unit.prerequisites = list(dict.fromkeys(aliases.get(r, self._canonical(r)) for r in unit.prerequisites if aliases.get(r, self._canonical(r)) != unit.artifact_id))
            unresolved = {r for unit_id in scope_units - removed for r in [*candidate[unit_id].prerequisites, *candidate[unit_id].related_unit_ids]
                if aliases.get(r, self._canonical(r)) not in candidate}
            if unresolved:
                raise ValueError(f"resolve named relationships in this scope before finalize: {sorted(unresolved)}; use search_units/read_units")
            _dependency_order(list(candidate), candidate, allow_unresolved=True)

        for step in range(self.tool_steps):
            decision = self.generate("coordinate_blueprint", f"{scope_id}:step-{step + 1}", CoordinationDecision,
                lambda feedback: load_prompt("coordinate_blueprint", GROUPS=_json(cards), TOOL_RESULT=_json(tool_result), FEEDBACK=feedback), check)
            if decision.action == "read":
                tool_result = []
                for request in decision.requests:
                    result = self.tool(request)
                    self.trace.tool_calls.append({"scope_id": scope_id, "step": step + 1, "request": request.model_dump(), "result": result})
                    tool_result.append(result)
                # Tool results cannot overflow the next request. Return an explicit
                # narrowing request, never silently truncate source evidence.
                prompt = load_prompt("coordinate_blueprint", GROUPS=_json(cards), TOOL_RESULT=_json(tool_result), FEEDBACK="")
                if not self.budget.fits(prompt, CoordinationDecision):
                    tool_result = {"error": "tool_result_over_budget; request fewer IDs or use read_sources with offsets"}
                # Retain earlier useful reads while they fit. Dropping the first
                # search result after a subsequent source read would erase the
                # very ID the agent needed to resolve a cross-chapter relation.
                current = tool_result if isinstance(tool_result, list) else [tool_result]
                candidate_history = [*history, *current]
                prompt = load_prompt("coordinate_blueprint", GROUPS=_json(cards), TOOL_RESULT=_json(candidate_history), FEEDBACK="")
                if self.budget.fits(prompt, CoordinationDecision):
                    history = candidate_history
                else:
                    history = [{"notice": "earlier tool results omitted to respect budget; reread if needed"}, *current]
                tool_result = history
                continue
            ordered = list(dict.fromkeys(self._canonical(u) for group_id in decision.group_order for g in groups if g.group_id == group_id for u in g.unit_ids))
            for update in decision.unit_updates:
                unit = self.units[update.unit_id]
                if update.prerequisites is not None:
                    unit.prerequisites = [self._canonical(r) for r in update.prerequisites]
                if update.related_unit_ids is not None:
                    unit.related_unit_ids = [self._canonical(r) for r in update.related_unit_ids]
                if update.before_id:
                    ordered.remove(update.unit_id)
                    ordered.insert(ordered.index(update.before_id), update.unit_id)
            for merge in decision.merges:
                duplicate = self.units.pop(merge.duplicate_id)
                canonical = self.units[merge.canonical_id]
                for field in ("source_refs", "structure_node_ids", "prerequisites", "related_unit_ids"):
                    setattr(canonical, field, list(dict.fromkeys([*getattr(canonical, field), *getattr(duplicate, field)])))
                canonical.learning_objectives = [*canonical.learning_objectives, *duplicate.learning_objectives]
                self.trace.merges[merge.duplicate_id] = merge.canonical_id
            for unit in self.units.values():
                for field in ("prerequisites", "related_unit_ids"):
                    setattr(unit, field, list(dict.fromkeys(self._canonical(r) for r in getattr(unit, field) if self._canonical(r) != unit.artifact_id)))
            ordered = list(dict.fromkeys(self._canonical(u) for u in ordered))
            group = PlanGroup(group_id=scope_id, title=groups[0].title if len(groups) == 1 else self.document.title,
                summary=decision.summary, unit_ids=ordered,
                representative_unit_ids=list(dict.fromkeys(self._canonical(u) for u in decision.representative_unit_ids)) or ordered[:1])
            self.trace.groups.append({"scope_id": scope_id, "children": decision.group_order, "decision": decision.model_dump(mode="json"), "result": group.model_dump()})
            self.context_digest = hashlib.sha256((self.context_digest + _json(decision.model_dump())).encode()).hexdigest()
            return group
        raise HierarchyStop(f"coordination_tool_steps_exhausted:{scope_id}")

    def run(self, windows: list[BlueprintWindow]) -> LearningBlueprint | None:
        self.trace.windows = [WindowResult(window=w) for w in windows]
        try:
            if not windows:
                raise HierarchyStop("no_extracted_text")
            if len({w.window_id for w in windows}) != len(windows):
                raise HierarchyStop("duplicate_source_window")
            # Verify input coverage independently of model-produced units.
            # Context overlap never counts as ownership of omitted source.
            spans: dict[str, list[tuple[int, int]]] = {}
            owners = {ref: node.node_id for node in self.structure.nodes for span in node.owned_ranges
                      for ref in self.structure.ordered_source_refs[span.start:span.end]}
            for window in windows:
                for span in window.slices:
                    if owners.get(span.source_ref) != window.node_id:
                        raise HierarchyStop(f"invalid_window_owner:{span.source_ref}")
                    spans.setdefault(span.source_ref, []).append((span.start, span.end))
            for ref in self.structure.ordered_source_refs:
                end = 0
                for start, stop in sorted(spans.get(ref, [])):
                    if start != end or stop <= start or stop > len(self.blocks[ref].text):
                        raise HierarchyStop(f"source_window_gap_or_overlap:{ref}")
                    end = stop
                if end != len(self.blocks[ref].text):
                    raise HierarchyStop(f"source_window_missing:{ref}")
            groups = []
            for window in windows:
                try:
                    groups.append(self.local(window))
                except HierarchyStop as exc:
                    record = next(r for r in self.trace.windows if r.window.window_id == window.window_id)
                    record.status = "failed" if exc.failed else "blocked"
                    raise
            if not self.units:
                raise HierarchyStop("all_windows_skipped_no_learning_units")
            group_counter = 0
            coordination_count = 0

            def reduce_groups(children: list[PlanGroup], title: str) -> PlanGroup:
                nonlocal group_counter, coordination_count
                groups = children
                while len(groups) > 1:
                    batches: list[list[PlanGroup]] = []
                    for group in groups:
                        if not batches:
                            batches.append([])
                        candidate = [*batches[-1], group]
                        prompt = load_prompt("coordinate_blueprint", GROUPS=_json([self._card(g) for g in candidate]), TOOL_RESULT="[]", FEEDBACK="")
                        if len(candidate) > 4 or not PromptBudget(max(0, self.budget.input_tokens - 1800), self.budget.output_tokens).fits(prompt, CoordinationDecision):
                            batches.append([group])
                        else:
                            batches[-1].append(group)
                    if len(batches) >= len(groups):
                        raise HierarchyStop("coordination_budget_cannot_reduce_groups")
                    next_groups = []
                    for batch in batches:
                        if len(batch) == 1:
                            next_groups.append(batch[0])
                        else:
                            group_counter += 1
                            coordination_count += 1
                            next_groups.append(self.coordinate(batch, f"group-{group_counter}"))
                    groups = next_groups
                result = groups[0]
                result.title = title
                return result

            # Assemble actual textbook siblings first, then chapters, then the
            # book. Execution windows never become fabricated textbook sections.
            local_by_window = {g.group_id: g for g in groups}
            positions = {ref: i for i, ref in enumerate(self.structure.ordered_source_refs)}
            node_groups: dict[str, PlanGroup] = {}
            windows_by_node: dict[str, list[BlueprintWindow]] = {}
            children_by_node: dict[str, list[Any]] = {}
            for window in windows:
                windows_by_node.setdefault(window.node_id, []).append(window)
            for node in self.structure.nodes:
                if node.parent_id:
                    children_by_node.setdefault(node.parent_id, []).append(node)
            for node in reversed(self.structure.nodes):
                located = [(positions[w.slices[0].source_ref], w.slices[0].start, local_by_window[w.window_id])
                    for w in windows_by_node.get(node.node_id, [])]
                located.extend((child.start, 0, node_groups[child.node_id]) for child in children_by_node.get(node.node_id, [])
                    if child.node_id in node_groups)
                located.sort(key=lambda value: (value[0], value[1]))
                if located:
                    node_groups[node.node_id] = reduce_groups([value[2] for value in located], node.title)
            groups = [node_groups[self.structure.nodes[0].node_id]]
            # A single group still needs a chance to resolve named relations.
            if coordination_count == 0:
                groups = [self.coordinate(groups, "group-final")]
            ordered = _dependency_order(groups[0].unit_ids, self.units)
            for record in self.trace.windows:
                for original_id in record.unit_ids:
                    unit_id = self._canonical(original_id)
                    if unit_id not in ordered or record.window.node_id not in self.units[unit_id].structure_node_ids:
                        raise HierarchyStop(f"window_unit_lost:{record.window.window_id}")
            blueprint = LearningBlueprint(artifact_id=f"bp-{uuid.uuid4().hex[:12]}", run_id=self.document.run_id,
                version=1, status="accepted", created_by=f"provider:{self.provider.provider}", title=self.document.title,
                source_refs=self.structure.ordered_source_refs, knowledge_units=[self.units[u] for u in ordered])
            check = validate_blueprint(blueprint, valid_source_refs=set(self.blocks))
            if check.status != "accepted":
                raise HierarchyStop("final_blueprint_invalid:" + ";".join(i.message for i in check.issues))
            blueprint.issues = check.issues
            self.trace.final_status = "accepted"
            self.trace.stop_reason = "all_windows_accounted_and_coordinated"
            return blueprint
        except (HierarchyStop, ValueError) as exc:
            self.trace.final_status = "failed" if getattr(exc, "failed", False) else "blocked"
            self.trace.stop_reason = str(exc)
            return None


def _dependency_order(order: list[str], units: dict[str, KnowledgeUnit], *, allow_unresolved: bool = False) -> list[str]:
    # Stable topological assembly respects the dependencies selected by the LLM;
    # it never invents pedagogical dependencies from textbook order.
    incoming = {u: set(units[u].prerequisites) & units.keys() for u in units}
    if not allow_unresolved:
        unknown = {r for u in units.values() for r in [*u.prerequisites, *u.related_unit_ids] if r not in units}
        if unknown:
            raise ValueError(f"unresolved relationships: {sorted(unknown)}")
    return _topological_ids(list(dict.fromkeys([*order, *units])), incoming)


def _topological_ids(order: list[str], incoming: dict[str, set[str]]) -> list[str]:
    ranks = {unit_id: index for index, unit_id in enumerate(order)}
    successors: dict[str, list[str]] = {u: [] for u in incoming}
    for unit_id, dependencies in incoming.items():
        for dependency in dependencies:
            successors[dependency].append(unit_id)
    ready = [(ranks[u], u) for u, dependencies in incoming.items() if not dependencies]
    heapq.heapify(ready)
    result: list[str] = []
    while ready:
        _, unit_id = heapq.heappop(ready)
        result.append(unit_id)
        for successor in successors[unit_id]:
            incoming[successor].remove(unit_id)
            if not incoming[successor]:
                heapq.heappush(ready, (ranks[successor], successor))
    if len(result) != len(incoming):
        raise ValueError("cyclic prerequisites")
    return result
