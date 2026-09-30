"""Runtime paths resolved independently of the process working directory."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOOKS_DIR = PROJECT_ROOT / "books"
STORAGE_DIR = PROJECT_ROOT / "storage"
DATABASE_PATH = STORAGE_DIR / "annotation.sqlite3"
# Kept as a migration/debug source for installations created before the
# unified library database. New indexing uses ``DATABASE_PATH``.
LEGACY_SOURCE_INDEX_PATH = STORAGE_DIR / "source-index.sqlite3"
SOURCE_INDEX_PATH = DATABASE_PATH

# The first revision loop is deliberately bounded.  Tests may inject a
# smaller value while production defaults to the three-attempt contract.
BLUEPRINT_MAX_ATTEMPTS = 3
CONTENT_REFLECTION_MAX_ATTEMPTS = 3
FACT_CHECK_MAX_CORRECTIONS = 2
FACT_CHECK_MAX_CLAIMS_PER_UNIT = 20
FACT_CHECK_TEXTBOOK_RESULT_LIMIT = 5
FACT_CHECK_WEB_QUERY_LIMIT = 10
FACT_CHECK_ADDITIONAL_RETRIEVAL_ENABLED = False
WEB_SEARCH_PROVIDER = "tavily"
WEB_SEARCH_ENDPOINT = "https://api.tavily.com/search"
WEB_SEARCH_TIMEOUT_SECONDS = 10.0
WEB_SEARCH_MAX_RESULTS = 5
INTERACTIVE_COMPONENT_MAX_ATTEMPTS = 3
MODEL_MAX_OUTPUT_TOKENS = 8000


def model_max_output_tokens(value: int | str | None = None) -> int:
    """Return the shared upper bound for structured model responses.

    Long Blueprint/component JSON objects are otherwise easy to truncate. The
    value remains configurable because OpenAI-compatible providers expose
    different output limits.
    """

    raw_value = value if value is not None else os.getenv("MODEL_MAX_OUTPUT_TOKENS")
    if raw_value is None or raw_value == "":
        return MODEL_MAX_OUTPUT_TOKENS
    try:
        tokens = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("MODEL_MAX_OUTPUT_TOKENS must be an integer from 1000 to 32000") from exc
    if tokens < 1000:
        raise ValueError("MODEL_MAX_OUTPUT_TOKENS must be at least 1000")
    return min(tokens, 32000)


def blueprint_max_attempts(value: int | str | None = None) -> int:
    """Return the configured Blueprint loop limit, bounded to one through three.

    An explicit value is useful for deterministic tests. Invalid values fail
    loudly instead of silently turning a configuration mistake into an
    unbounded or zero-attempt run.
    """

    raw_value: int | str | None = value
    if raw_value is None:
        raw_value = os.getenv("BLUEPRINT_MAX_ATTEMPTS")
    if raw_value is None or raw_value == "":
        return BLUEPRINT_MAX_ATTEMPTS
    try:
        attempts = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("BLUEPRINT_MAX_ATTEMPTS must be an integer from 1 to 3") from exc
    if attempts < 1:
        raise ValueError("BLUEPRINT_MAX_ATTEMPTS must be at least 1")
    return min(attempts, BLUEPRINT_MAX_ATTEMPTS)


def content_reflection_max_attempts(value: int | str | None = None) -> int:
    """Return the bounded per-unit content reflection loop limit."""

    raw_value: int | str | None = value
    if raw_value is None:
        raw_value = os.getenv("CONTENT_REFLECTION_MAX_ATTEMPTS")
    if raw_value is None or raw_value == "":
        return CONTENT_REFLECTION_MAX_ATTEMPTS
    try:
        attempts = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("CONTENT_REFLECTION_MAX_ATTEMPTS must be an integer from 1 to 3") from exc
    if attempts < 1:
        raise ValueError("CONTENT_REFLECTION_MAX_ATTEMPTS must be at least 1")
    return min(attempts, CONTENT_REFLECTION_MAX_ATTEMPTS)


def interactive_component_max_attempts(value: int | str | None = None) -> int:
    """Return the bounded A-004 generation/revision attempt limit."""

    raw_value: int | str | None = value
    if raw_value is None:
        raw_value = os.getenv("INTERACTIVE_COMPONENT_MAX_ATTEMPTS")
    if raw_value is None or raw_value == "":
        return INTERACTIVE_COMPONENT_MAX_ATTEMPTS
    try:
        attempts = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("INTERACTIVE_COMPONENT_MAX_ATTEMPTS must be an integer from 1 to 3") from exc
    if attempts < 1:
        raise ValueError("INTERACTIVE_COMPONENT_MAX_ATTEMPTS must be at least 1")
    return min(attempts, INTERACTIVE_COMPONENT_MAX_ATTEMPTS)


def fact_check_max_corrections(value: int | str | None = None) -> int:
    """Return the independent A-003 correction limit, capped at two."""

    raw_value: int | str | None = value
    if raw_value is None:
        raw_value = os.getenv("FACT_CHECK_MAX_CORRECTIONS")
    if raw_value is None or raw_value == "":
        return FACT_CHECK_MAX_CORRECTIONS
    try:
        corrections = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("FACT_CHECK_MAX_CORRECTIONS must be an integer from 0 to 2") from exc
    if corrections < 0:
        raise ValueError("FACT_CHECK_MAX_CORRECTIONS must be at least 0")
    return min(corrections, FACT_CHECK_MAX_CORRECTIONS)


def fact_check_max_claims_per_unit(value: int | str | None = None) -> int:
    """Return the bounded claim-extraction budget for one knowledge unit."""

    raw_value: int | str | None = value
    if raw_value is None:
        raw_value = os.getenv("FACT_CHECK_MAX_CLAIMS_PER_UNIT")
    if raw_value is None or raw_value == "":
        return FACT_CHECK_MAX_CLAIMS_PER_UNIT
    try:
        claims = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("FACT_CHECK_MAX_CLAIMS_PER_UNIT must be an integer from 1 to 50") from exc
    if claims < 1:
        raise ValueError("FACT_CHECK_MAX_CLAIMS_PER_UNIT must be at least 1")
    return min(claims, 50)


def fact_check_web_enabled(value: bool | str | None = None) -> bool:
    """Return whether the optional external evidence skill may make requests."""

    raw_value: bool | str | None = value
    if raw_value is None:
        raw_value = os.getenv("FACT_CHECK_WEB_ENABLED")
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None or raw_value == "":
        return False
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("FACT_CHECK_WEB_ENABLED must be a boolean")


def fact_check_web_query_limit(value: int | str | None = None) -> int:
    """Return the bounded external-query budget for one knowledge unit."""

    raw_value: int | str | None = value
    if raw_value is None:
        raw_value = os.getenv("FACT_CHECK_WEB_QUERY_LIMIT")
    if raw_value is None or raw_value == "":
        return FACT_CHECK_WEB_QUERY_LIMIT
    try:
        queries = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("FACT_CHECK_WEB_QUERY_LIMIT must be an integer from 0 to 20") from exc
    if queries < 0:
        raise ValueError("FACT_CHECK_WEB_QUERY_LIMIT must be at least 0")
    return min(queries, 20)


def fact_check_additional_retrieval_enabled(value: bool | str | None = None) -> bool:
    raw_value = value if value is not None else os.getenv("FACT_CHECK_ADDITIONAL_RETRIEVAL_ENABLED")
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None or raw_value == "":
        return FACT_CHECK_ADDITIONAL_RETRIEVAL_ENABLED
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("FACT_CHECK_ADDITIONAL_RETRIEVAL_ENABLED must be a boolean")


def web_search_enabled(value: bool | str | None = None) -> bool:
    return fact_check_web_enabled(value if value is not None else os.getenv("WEB_SEARCH_ENABLED"))


def web_search_allowed_domains() -> list[str]:
    raw = os.getenv("WEB_SEARCH_ALLOWED_DOMAINS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def web_search_timeout_seconds() -> float:
    return float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", str(WEB_SEARCH_TIMEOUT_SECONDS)))


def web_search_max_results() -> int:
    return max(1, min(int(os.getenv("WEB_SEARCH_MAX_RESULTS", str(WEB_SEARCH_MAX_RESULTS))), 100))


def rag_enabled() -> bool:
    return str(os.getenv("RAG_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}


def embedding_provider_name() -> str:
    return (os.getenv("RAG_EMBEDDING_PROVIDER") or "local").strip().lower()


def embedding_config() -> dict[str, str]:
    provider = embedding_provider_name()
    if provider == "local":
        model_path = (os.getenv("RAG_EMBEDDING_MODEL_PATH") or "").strip()
        if not model_path:
            raise ValueError("RAG_EMBEDDING_MODEL_PATH is required for local embeddings")
        return {"provider": provider, "model_path": model_path}
    if provider in {"api", "openai-compatible"}:
        api_key = (os.getenv("RAG_EMBEDDING_API_KEY") or "").strip()
        model = (os.getenv("RAG_EMBEDDING_API_MODEL") or "").strip()
        if not api_key or not model:
            raise ValueError("RAG_EMBEDDING_API_KEY and RAG_EMBEDDING_API_MODEL are required for API embeddings")
        return {"provider": "api", "api_key": api_key, "model": model, "base_url": os.getenv("RAG_EMBEDDING_API_BASE_URL", "")}
    raise ValueError("RAG_EMBEDDING_PROVIDER must be local or api")


def first_book_pdf() -> Path:
    pdfs = sorted(BOOKS_DIR.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"books/ 中没有教材 PDF: {BOOKS_DIR}")
    return pdfs[0]
