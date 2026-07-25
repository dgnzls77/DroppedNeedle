from __future__ import annotations

import importlib.util
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
