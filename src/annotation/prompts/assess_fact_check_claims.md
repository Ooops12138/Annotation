# Agent: assess_fact_check_claims

Assess each claim against the supplied evidence and return a
`FactCheckAssessmentsDraft` JSON object.

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

Use `contradicted` and set `sufficient_textbook_evidence` true only for a
direct, locatable textbook contradiction. Use `insufficient` for missing
evidence, `external_conflict` for external disagreement, and `stance` for an
interpretive or contested viewpoint. Return at most one assessment per claim.

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
