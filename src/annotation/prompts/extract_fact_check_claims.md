# Agent: extract_fact_check_claims

Use the textbook's language and terminology for retrieval queries; avoid
translating Chinese textbook terms into English-only queries. Prioritize theorem
statements, summaries, equality conditions, domains, quantifiers and correct quiz
answers. Preserve the assertion exactly enough to expose missing hypotheses:
never silently repair an incorrect assertion while extracting it. Check optional
callouts as well as the main explanation. A mathematical assertion is a fact,
including a claimed generalization; it is not an interpretive stance.

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
