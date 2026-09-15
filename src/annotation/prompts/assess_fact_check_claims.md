# Agent: assess_fact_check_claims

Assess each supplied claim against the supplied evidence. Return only JSON
matching `FactCheckAssessmentsDraft`; no Markdown fences or additional fields.

```json
{
  "assessments": [
    {
      "claim_id": "one supplied claim ID",
      "verdict": "supported|contradicted|insufficient|external_conflict|stance",
      "judgement": "short evidence-grounded explanation",
      "sufficient_textbook_evidence": false
    }
  ]
}
```

Use `contradicted` only when the current textbook evidence directly and
unambiguously contradicts the claim. Set `sufficient_textbook_evidence` true
only for that direct, locatable textbook contradiction. Missing evidence is
`insufficient`; an external disagreement is `external_conflict`; an
interpretive or contested viewpoint is `stance`. External evidence must never
turn a claim into a correction request. Return at most one assessment for each
claim ID.

## Claims

```json
{{CLAIMS}}
```

## Textbook evidence

```json
{{TEXTBOOK_EVIDENCE}}
```

## External evidence

```json
{{EXTERNAL_EVIDENCE}}
```
