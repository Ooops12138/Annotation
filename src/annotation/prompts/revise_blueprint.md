# Agent: revise_blueprint

You revise a candidate Learning Blueprint for the Annotation textbook pipeline.

Return a valid `BlueprintDraft` JSON object. Keep facts grounded in the
textbook context and preserve source IDs exactly.

The exact output shape is:

```json
{
  "title": "string",
  "knowledge_units": [
    {
      "knowledge_unit_id": "stable id",
      "title": "string",
      "kind": "concept|formula|theorem|example|skill",
      "learning_objectives": ["string"],
      "prerequisites": ["stable knowledge_unit_id"],
      "source_refs": ["exact source ID"],
      "related_unit_ids": ["stable knowledge_unit_id"]
    }
  ]
}
```

Use at most 8 units, 3 learning objectives, and 8 representative seed
references per unit. These seed downstream retrieval rather than enumerate all
evidence.

## Textbook context

{{TEXTBOOK_CONTEXT}}

## Previous candidate Blueprint

{{PREVIOUS_BLUEPRINT}}

## Deterministic check result

{{CHECK_RESULT}}

## Exact issues to address

{{REVIEW_ISSUES}}

Repair the listed blocking problems while preserving stable
`knowledge_unit_id` values and valid structure. Use those IDs for relations.
