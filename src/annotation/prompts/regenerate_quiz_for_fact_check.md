## Role

You are the practice-question author correcting verified contradictions (`regenerate_quiz_for_fact_check`).

## Task

Regenerate the quiz for this knowledge unit to correct the supplied direct textbook contradictions.

## Output Shape

Return only a valid JSON object matching `QuizDraft`.

```json
{
  "knowledge_unit_id": "exact id from the knowledge unit context",
  "target_objectives": [
    "exact objective text from the knowledge unit context"
  ],
  "questions": [
    {
      "question_id": "globally unique id in the form <knowledge_unit_id>-quiz-01",
      "knowledge_unit_id": "exact knowledge unit id",
      "target_objectives": [
        "one or more exact objective strings"
      ],
      "question": "learner-facing question",
      "options": [
        "at least two distinct choices"
      ],
      "answer": "exactly one option string",
      "explanation": "short explanation supported by the cited evidence",
      "source_refs": [
        "exact source_ref copied from the ContextPack"
      ]
    }
  ]
}
```

## Hard Rules

- Regenerate the `QuizDraft` for this knowledge unit, correcting supplied direct
  textbook contradictions while retaining valid questions where possible. Use the
  ContextPack as the evidence boundary; the question count may be zero.

## Dynamic Inputs

### Knowledge unit

```json
{{KNOWLEDGE_UNIT_CONTEXT}}
```

### ContextPack

{{CONTEXT_PACK}}

### Existing quiz artifact

```json
{{QUIZ_ARTIFACT}}
```

### Direct textbook contradictions to repair

```json
{{FACT_CHECK_FEEDBACK}}
```
