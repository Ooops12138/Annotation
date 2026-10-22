"""Compare frozen textbook FTS5, vector and alternating hybrid retrieval.

Run from any directory with the project virtual environment. Embedding secrets
are read only from dedicated RAG_EMBEDDING_* settings; they are never reported.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv
from annotation.retrieval.evaluation import evaluate_tool, load_dataset, render_report, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    fixture = ROOT / "tests/fixtures/retrieval/math_ch1"
    parser.add_argument("--corpus", type=Path, default=fixture / "corpus.jsonl")
    parser.add_argument("--questions", type=Path, default=fixture / "questions.json")
    parser.add_argument("--methods", nargs="+", choices=["fts", "vector", "hybrid"], default=["fts", "vector", "hybrid"])
    parser.add_argument("--chunking", choices=["raw", "semantic"], default="raw")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results/retrieval/local")
    parser.add_argument("--persist-directory", type=Path, default=ROOT / "storage/chroma/retrieval-evaluation")
    parser.add_argument("--embedding-provider", choices=["auto", "local", "api"])
    parser.add_argument("--local-model", help="Override RAG_EMBEDDING_MODEL_PATH")
    parser.add_argument("--model-revision", help="Pin the local Hugging Face model revision")
    parser.add_argument("--api-model", help="Override RAG_EMBEDDING_API_MODEL")
    parser.add_argument("--api-base-url", help="Override RAG_EMBEDDING_API_BASE_URL")
    parser.add_argument("--query-instruction", help="Explicit local query prefix, including an empty string")
    parser.add_argument("--offline", action="store_true", help="Use only cached model files (no model downloads)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--reuse-probe", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    load_dotenv(ROOT / ".env", override=False)
    if args.embedding_provider:
        os.environ["RAG_EMBEDDING_PROVIDER"] = args.embedding_provider
    for value, variable in [(args.local_model, "RAG_EMBEDDING_MODEL_PATH"), (args.model_revision, "RAG_EMBEDDING_REVISION"),
                            (args.api_model, "RAG_EMBEDDING_API_MODEL"), (args.api_base_url, "RAG_EMBEDDING_API_BASE_URL"),
                            (args.query_instruction, "RAG_EMBEDDING_QUERY_INSTRUCTION")]:
        if value is not None:
            os.environ[variable] = value
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    blocks, dataset = load_dataset(args.corpus, args.questions)
    if args.chunking == "semantic":
        from annotation.ingestion.semantic_chunker import build_semantic_blocks
        raw_blocks = blocks
        blocks = build_semantic_blocks(raw_blocks)
        raw_qrels = {q["id"]: q["qrels"] for q in dataset["questions"]}
        for question in dataset["questions"]:
            mapped = {}
            for chunk in blocks:
                grade = max((raw_qrels[question["id"]].get(ref, {}).get("grade", 0)
                             for ref in chunk.source_refs), default=0)
                if grade:
                    mapped[chunk.source_ref] = {
                        "grade": grade,
                        "text_sha256": hashlib.sha256(chunk.text.encode()).hexdigest(),
                    }
            question["qrels"] = mapped
            question["answerable"] = bool(mapped)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed", "annotation_status": dataset["annotation_status"],
        "command": subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]),
        "invocation": [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
        "corpus": {"blocks": len(blocks), "pages": len({(b.document_id, b.page_number) for b in blocks}),
                   "sha256": sha256_file(args.corpus), "questions_sha256": sha256_file(args.questions)},
        "environment": {"python": sys.version, "platform": platform.platform(), "packages": {}},
        "encoder": None, "setup": {}, "methods": {}, "chunking": args.chunking,
        "semantic_chunking": ({"policy": "semantic-chunker-v1", "target_chars": 480, "max_chars": 900,
                                "overlap_blocks": 0, "source_pages": len({(b.document_id, b.page_number) for b in raw_blocks}),
                                "source_blocks": len(raw_blocks), "semantic_blocks": len(blocks)} if args.chunking == "semantic" else None),
    }
    for package in ["torch", "sentence-transformers", "llama-index-core", "llama-index-vector-stores-chroma", "chromadb", "openai", "posthog"]:
        try:
            report["environment"]["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report["environment"]["packages"][package] = None
    tools, records, connection = {}, [], None
    if any(method in args.methods for method in ["fts", "hybrid"]) and not args.reuse_probe:
        from annotation.fact_checking.skills import SQLiteFts5TextbookDatabaseSearchSkill
        from annotation.retrieval.fts import index_source_blocks
        started = perf_counter()
        connection = sqlite3.connect(":memory:")
        index_source_blocks(connection, blocks)
        tools["fts"] = SQLiteFts5TextbookDatabaseSearchSkill(connection, max_results=100)
        report["setup"]["fts_index_seconds"] = perf_counter() - started
    if any(method in args.methods for method in ["vector", "hybrid"]):
        try:
            from annotation.retrieval.embeddings import create_embedding_provider_from_env
            from annotation.retrieval.llamaindex_chroma import LlamaIndexChromaTextbookSearchTool
            started = perf_counter()
            provider = create_embedding_provider_from_env()
            report["setup"]["model_load_seconds"] = perf_counter() - started
            report["encoder"] = provider.identity
            selected = "local" if provider.name == "local-sentence-transformer" else "api"
            reproduce = [sys.executable, str(Path(__file__).resolve()), "--corpus", str(args.corpus.resolve()),
                         "--questions", str(args.questions.resolve()), "--methods", *args.methods, "--chunking", args.chunking,
                         "--output-dir", str(args.output_dir.resolve()), "--persist-directory", str(args.persist_directory.resolve()),
                         "--embedding-provider", selected, "--repeats", str(args.repeats)]
            if selected == "local":
                reproduce += ["--local-model", provider.identity["model"], "--query-instruction", provider.identity["query_instruction"]]
                if not provider.identity["revision"].startswith("local"):
                    reproduce += ["--model-revision", provider.identity["revision"]]
            else:
                reproduce += ["--api-model", provider.identity["model"], "--api-base-url", provider.identity["endpoint"]]
            if args.offline:
                reproduce.append("--offline")
            report["command"] = "& " + " ".join("'" + str(arg).replace("'", "''") + "'" for arg in reproduce)
            if provider.name == "local-sentence-transformer":
                import torch
                report["environment"]["torch_threads"] = torch.get_num_threads()
            tools["vector"] = LlamaIndexChromaTextbookSearchTool(
                blocks, embedding_provider=provider, persist_directory=str(args.persist_directory),
            )
            report["setup"]["vector_index"] = tools["vector"].index_stats
            if args.reuse_probe:
                args.reuse_probe.write_text(json.dumps({"encoder": provider.identity, **report["setup"]},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
                return 0
        except Exception as exc:
            # Error class is sufficient for reports and cannot expose provider keys.
            report["status"] = "incomplete"
            report["setup"]["vector_failure"] = type(exc).__name__
            for method in args.methods:
                if method in {"vector", "hybrid"}:
                    report["methods"][method] = {"failure": type(exc).__name__}
            print(f"Vector initialization failed: {type(exc).__name__}", file=sys.stderr)
            if args.reuse_probe:
                return 1
    if "hybrid" in args.methods and "vector" in tools:
        from annotation.retrieval.tool import HybridTextbookSearchTool
        tools["hybrid"] = HybridTextbookSearchTool(tools["fts"], tools["vector"])
    try:
        for method in dict.fromkeys(args.methods):
            if method not in tools:
                continue
            print(f"Evaluating {method}: {len(dataset['questions'])} questions x {args.repeats} rounds", flush=True)
            summary, rows = evaluate_tool(tools[method], dataset["questions"], blocks, repeats=args.repeats)
            report["methods"][method] = summary
            if summary["error_rounds"] or summary["degraded_rounds"] or summary["warmup_error"]:
                report["status"] = "incomplete"
            records.extend({"method": method, **row} for row in rows)
        if "vector" in tools:
            # A new interpreter must reconstruct the index without encoding documents.
            probe_path = args.output_dir / "reuse-probe.json"
            child = [sys.executable, str(Path(__file__).resolve()), "--corpus", str(args.corpus.resolve()),
                     "--questions", str(args.questions.resolve()), "--methods", "vector", "--chunking", args.chunking,
                     "--persist-directory", str(args.persist_directory.resolve()),
                     "--output-dir", str(args.output_dir.resolve()), "--reuse-probe", str(probe_path.resolve()),
                     "--embedding-provider", "local" if report["encoder"]["provider"] == "local-sentence-transformer" else "api"]
            if args.offline:
                child.append("--offline")
            if report["encoder"]["provider"] == "local-sentence-transformer":
                child += ["--local-model", report["encoder"]["model"], "--query-instruction", report["encoder"]["query_instruction"]]
                if not report["encoder"]["revision"].startswith("local"):
                    child += ["--model-revision", report["encoder"]["revision"]]
            else:
                child += ["--api-model", report["encoder"]["model"], "--api-base-url", report["encoder"]["endpoint"]]
            try:
                probe = subprocess.run(child, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
                if probe.returncode:
                    raise RuntimeError("restart probe failed")
                report["setup"]["restart_reuse"] = json.loads(probe_path.read_text(encoding="utf-8"))
                stats = report["setup"]["restart_reuse"]["vector_index"]
                if stats["indexed"] != 0 or stats["reused"] != len(blocks) or stats["collection"] != tools["vector"].collection_name:
                    raise RuntimeError("restart did not reuse the same index")
            except Exception as exc:
                report["setup"]["restart_failure"] = type(exc).__name__
                report["status"] = "incomplete"
    finally:
        if connection is not None:
            connection.close()
    (args.output_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "queries.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    (args.output_dir / "report.md").write_text(render_report(report, records), encoding="utf-8")
    print(args.output_dir / "report.md", flush=True)
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
