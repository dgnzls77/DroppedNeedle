#!/usr/bin/env python3
"""Conservatively remove cosmetic remaster suffixes from audio TITLE/ALBUM tags.

The tool never renames files and never removes meaningful edition labels such as
Live, Demo, Mono, Remix, Deluxe, Expanded, or Extended. Apply mode writes a JSONL
restoration manifest before changing the first file; rollback mode restores only
records whose current values still match the manifest's expected cleaned values.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from mutagen import File as MutagenFile

AUDIO_SUFFIXES = {".flac", ".m4a", ".mp4"}
_REMASTER_SUFFIX = re.compile(
    r"""
    \s*
    (?:
        [\[(]\s*
        (?:
            (?:\d{4}\s+)?remaster(?:ed)?
            (?:\s+(?:version|edition))?
            (?:\s+\d{4})?
          |
            remaster(?:ed)?\s+\d{4}
            (?:\s+(?:version|edition))?
        )
        \s*[\])]
      |
        [-\u2013\u2014:]\s*
        (?:\d{4}\s+)?remaster(?:ed)?
        (?:\s+(?:version|edition))?
        (?:\s+\d{4})?
    )
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def clean_title(value: str) -> str:
    """Strip one or more safe trailing remaster-only suffixes."""
    current = (value or "").strip()
    while current:
        cleaned = _REMASTER_SUFFIX.sub("", current).rstrip()
        if cleaned == current:
            break
        current = cleaned
    return current


def _first(tags: Any, key: str) -> str:
    values = tags.get(key) if tags is not None else None
    if not values:
        return ""
    if isinstance(values, (list, tuple)):
        return str(values[0] or "")
    return str(values)


def _audio_paths(root: Path) -> Iterable[Path]:
    if root.is_file():
        if root.suffix.lower() in AUDIO_SUFFIXES:
            yield root
        return
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
            yield path


def _change_for(path: Path) -> dict[str, Any] | None:
    audio = MutagenFile(path, easy=True)
    if audio is None or audio.tags is None:
        return None
    old_title = _first(audio.tags, "title")
    old_album = _first(audio.tags, "album")
    new_title = clean_title(old_title)
    new_album = clean_title(old_album)
    if (new_title, new_album) == (old_title, old_album):
        return None
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "old_title": old_title,
        "new_title": new_title,
        "old_album": old_album,
        "new_album": new_album,
    }


