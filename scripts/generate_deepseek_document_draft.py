import sys
from pathlib import Path

from annotation.providers import StructuredGenerationRequest, create_provider_from_env
from annotation.workflow.graph import DocumentDraft
from annotation.ingestion.pdf_parser import parse_pdf


_, blocks = parse_pdf("books/数学分析第1章.pdf", run_id="run-draft-generation")
text = "\n".join(f"[{block.source_ref}] {block.text}" for block in blocks[:12])[:5000]
provider = create_provider_from_env()
if provider.provider == "mock":
    raise SystemExit("Set MODEL_PROVIDER=deepseek (or another real endpoint) before generating a model-backed sample.")
provider._client.timeout = 180
prompt = """You are generating one small regression fixture from a Chinese textbook excerpt. Return ONLY valid JSON matching the schema exactly. Use simplified Chinese. Do not invent facts. Keep every string short. quiz_answer must exactly equal one option. source_refs must be [].\n\nTEXTBOOK EXCERPT:\n""" + text
response = provider.generate_structured(
    StructuredGenerationRequest(
        prompt=prompt,
        schema=DocumentDraft,
        max_output_tokens=6000,
        metadata={"agent": "generate_deepseek_document_draft", "run_id": "run-draft-generation"},
    )
)
payload = response.value.model_dump_json(indent=2)
Path("storage/deepseek-document-draft.json").parent.mkdir(parents=True, exist_ok=True)
Path("storage/deepseek-document-draft.json").write_text(payload, encoding="utf-8")
sys.stdout.buffer.write(payload.encode("utf-8"))
