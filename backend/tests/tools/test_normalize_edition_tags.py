from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cleaner():
    path = Path(__file__).parents[3] / "scripts" / "normalize_edition_tags.py"
    spec = importlib.util.spec_from_file_location("normalize_edition_tags", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("Song (2007 Remaster)", "Song"),
        ("Song [Remastered]", "Song"),
        ("Song (Remastered 2011)", "Song"),
        ("Song - 2011 Remastered", "Song"),
        ("Song – 2011 Remastered", "Song"),
        ("Song — Remastered", "Song"),
        ("Song [Long Version] [2002 Remaster]", "Song [Long Version]"),
        ("(You Gotta Walk) Don't Look Back [2002 Remaster]", "(You Gotta Walk) Don't Look Back"),
    ],
)
def test_clean_title_removes_only_safe_remaster_suffixes(cleaner, original, expected):
    assert cleaner.clean_title(original) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Song (Demo / Remastered 2016)",
        "Song (Mono / Remastered 2022)",
        "Song (Remix/Remastered 2009)",
        "Song (Live 1975/Remastered)",
        "Album (Deluxe Remastered Edition)",
        "Album (Remastered / Expanded Edition)",
        "Album (25th Anniversary Remaster)",
    ],
)
def test_clean_title_preserves_complex_or_meaningful_editions(cleaner, title):
    assert cleaner.clean_title(title) == title


def test_index_sync_updates_only_rows_matching_manifest_old_values(
    tmp_path: Path,
) -> None:
    script = Path(__file__).parents[3] / "scripts" / "sync_edition_manifest_to_library_db.py"
    spec = importlib.util.spec_from_file_location("sync_edition_index", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    database = tmp_path / "library.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE library_files (
            file_path TEXT,
            track_title TEXT,
            album_title TEXT,
            track_title_folded TEXT,
            album_title_folded TEXT,
            deleted_at REAL
        )
        """
    )
    connection.executemany(
        "INSERT INTO library_files VALUES (?, ?, ?, ?, ?, NULL)",
        [
            ("/music/a.flac", "Song (2007 Remaster)", "Album", "old", "album"),
            ("/music/b.flac", "User Edit", "Album", "user edit", "album"),
        ],
    )
    connection.commit()
    connection.close()

    manifest = tmp_path / "manifest.jsonl"
    header = {"kind": "edition-tag-cleanup", "version": 1}
    changes = [
        {
            "path": "/music/a.flac",
            "old_title": "Song (2007 Remaster)",
            "new_title": "Song",
            "old_album": "Album",
            "new_album": "Album",
        },
        {
            "path": "/music/b.flac",
            "old_title": "Song (2007 Remaster)",
            "new_title": "Song",
            "old_album": "Album",
            "new_album": "Album",
        },
    ]
    manifest.write_text(
        "\n".join(json.dumps(row) for row in [header, *changes]) + "\n",
        encoding="utf-8",
    )

    rows = module.records(manifest)
    assert len(rows) == 2
    db = sqlite3.connect(database)
    db.execute("BEGIN IMMEDIATE")
    first = rows[0]
    changed = db.execute(
        """
        UPDATE library_files
        SET track_title=?, album_title=?, track_title_folded=?, album_title_folded=?
        WHERE file_path=? AND track_title=? AND album_title=? AND deleted_at IS NULL
        """,
        (
            first["new_title"],
            first["new_album"],
            module.fold(first["new_title"]),
            module.fold(first["new_album"]),
            first["path"],
            first["old_title"],
            first["old_album"],
        ),
    ).rowcount
    db.commit()
    result = db.execute(
        "SELECT file_path, track_title FROM library_files ORDER BY file_path"
    ).fetchall()
    db.close()
    assert changed == 1
    assert result == [("/music/a.flac", "Song"), ("/music/b.flac", "User Edit")]
