#!/usr/bin/env python3
"""Synchronize conservative edition-tag changes into DroppedNeedle's index.

The audio restoration manifest remains authoritative. This helper updates only
rows whose current indexed TITLE/ALBUM still equal the manifest's old values,
so concurrent or newer metadata is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import unicodedata
from pathlib import Path

from unidecode import unidecode


def fold(value: str) -> str:
    text = unicodedata.normalize("NFC", value or "").lower()
    # Mirror DroppedNeedle's title fold without transliterating CJK.
    has_cjk = any(
        low <= ord(char) <= high
        for char in text
        for low, high in (
            (0x4E00, 0x9FFF),
            (0x3040, 0x309F),
            (0x30A0, 0x30FF),
            (0x3400, 0x4DBF),
        )
    )
    return text if has_cjk else unidecode(text)


def records(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if not rows or rows[0].get("kind") != "edition-tag-cleanup":
        raise ValueError(f"not an edition cleanup manifest: {path}")
    return rows[1:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    args = parser.parse_args()

    db = sqlite3.connect(args.database, timeout=60)
    matched = 0
    drifted = 0
    try:
        db.execute("BEGIN IMMEDIATE")
        for manifest in args.manifest:
            for item in records(manifest):
                cursor = db.execute(
                    """
                    UPDATE library_files
                    SET track_title = ?,
                        album_title = ?,
                        track_title_folded = ?,
                        album_title_folded = ?
                    WHERE file_path = ?
                      AND track_title = ?
                      AND album_title = ?
                      AND deleted_at IS NULL
                    """,
                    (
                        item["new_title"],
                        item["new_album"],
                        fold(item["new_title"]),
                        fold(item["new_album"]),
                        item["path"],
                        item["old_title"],
                        item["old_album"],
                    ),
                )
                if cursor.rowcount:
                    matched += cursor.rowcount
                else:
                    drifted += 1
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    print(f"matched={matched} drifted={drifted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
