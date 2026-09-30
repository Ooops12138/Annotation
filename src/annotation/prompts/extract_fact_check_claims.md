# Agent: extract_fact_check_claims

Extract factual assertions and explicit viewpoints that need checking from the
accepted explanation and quiz material. Return a `FactCheckClaimsDraft` JSON object.

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

Use supplied target IDs. Mark methodological, interpretive, evaluative, or
disputed viewpoints as `stance`; exclude intentionally wrong quiz distractors.
Select at most `{{MAX_CLAIMS}}` claims.

## Knowledge unit

```json
{{KNOWLEDGE_UNIT}}
```

## Checkable targets

```json
{{TARGETS}}
```
