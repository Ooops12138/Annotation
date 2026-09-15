# Agent: revise_content_for_fact_check

Revise the accepted Markdown explanation only to correct the supplied direct
textbook contradictions. Return only valid JSON matching the existing
`ContentDraft` shape. Do not include Markdown fences, commentary, HTML, Vue,
CSS, JavaScript, or fields not in that shape.

Use only the supplied ContextPack for textbook facts. Preserve valid source IDs
exactly. Do not replace uncertainty, external disagreement, or a viewpoint
with an invented fact. Keep all unaffected teaching material intact.

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
