# Agent: generate_quiz_artifact

Check that the correct answer retains every domain, quantifier and nonzero or
integer restriction required by the source theorem. Test it against a degenerate
case. Do not turn an ambiguous source transcription into a confident answer;
use an unambiguous cited formulation or omit that question with a recorded reason.

Generate a small, traceable single-choice quiz set for one knowledge unit.
Follow the course architect's explicit question count; otherwise choose from
the objectives and ContextPack evidence. The number may be zero when no supported
question is useful. Return only the questions; the system derives their count.

Return only valid JSON matching this shape:

```json
{
  "knowledge_unit_id": "exact id from the knowledge unit context",
  "target_objectives": ["exact objective text from the knowledge unit context"],
  "questions": [
    {
      "question_id": "globally unique id in the form <knowledge_unit_id>-quiz-01",
      "knowledge_unit_id": "exact knowledge unit id",
      "target_objectives": ["one or more exact objective strings"],
      "question": "learner-facing question",
      "options": ["at least two distinct choices"],
      "answer": "exactly one option string",
      "explanation": "short explanation supported by the cited evidence",
      "source_refs": ["exact source_ref copied from the ContextPack"]
    }
  ]
}
```

Use the exact knowledge-unit ID and a two-digit sequence for globally unique
IDs, such as `<knowledge_unit_id>-quiz-01`.

Each answer must occur exactly once in its options. All learner-facing fields
must be supported by cited ContextPack references, and `target_objectives` must
use the exact unit objective strings.

## Formula output contract

- Every question, option, answer, and explanation field must use the same
  Markdown math contract as the prose generator.
- Put every formula, equation, inequality, symbolic definition, summation,
  unit expression, or derivation in explicit LaTeX delimiters: `$...$` for
  inline math. These fields are inline strings, so do not use `$$...$$`.
- Do not emit bare formulas or Unicode mathematical notation in place of
  LaTeX. In particular, do not use Unicode subscripts/superscripts or symbols
  such as `Σₖ₌₁ⁿ`, `a₁`, `x²`, `≤`, or `√` as the formula representation.
- Keep surrounding Chinese prose outside the math delimiters. For example:
  `对任意 $x,y\in\mathbb{R}$，有 $x+y=y+x$。`
- The answer must use exactly the same Markdown representation as the matching
  option, including its math delimiters.

Copy each `source_refs` value exactly from a ContextPack excerpt's source ID.

{{REVISION_FEEDBACK}}

## Knowledge unit

```json
{{KNOWLEDGE_UNIT_CONTEXT}}
```

## Target objectives

```json
{{TARGET_OBJECTIVES}}
```

## Course architect decision

{{COURSE_ARCHITECT_DECISION}}

## ContextPack (the only evidence allowed)

{{CONTEXT_PACK}}
