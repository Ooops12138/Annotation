### Interactive critic rules

- Report `interaction_mismatch` only when actual behavior contradicts the spec description or textbook facts; absence of endpoint switching alone is not a mismatch.
- Use `objective_mismatch`, `source_mismatch`, `mathematical_mismatch` and `interaction_mismatch` only for blocking inconsistencies. Use `accessibility` for accessibility issues that do not change mathematical facts.
- Do not replace existing source, expression, safety or browser-rendering hard checks.
