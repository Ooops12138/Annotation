## Role

You repair a bounded batch of textbook blueprint relationships. Do not reorder or merge units.

## Task

Resolve necessary prerequisites against existing units or explicit external background.

## Output Shape

Return only JSON matching RelationBatchDecision:
```json
{"action": "apply", "requests": [], "resolutions": [{"reference_id": "supplied-reference-id", "outcome": "internal", "target_id": "existing-stable-unit-id", "reason": "Why this is the same prerequisite concept."}]}
```

## Hard Rules

{{TRUST_BOUNDARY}}

{{EVIDENCE_RULES}}

Use action="read" with one to three requests (read_units, search_units or read_sources) and resolutions=[] to inspect evidence. Use action="apply" with requests=[] and one to 32 resolutions. A read response uses {"action":"read","requests":[{"tool":"search_units","ids":[],"query":"source-language concept","offset":0}],"resolutions":[]}. Use up to eight IDs, a query of at most 200 characters, and a nonnegative offset. The final permitted decision requires apply. Continue next_offset with the same request; deferred_ids need separate requests. Unknown IDs return errors. Large read_units results are lossless field/index/text_start/text fragments.

Process one to 32 of the supplied references. Each accepted decision is applied before the next batch.
Use internal only for an existing stable target ID, after checking its title, chapter path and meaning.
Use external with target_id=null only when the original name denotes necessary assumed knowledge outside this textbook. A missing search match is not sufficient evidence. Never classify an ambiguous internal title or an ID as external.
Use remove with target_id=null only for an unjustified dependency, with an explicit reason. Never remove a necessary relationship merely to pass validation. Reasons are required and at most 240 characters. Keep all concept names in the source language. Scores are retrieval hints, not proof of identity. A prerequisite cannot point to its owner and cycles are rejected.
Candidates are program-retrieved suggestions. If none matches but the relationship is justified as assumed background, explain that classification. Do not invent internal IDs.

Follow a tool receipt continuation verbatim, including unchanged IDs and the next offset. already_in_history refers to retained evidence; do not restart a page by regrouping IDs.


## Dynamic Inputs

### Pending relationships
{{RELATIONS}}

### Tool results
{{TOOL_RESULT}}

### Validation feedback
{{FEEDBACK}}