def collect(
    root: Path, explicit_paths: list[Path] | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    changes: list[dict[str, Any]] = []
    errors: list[str] = []
    paths = explicit_paths if explicit_paths is not None else list(_audio_paths(root))

    def inspect(path: Path) -> tuple[dict[str, Any] | None, str | None]:
        try:
            return _change_for(path), None
        except Exception as exc:  # noqa: BLE001 - inventory must report and continue
            return None, f"{path}: {type(exc).__name__}"

    # Audio metadata inventory is dominated by independent small reads. A
    # bounded pool keeps a large library practical without allowing concurrent
    # writes; apply_changes below remains deliberately sequential.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        inspected = executor.map(inspect, paths)
        for change, error in inspected:
            if error is not None:
                errors.append(error)
                continue
            if change is not None:
                changes.append(change)
    return changes, errors


def _write_manifest(
    manifest: Path, root: Path, changes: list[dict[str, Any]], errors: list[str]
) -> None:
    if manifest.exists():
        raise FileExistsError(f"Refusing to overwrite manifest: {manifest}")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(
                {
                    "kind": "edition-tag-cleanup",
                    "version": 1,
                    "created_at": time.time(),
                    "root": str(root),
                    "change_count": len(changes),
                    "inventory_error_count": len(errors),
                },
                sort_keys=True,
            )
            + "\n"
        )
        for change in changes:
            handle.write(json.dumps(change, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _set_tags(path: Path, title: str, album: str) -> None:
    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError("unsupported audio file")
    if audio.tags is None:
        audio.add_tags()
    if title:
        audio["title"] = [title]
    if album:
        audio["album"] = [album]
    audio.save()

    # Re-open the file so apply mode proves the values were persisted before
    # reporting success. The restoration manifest already contains the original
    # values if a particular codec or tag write fails this check.
    verified = MutagenFile(path, easy=True)
    if verified is None or verified.tags is None:
        raise ValueError("tag verification failed")
    if (_first(verified.tags, "title"), _first(verified.tags, "album")) != (
        title,
        album,
    ):
        raise ValueError("tag verification mismatch")


def apply_changes(
    root: Path, manifest: Path, changes: list[dict[str, Any]], errors: list[str]
) -> tuple[int, list[str]]:
    _write_manifest(manifest, root, changes, errors)
    changed = 0
    failures: list[str] = []
    for record in changes:
        path = Path(record["path"])
        try:
            path.relative_to(root)
            _set_tags(path, record["new_title"], record["new_album"])
            changed += 1
        except Exception as exc:  # noqa: BLE001 - manifest retains rollback evidence
            failures.append(f"{path}: {type(exc).__name__}")
    return changed, failures


def _manifest_records(manifest: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with manifest.open("r", encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    if not lines or lines[0].get("kind") != "edition-tag-cleanup":
        raise ValueError("Not an edition-tag cleanup manifest")
    return lines[0], lines[1:]


def rollback(manifest: Path) -> tuple[int, int, list[str]]:
    header, records = _manifest_records(manifest)
    root = Path(header["root"]).resolve()
    restored = 0
    drifted = 0
    failures: list[str] = []
    for record in records:
        path = Path(record["path"]).resolve()
        try:
            path.relative_to(root)
            audio = MutagenFile(path, easy=True)
            if audio is None or audio.tags is None:
                raise ValueError("unsupported or untagged audio file")
            current = (_first(audio.tags, "title"), _first(audio.tags, "album"))
            expected = (record["new_title"], record["new_album"])
            if current != expected:
                drifted += 1
                continue
            _set_tags(path, record["old_title"], record["old_album"])
            restored += 1
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{path}: {type(exc).__name__}")
    return restored, drifted, failures


def _print_summary(changes: list[dict[str, Any]], errors: list[str]) -> None:
    title_changes = sum(r["old_title"] != r["new_title"] for r in changes)
    album_changes = sum(r["old_album"] != r["new_album"] for r in changes)
    print(f"files={len(changes)} title_changes={title_changes} album_changes={album_changes}")
    print(f"inventory_errors={len(errors)}")
    for record in changes[:20]:
        print(
            json.dumps(
                {
                    "path": record["path"],
                    "title": [record["old_title"], record["new_title"]],
                    "album": [record["old_album"], record["new_album"]],
                },
                ensure_ascii=False,
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--paths-file",
        type=Path,
        help="Optional newline-delimited candidate paths; every path must be below --root.",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()

    if args.rollback:
        if args.apply or args.root or args.paths_file or not args.manifest:
            parser.error("--rollback requires only --manifest")
        restored, drifted, failures = rollback(args.manifest)
        print(f"restored={restored} drifted={drifted} failures={len(failures)}")
        return 1 if failures else 0

    if not args.root:
        parser.error("--root is required")
    root = args.root.resolve()
    if not root.exists() or (
        root.is_file() and root.suffix.lower() not in AUDIO_SUFFIXES
    ):
        parser.error(f"root is not an audio file or directory: {root}")
    explicit_paths = None
    if args.paths_file:
        explicit_paths = []
        for raw_path in args.paths_file.read_text(encoding="utf-8").splitlines():
            if not raw_path.strip():
                continue
            candidate = Path(raw_path.strip()).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                parser.error(f"candidate path is outside root: {candidate}")
            if candidate.is_file() and candidate.suffix.lower() in AUDIO_SUFFIXES:
                explicit_paths.append(candidate)
    changes, errors = collect(root, explicit_paths)
    _print_summary(changes, errors)
    if not args.apply:
        return 1 if errors else 0
    if not args.manifest:
        parser.error("--apply requires --manifest")
    changed, failures = apply_changes(root, args.manifest, changes, errors)
    print(f"changed={changed} failures={len(failures)} manifest={args.manifest}")
    return 1 if errors or failures else 0


if __name__ == "__main__":
    sys.exit(main())
