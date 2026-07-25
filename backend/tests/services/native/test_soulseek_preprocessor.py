from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from services.native.soulseek_preprocessor import (
    SoulseekPreprocessError,
    SoulseekPreprocessor,
)


@pytest.mark.asyncio
async def test_handoff_writes_relative_paths_and_consumes_success_receipt(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "Artist" / "Album" / "01 - Song.flac"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"fixture")
    processor = SoulseekPreprocessor(
        tmp_path, timeout_seconds=2, poll_interval_seconds=0.01
    )

    async def worker() -> None:
        request = tmp_path / "_pipeline" / "requests" / "task-1.json"
        while not request.exists():
            await asyncio.sleep(0.01)
        payload = json.loads(request.read_text(encoding="utf-8"))
        assert payload["paths"] == ["Artist/Album/01 - Song.flac"]
        receipt = tmp_path / "_pipeline" / "receipts" / "task-1.json"
        receipt.write_text('{"status":"ok"}\n', encoding="utf-8")

    worker_task = asyncio.create_task(worker())
    await processor.preprocess("task-1", [audio])
    await worker_task
    assert not (tmp_path / "_pipeline" / "requests" / "task-1.json").exists()
    assert not (tmp_path / "_pipeline" / "receipts" / "task-1.json").exists()


@pytest.mark.asyncio
async def test_handoff_rejects_path_outside_download_root(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"fixture")
    processor = SoulseekPreprocessor(root)

    with pytest.raises(SoulseekPreprocessError, match="outside"):
        await processor.preprocess("task-2", [outside])


@pytest.mark.asyncio
async def test_handoff_surfaces_worker_failure(tmp_path: Path) -> None:
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"fixture")
    processor = SoulseekPreprocessor(
        tmp_path, timeout_seconds=2, poll_interval_seconds=0.01
    )

    async def worker() -> None:
        request = tmp_path / "_pipeline" / "requests" / "task-3.json"
        while not request.exists():
            await asyncio.sleep(0.01)
        receipt = tmp_path / "_pipeline" / "receipts" / "task-3.json"
        receipt.write_text(
            '{"status":"error","error":"ReplayGain failed"}\n',
            encoding="utf-8",
        )

    worker_task = asyncio.create_task(worker())
    with pytest.raises(SoulseekPreprocessError, match="ReplayGain failed"):
        await processor.preprocess("task-3", [audio])
    await worker_task
