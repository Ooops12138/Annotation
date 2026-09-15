# Agent: generate_offline_sample

Generate one small offline regression sample from the Chinese textbook excerpt.

Return **only valid JSON** matching this exact object shape:

```json
{
  "title": "string",
  "subtitle": "string",
  "learning_objectives": ["string"],
  "key_terms": ["string"],
  "sections": [
    {
      "title": "string",
      "explanation": "complete learner-facing Markdown",
      "source_hint": "string"
    }
  ],
  "quiz": [
    {
      "question": "string",
      "options": ["string"],
      "answer": "string or integer",
      "explanation": "string",
      "source_hint": "string"
    }
  ],
  "risk_notes": ["string"]
}
```

Use simplified Chinese. Make exactly 2 sections. Choose the number of quiz
items from the evidence in the textbook excerpt, including zero when no
well-supported practice item is appropriate; do not pad the list to reach a
fixed target. Use only facts in the textbook excerpt; do not invent facts.
`source_hint` should be a page or section label, not a `source_ref`.
Put formulas and ordinary worked examples directly in `explanation` as
Markdown, using `$...$` or `$$...$$` for math. Do not return separate formula
or example fields.

## Textbook excerpt

{{TEXTBOOK_CONTEXT}}
