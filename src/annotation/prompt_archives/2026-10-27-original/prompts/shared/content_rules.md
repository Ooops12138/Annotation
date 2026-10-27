- Use only ContextPack evidence. Omit unsupported details and record uncertainty in `evidence_notes` with `claim`, `reason` and exact `source_refs`; do not silently strengthen ambiguous textbook statements. Do not put source IDs or internal diagnostics in learner-facing prose.
- `content` is the complete learner-facing Markdown explanation; examples, proofs and derivations belong there. Optional `callouts` are reserved for useful emphasis. Do not add quiz questions or a separate formula field.
- Follow the task's `depth_guidance`: `concise` covers the core objective and essential steps; `standard` develops the objectives with necessary reasoning and useful examples; `detailed` adds supported derivations, prerequisite bridges and examples where useful. Only assess and teach the supplied task objectives, not other objectives from a split source unit.
- Preserve domains, quantifiers, signs, nonzero hypotheses and equality conditions in proofs, summaries and callouts. Test zero and degenerate cases; an ordered-real proof does not establish a complex extension.

- Learning objectives contain `objective_id` and `text`. Teach their text, and use their IDs only for internal task alignment. Do not print objective IDs in learner-facing content.

{{NOTATION_CONTRACT}}
