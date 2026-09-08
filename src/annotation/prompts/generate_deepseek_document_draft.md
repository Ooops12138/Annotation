# Agent: generate_deepseek_document_draft

Generate one small regression fixture from the Chinese textbook excerpt.

Return **only valid JSON** matching the supplied schema exactly. Use simplified
Chinese. Do not invent facts. Keep every string short. `quiz_answer` must
exactly equal one item in `quiz_options`. `source_refs` must be an empty array.

## Textbook excerpt

{{TEXTBOOK_CONTEXT}}

