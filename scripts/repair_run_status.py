"""Repair a saved run's SQLite projection, preserving its immutable artifacts.

Dry-run by default. --apply creates an exclusive backup and repair receipt first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def repair_run(manifest_path: Path, database: Path, output_dir: Path, *, apply: bool = False) -> dict:
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    run_id = manifest["run_id"]
    paths = manifest.get("paths", {})
    statuses = []
    reasons = []
    for key in ("blueprint_loop", "content_loop", "fact_check_summary"):
        status = (manifest.get(key) or {}).get("final_status")
        if status in {"blocked", "failed"}:
            statuses.append(status)
            reasons.append(f"{key}:{status}")
    for key in ("fact_check",):
        if paths.get(key):
            p = Path(paths[key])
            if not p.is_absolute():
                p = manifest_path.parent / p
            status = json.loads(p.read_text("utf-8")).get("status")
            if status in {"blocked", "failed"}:
                statuses.append(status)
                reasons.append(f"{key}:{status}")
    if not paths.get("document") or not paths.get("review"):
        reasons.append("missing_final_document_or_review")
    if not reasons:
        raise ValueError("No supported repair: the manifest does not establish an incomplete run")
    target_status = "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "failed"
    # mode=rw refuses typos rather than creating a new database.
    with sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown run: {run_id}")
        latest = connection.execute("SELECT path FROM artifacts WHERE run_id=? AND kind='run' ORDER BY version DESC LIMIT 1", (run_id,)).fetchone()
        if latest and Path(latest[0]).resolve() != manifest_path.resolve():
            raise ValueError(f"Stale manifest: use the latest indexed snapshot {latest[0]}")
        versions = [dict(r) for r in connection.execute("SELECT * FROM document_versions WHERE run_id=?", (run_id,))]
        plan = dict(run_id=run_id, manifest=str(manifest_path.resolve()),
                    manifest_sha256=hashlib.sha256(raw).hexdigest(), old_run=dict(row),
                    new_status=target_status, reasons=reasons, old_versions=versions, applied=False)
        if not apply:
            return plan
        output_dir.mkdir(parents=True, exist_ok=False)
        backup = output_dir / "before.sqlite3"
        with sqlite3.connect(backup) as destination:
            connection.backup(destination)
        (output_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        with connection:
            # Preserve concurrent changes instead of overwriting an unseen run.
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            current_versions = [dict(r) for r in connection.execute("SELECT * FROM document_versions WHERE run_id=?", (run_id,))]
            if dict(current) != dict(row) or current_versions != versions:
                raise RuntimeError("Run changed since backup; retry with a fresh output directory")
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata["status_repair"] = dict(reasons=reasons, manifest_sha256=plan["manifest_sha256"], backup=str(backup.resolve()))
            connection.execute("UPDATE runs SET status=?, metadata_json=? WHERE run_id=?",
                               (target_status, json.dumps(metadata, ensure_ascii=False), run_id))
            connection.execute("UPDATE document_versions SET document_status='blocked' WHERE run_id=?", (run_id,))
            connection.execute("UPDATE artifacts SET status='blocked' WHERE run_id=? AND kind='document'", (run_id,))
            for document_id in {v["document_id"] for v in versions}:
                # Only move a pointer that currently points at this stopped run.
                document = connection.execute("SELECT current_version_id FROM documents WHERE document_id=?", (document_id,)).fetchone()
                if document[0] not in {v["document_version_id"] for v in versions}:
                    continue
                usable = connection.execute("""SELECT dv.document_version_id FROM document_versions dv
                    JOIN runs r ON r.run_id=dv.run_id WHERE dv.document_id=? AND dv.run_id<>?
                    AND dv.document_status IN ('accepted','published') AND dv.review_status IN ('passed','at_risk')
                    AND r.status='succeeded' ORDER BY dv.version DESC LIMIT 1""", (document_id, run_id)).fetchone()
                # Keep a first blocked version queryable for diagnostics.
                if usable:
                    connection.execute("UPDATE documents SET current_version_id=? WHERE document_id=?", (usable[0], document_id))
        plan.update(applied=True, backup=str(backup.resolve()))
        (output_dir / "receipt.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair_run(args.manifest, args.database, args.output_dir, apply=args.apply), ensure_ascii=False, indent=2))
