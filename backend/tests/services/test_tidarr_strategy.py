from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from repositories.tidarr.tidarr_client import TidarrSearchResult
from services.native.acquisition import strategy as strategy_module
from services.native.acquisition.errors import OrchestrationError
from services.native.acquisition.strategy import TidarrStrategy


def _strategy(tmp_path, results):
    tidarr = MagicMock()
    tidarr.search = AsyncMock(return_value=results)
    return TidarrStrategy(
        client=MagicMock(),
        tidarr=tidarr,
        store=MagicMock(),
        scanner=MagicMock(),
        library=MagicMock(),
        library_paths=[],
        staging=tmp_path,
        manifest_codec=MagicMock(),
        naming_template="",
    )


def _album_task(title="Greatest Hits"):
    return SimpleNamespace(
        download_type="album",
        artist_name="Example Artist",
        album_title=title,
        track_title=None,
        year=1990,
    )


@pytest.mark.asyncio
async def test_tidarr_prefers_matching_remaster_over_original(tmp_path):
    strategy = _strategy(
        tmp_path,
        [
            TidarrSearchResult(
                id="original",
                media_type="album",
                title="Greatest Hits",
                artist="Example Artist",
                year=1990,
            ),
            TidarrSearchResult(
                id="remaster",
                media_type="album",
                title="Greatest Hits [2011 Remastered]",
                artist="Example Artist",
                year=2011,
            ),
        ],
    )

    candidates = await strategy.search_and_score(
        _album_task(), timeout=30.0, auto=0.7, manual=0.5
    )

    assert [candidate.tidal_id for candidate in candidates[:2]] == [
        "remaster",
        "original",
    ]
    assert candidates[0].tier == "auto"
    assert candidates[1].tier == "auto"


@pytest.mark.asyncio
async def test_tidarr_accepts_original_when_no_remaster_exists(tmp_path):
    strategy = _strategy(
        tmp_path,
        [
            TidarrSearchResult(
                id="original",
                media_type="album",
                title="Greatest Hits",
                artist="Example Artist",
                year=1990,
            )
        ],
    )

    candidates = await strategy.search_and_score(
        _album_task(), timeout=30.0, auto=0.7, manual=0.5
    )

    assert candidates[0].tidal_id == "original"
    assert candidates[0].tier == "auto"


@pytest.mark.asyncio
async def test_tidarr_does_not_promote_unrelated_remaster(tmp_path):
    strategy = _strategy(
        tmp_path,
        [
            TidarrSearchResult(
                id="correct",
                media_type="album",
                title="Greatest Hits",
                artist="Example Artist",
                year=1990,
            ),
            TidarrSearchResult(
                id="wrong-remaster",
                media_type="album",
                title="Another Album [Remastered]",
                artist="Example Artist",
                year=2011,
            ),
        ],
    )

    candidates = await strategy.search_and_score(
        _album_task(), timeout=30.0, auto=0.7, manual=0.5
    )

    assert candidates[0].tidal_id == "correct"
    assert candidates[0].tier == "auto"
    assert candidates[1].tidal_id == "wrong-remaster"
    assert candidates[1].tier != "auto"


@pytest.mark.asyncio
async def test_tidarr_keeps_deluxe_remaster_for_manual_review(tmp_path):
    strategy = _strategy(
        tmp_path,
        [
            TidarrSearchResult(
                id="deluxe-remaster",
                media_type="album",
                title="Greatest Hits [Deluxe Remastered Edition]",
                artist="Example Artist",
                year=2011,
            )
        ],
    )

    candidates = await strategy.search_and_score(
        _album_task(), timeout=30.0, auto=0.7, manual=0.5
    )

    assert candidates[0].tidal_id == "deluxe-remaster"
    assert candidates[0].tier == "manual"


@pytest.mark.asyncio
async def test_tidarr_standard_original_beats_deluxe_remaster(tmp_path):
    strategy = _strategy(
        tmp_path,
        [
            TidarrSearchResult(
                id="deluxe-remaster",
                media_type="album",
                title="Greatest Hits [Expanded Deluxe Remastered Edition]",
                artist="Example Artist",
                year=2011,
            ),
            TidarrSearchResult(
                id="original",
                media_type="album",
                title="Greatest Hits",
                artist="Example Artist",
                year=1990,
            ),
        ],
    )

    candidates = await strategy.search_and_score(
        _album_task(), timeout=30.0, auto=0.7, manual=0.5
    )

    assert candidates[0].tidal_id == "original"
    assert candidates[0].tier == "auto"
    assert candidates[1].tidal_id == "deluxe-remaster"
    assert candidates[1].tier == "manual"


@pytest.mark.asyncio
async def test_tidarr_completion_scans_only_matching_artist_folder(tmp_path):
    root = tmp_path / "music"
    artist = root / "Example Artist"
    artist.mkdir(parents=True)
    scanner = MagicMock()
    scanner.scan = AsyncMock()
    library = MagicMock()
    library.get_file_rows_for_album = AsyncMock(return_value=[])
    strategy = TidarrStrategy(
        client=MagicMock(),
        tidarr=MagicMock(),
        store=MagicMock(),
        scanner=scanner,
        library=library,
        library_paths=[root],
        staging=tmp_path,
        manifest_codec=MagicMock(),
        naming_template="",
    )
    task = _album_task()
    task.release_group_mbid = "rg-1"

    await strategy.import_files(task, MagicMock(), completed=True)

    scanner.scan.assert_awaited_once_with([artist])


@pytest.mark.asyncio
async def test_tidarr_completion_never_falls_back_to_full_library(
    tmp_path, monkeypatch
):
    root = tmp_path / "music"
    root.mkdir()
    scanner = MagicMock()
    scanner.scan = AsyncMock()
    library = MagicMock()
    library.get_file_rows_for_album = AsyncMock(return_value=[])
    strategy = TidarrStrategy(
        client=MagicMock(),
        tidarr=MagicMock(),
        store=MagicMock(),
        scanner=scanner,
        library=library,
        library_paths=[root],
        staging=tmp_path,
        manifest_codec=MagicMock(),
        naming_template="",
    )
    task = _album_task()
    task.release_group_mbid = "rg-1"
    monkeypatch.setattr(strategy_module, "_TIDARR_LIBRARY_SETTLE_SECONDS", 0.0)

    with pytest.raises(
        OrchestrationError,
        match="final artist folder did not appear",
    ):
        await strategy.import_files(task, MagicMock(), completed=True)

    scanner.scan.assert_not_awaited()
