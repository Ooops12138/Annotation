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
      "explanation": "string",
      "formula": "string",
      "example": "string",
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

Use simplified Chinese. Make exactly 2 sections and 2 quiz items. Use only
facts in the textbook excerpt; do not invent facts. `source_hint` should be a
page or section label, not a `source_ref`.

## Textbook excerpt

{{TEXTBOOK_CONTEXT}}

