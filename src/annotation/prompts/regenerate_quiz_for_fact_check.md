# Agent: regenerate_quiz_for_fact_check

Regenerate the `QuizDraft` for this knowledge unit, correcting supplied direct
textbook contradictions while retaining valid questions where possible. Use the
ContextPack as the evidence boundary; the question count may be zero.

## Knowledge unit

```json
{{KNOWLEDGE_UNIT_CONTEXT}}
```

## ContextPack

{{CONTEXT_PACK}}

## Existing quiz artifact

```json
{{QUIZ_ARTIFACT}}
```

## Direct textbook contradictions to repair

```json
{{FACT_CHECK_FEEDBACK}}
```
