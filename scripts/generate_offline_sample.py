"""Generate a richer offline sample with a configured OpenAI-compatible model.

The committed MockProvider fixture is the deterministic fallback. This script
is intentionally opt-in and writes JSON only to stdout, so API keys and model
responses are never committed accidentally.
"""

import sys
from pathlib import Path

from pydantic import BaseModel, Field

from annotation.ingestion.pdf_parser import parse_pdf
from annotation.providers import StructuredGenerationRequest, create_provider_from_env
from annotation.prompt_loader import load_prompt


class Section(BaseModel):
    title: str
    explanation: str
    source_hint: str


class QuizItem(BaseModel):
    question: str
    options: list[str]
    answer: str | int
    explanation: str
    source_hint: str


class OfflineSample(BaseModel):
    title: str = "实数系与复数系"
    subtitle: str = "基于《数学分析》第1章的离线回归样例"
    learning_objectives: list[str] = Field(default_factory=list)
    key_terms: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    quiz: list[QuizItem] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


_, blocks = parse_pdf("books/数学分析第1章.pdf", run_id="run-fixture-generation")
text = "\n".join(f"[{block.source_ref}] {block.text}" for block in blocks[:20])[:9000]
prompt = load_prompt("generate_offline_sample", TEXTBOOK_CONTEXT=text)
provider = create_provider_from_env()
if provider.provider == "mock":
    raise SystemExit("Set MODEL_PROVIDER=deepseek (or another real endpoint) before generating a model-backed sample.")
provider._client.timeout = 180
response = provider.generate_structured(
    StructuredGenerationRequest(prompt=prompt, schema=OfflineSample, max_output_tokens=3000)
)
payload = response.value.model_dump_json(indent=2)
Path("storage/deepseek-offline-sample.json").parent.mkdir(parents=True, exist_ok=True)
Path("storage/deepseek-offline-sample.json").write_text(payload, encoding="utf-8")
sys.stdout.buffer.write(payload.encode("utf-8"))
