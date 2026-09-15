# Agent: extract_fact_check_claims

Extract only the factual assertions and explicit viewpoints that need checking
from the supplied accepted explanation and quiz material. Return only JSON
matching `FactCheckClaimsDraft`; no Markdown fences or additional fields.

```json
{
  "claims": [
    {
      "target_id": "one supplied target ID",
      "kind": "fact|stance",
      "text": "one concise assertion copied or faithfully condensed from the target",
      "query": "a focused textbook search query"
    }
  ]
}
```

Use only supplied target IDs. A claim marked `stance` is a methodological,
interpretive, evaluative, or disputed viewpoint rather than a textbook fact.
Do not inspect intentionally wrong multiple-choice distractors. Select at most
`{{MAX_CLAIMS}}` claims. An empty list is valid when no checkable assertion is
present.

## Knowledge unit

```json
{{KNOWLEDGE_UNIT}}
```

## Checkable targets

```json
{{TARGETS}}
```
