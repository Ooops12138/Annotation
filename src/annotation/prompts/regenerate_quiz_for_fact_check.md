# Agent: regenerate_quiz_for_fact_check

Regenerate one quiz artifact for the supplied knowledge unit. Return only JSON
matching the existing `QuizDraft` shape. Correct the supplied direct textbook
contradictions while retaining valid questions where possible. Every question,
correct answer, and explanation must be supported by the ContextPack. The
number of questions may be zero. Do not use external sources or invent source
IDs.

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
