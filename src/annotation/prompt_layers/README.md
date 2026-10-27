# Prompt layer migration

This directory is the additive layer for the prompt migration.

- `manifest.json` declares profiles and which slots each agent may read.
- Profile rules are editable Markdown and are injected only into prompts that declare the slot in the manifest.
- Existing shared contracts and Pydantic-generated output schemas remain owned by `src/annotation/prompt_loader.py`.
- `profile=None` uses an agent's manifest default. `profile=""` disables profile injection for comparison or rollback testing.
- This layer is currently piloted by the content authoring, quiz, retrieval, and
  fact-check agents. The two content authoring agents read the mathematics
  profile; the quiz author and fact-check quiz correction read only the separate
  `mathematics_quiz` domain-rules profile; the three retrieval agents read only
  the generic `retrieval` profile; and the claim extractor, fact-check revision
  agent, and fact-check assessor read only the separate
  `mathematics_fact_check` domain-rules profile. Evidence assignment and
  verdict normalization remain workflow-owned contracts. The blueprint
  coordinator reads only the generic `coordination` `EVIDENCE_RULES` profile;
  tool protocol remains core/workflow owned. The content critic reads only
  `TEACHING_GUIDANCE` from the mathematics profile; issue routing and evidence
  boundaries remain core/workflow owned.
- The local blueprint, blueprint loader/reviser and course architect read only
  `TEACHING_GUIDANCE` from the separate `blueprint` profile. Blueprint schema,
  objective identity, window bounds, coverage and skip policies remain
  core/workflow contracts.
- Interactive component generation and revision read no profile. Renderer and
  sandbox capabilities, component schema, source bindings and revision routing
  remain core/workflow contracts.
- `migration_plan.json` records completed migrations and candidate agents. A
  candidate is not enabled in the runtime manifest until its open choice is
  confirmed.
