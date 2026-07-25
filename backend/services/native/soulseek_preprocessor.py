"""Filesystem handoff for the operator-owned Soulseek processing pipeline."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class SoulseekPreprocessError(RuntimeError):
    """The external Beets/ReplayGain pass did not complete successfully."""


class SoulseekPreprocessor:
    """Request Beets then ReplayGain for files still below the slskd mount.

    DroppedNeedle does not execute or configure either tool. The host-side
    worker consumes a JSON request and returns an atomic receipt, after which
    FileProcessor performs its normal verification and final-library move.
    """

    def __init__(
        self,
        downloads_root: Path,
        *,
        timeout_seconds: float = 1800.0,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._root = Path(downloads_root)
        self._timeout = max(1.0, timeout_seconds)
        self._poll_interval = max(0.05, poll_interval_seconds)
        self._requests = self._root / "_pipeline" / "requests"
        self._receipts = self._root / "_pipeline" / "receipts"

    async def preprocess(self, task_id: str, paths: list[Path]) -> None:
        if not _SAFE_TASK_ID.fullmatch(task_id):
            raise SoulseekPreprocessError("invalid task identifier")
        root = self._root.resolve()
        relative_paths: list[str] = []
        for raw_path in paths:
            try:
                resolved = Path(raw_path).resolve(strict=True)
                relative = resolved.relative_to(root)
            except (OSError, ValueError) as exc:
                raise SoulseekPreprocessError(
                    "download path is outside the Soulseek mount"
                ) from exc
            if not resolved.is_file():
                raise SoulseekPreprocessError("download path is not a file")
            relative_paths.append(relative.as_posix())
        if not relative_paths:
            return

        await asyncio.to_thread(self._write_request, task_id, relative_paths)
        receipt_path = self._receipts / f"{task_id}.json"
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            receipt = await asyncio.to_thread(self._read_receipt, receipt_path)
            if receipt is not None:
                if receipt.get("status") == "ok":
                    await asyncio.to_thread(self._cleanup, task_id)
                    return
                raise SoulseekPreprocessError(
                    str(receipt.get("error") or "processing failed")
                )
            await asyncio.sleep(self._poll_interval)
        raise SoulseekPreprocessError("Beets/ReplayGain processing timed out")

    def _write_request(self, task_id: str, paths: list[str]) -> None:
        self._requests.mkdir(parents=True, exist_ok=True)
        self._receipts.mkdir(parents=True, exist_ok=True)
        request_path = self._requests / f"{task_id}.json"
        receipt_path = self._receipts / f"{task_id}.json"
        # A completed receipt is deliberately restart-idempotent.
        if receipt_path.exists() or request_path.exists():
            return
        payload = {
            "version": 1,
            "task_id": task_id,
            "created_at": time.time(),
            "paths": paths,
        }
        temp_path = request_path.with_suffix(f".tmp-{os.getpid()}")
        with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, request_path)

    @staticmethod
    def _read_receipt(path: Path) -> dict | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise SoulseekPreprocessError("invalid processing receipt") from exc

    def _cleanup(self, task_id: str) -> None:
        (self._requests / f"{task_id}.json").unlink(missing_ok=True)
        (self._receipts / f"{task_id}.json").unlink(missing_ok=True)
