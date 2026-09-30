# Agent: revise_content_for_fact_check

Revise the accepted Markdown explanation only to correct supplied direct
textbook contradictions. Return a valid `ContentDraft` JSON object.

Use the ContextPack as the evidence boundary, preserve valid source IDs, and
keep unaffected teaching material intact.

## Knowledge unit

```json
{{KNOWLEDGE_UNIT_CONTEXT}}
```

## ContextPack

{{CONTEXT_PACK}}

## Current candidate artifact

```json
{{CANDIDATE_ARTIFACT}}
```

## Direct textbook contradictions to repair

```json
{{FACT_CHECK_FEEDBACK}}
```
