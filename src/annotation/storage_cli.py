"""Local artifact inspection and explicit, lossless execution archival.

Usage: python -m annotation.storage_cli stats
       python -m annotation.storage_cli archive-execution --run-id ID [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import uuid
import zipfile
from pathlib import Path
from typing import Any

from annotation.config import STORAGE_DIR
from annotation.artifact_storage import find_run_directory, execution_lock
from annotation.prompt_archive import read_artifact_json


def _files(directory: Path):
    """One pass, ignoring links so inventory cannot traverse outside storage."""
    if not directory.is_dir():
        return
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                yield from _files(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                yield Path(entry.path), entry.stat(follow_symlinks=False).st_size


def inventory(root: Path | None = None) -> dict[str, Any]:
    root = root or STORAGE_DIR / "artifacts"
    groups: dict[str, dict[str, Any]] = {}
    runs: dict[str, dict[str, Any]] = {}
    for path, size in _files(root):
        parts = path.relative_to(root).parts
        group = groups.setdefault(parts[0], {"files": 0, "bytes": 0})
        group["files"] += 1
        group["bytes"] += size
        if parts[0] == "runs" and len(parts) >= 3:
            key = parts[1]
        else:
            continue
        item = runs.setdefault(key, {"directory": key, "files": 0, "bytes": 0, "status": "unknown"})
        item["files"] += 1
        item["bytes"] += size
        if path.name == "run-info.json":
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
                item.update({key: info.get(key, "") for key in ("run_id", "title", "created_at", "status")})
            except (OSError, ValueError):
                item["status"] = "unreadable"
    return {"root": str(root.resolve()), "files": sum(g["files"] for g in groups.values()),
            "bytes": sum(g["bytes"] for g in groups.values()), "groups": groups,
            "runs": sorted(runs.values(), key=lambda item: item["bytes"], reverse=True)}


def _execution_directory(run_id: str) -> Path:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be a single path component")
    directory = find_run_directory(run_id)
    if directory is None:
        raise ValueError("run is not in the current artifact layout")
    target = directory / "execution"
    resolved = target.resolve()
    if not resolved.is_relative_to((STORAGE_DIR / "artifacts").resolve()) or target.is_symlink():
        raise ValueError("execution directory escapes artifact storage")
    return target


def archive_execution(run_id: str, *, apply: bool = False, database: Path | None = None) -> dict[str, Any]:
    target = _execution_directory(run_id)
    database = database or STORAGE_DIR / "annotation.sqlite3"
    if not database.is_file():
        raise ValueError("library database is required to verify terminal run status")
    # Read-only preview never creates/migrates the library. The write transaction
    # in apply prevents a concurrent API from changing the verified run status.
    uri = database.resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    with sqlite3.connect(uri, uri=True, timeout=1) as connection:
        if apply:
            connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row[0] not in {"succeeded", "blocked"}:
            raise ValueError("only verified succeeded/blocked runs can be archived; active, resumable and unknown runs are protected")
        if not target.is_dir():
            raise ValueError("execution directory does not exist (it may already be archived)")
        for (raw_path,) in connection.execute("SELECT path FROM artifacts"):
            if Path(raw_path).resolve().is_relative_to(target.resolve()):
                raise ValueError("a library artifact references execution data; archival refused")
        # Reject every link, including directory junctions, before walking/deletion.
        for parent, directories, filenames in os.walk(target, followlinks=False):
            for name in [*directories, *filenames]:
                child = Path(parent) / name
                if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
                    raise ValueError("execution directory contains a link or junction")
        files = list(_files(target))
        archive = target.with_name(target.name + ".zip")
        result = {"run_id": run_id, "status": row[0], "files": len(files),
                  "bytes": sum(size for _, size in files), "source": str(target),
                  "archive": str(archive), "applied": False}
        if not apply:
            return result
        if archive.exists():
            raise ValueError("archive already exists; refusing to replace audit evidence")
        lock_root = find_run_directory(run_id) or target.parent / (".archive-lock-" + run_id)
        with execution_lock(lock_root):
            temporary = archive.with_name(f".{archive.name}.{uuid.uuid4().hex}.tmp")
            try:
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
                    for path, _ in files:
                        output.write(path, path.relative_to(target).as_posix())
                with zipfile.ZipFile(temporary) as output:
                    if output.testzip() is not None or len(output.infolist()) != len(files):
                        raise ValueError("archive verification failed")
                temporary.replace(archive)
                # Recheck the resolved boundary immediately before recursive removal.
                if target.resolve() != Path(result["source"]).resolve() or not target.resolve().is_relative_to((STORAGE_DIR / "artifacts").resolve()):
                    raise ValueError("execution directory changed during archival")
                shutil.rmtree(target)
                result.update(applied=True, archive_bytes=archive.stat().st_size)
                return result
            finally:
                temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stats = sub.add_parser("stats", help="read-only inventory of current artifacts")
    stats.add_argument("--json", action="store_true", help="include all per-run details")
    archive = sub.add_parser("archive-execution", help="preview lossless archival of terminal execution records")
    archive.add_argument("--run-id", required=True)
    archive.add_argument("--apply", action="store_true", help="create and verify ZIP, then remove the original execution directory")
    read = sub.add_parser("read", help="resolve and print an artifact including its diagnostic references")
    read.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "stats":
            result = inventory()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"{result['files']:,} files / {result['bytes'] / 1024**3:.2f} GiB — {result['root']}")
                for name, item in sorted(result["groups"].items(), key=lambda pair: pair[1]["bytes"], reverse=True):
                    print(f"{name:24} {item['files']:8,} files {item['bytes'] / 1024**2:10.2f} MiB")
                print("Largest runs:")
                for item in result["runs"][:20]:
                    print(f"{item['bytes'] / 1024**2:10.2f} MiB {item['status']:12} {item['directory']}")
        elif args.command == "read":
            print(json.dumps(read_artifact_json(args.path), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(archive_execution(args.run_id, apply=args.apply), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, sqlite3.Error, zipfile.BadZipFile) as exc:
        parser.exit(2, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
