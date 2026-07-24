"""DownloadClientProtocol adapter for jobs owned and post-processed by Tidarr."""

from pathlib import Path

from models.common import ServiceStatus
from repositories.protocols.download_client import (
    DownloadTaskStatus,
    EnqueueRequest,
    MountDiagnosis,
    TaskHandle,
)

from .tidarr_client import TidarrClient


class TidarrDownloadClient:
    def __init__(self, client: TidarrClient):
        self._client = client

    @property
    def client_name(self) -> str:
        return "tidarr"

    def is_configured(self) -> bool:
        return self._client.configured

    async def health_check(self) -> ServiceStatus:
        try:
            version = await self._client.health()
            return ServiceStatus(status="ok", version=version, message="Tidarr connected")
        except Exception as exc:  # noqa: BLE001
            return ServiceStatus(status="error", message=str(exc))

    async def enqueue(self, request: EnqueueRequest) -> TaskHandle:
        tidal_id = request.nzb_url or ""
        media_type = request.job_name or "album"
        await self._client.enqueue(tidal_id, media_type)
        return TaskHandle(source="tidarr", username=tidal_id, job_name=media_type)

    async def get_status(self, handle: TaskHandle) -> DownloadTaskStatus:
        item = await self._client.queue_item(handle.username)
        if item is None:
            return DownloadTaskStatus(task_id=handle.username, status="queued")
        raw = str(item.get("status") or "").lower()
        status = {
            "finished": "completed",
            "error": "failed",
            "download": "downloading",
            "processing": "downloading",
            "queue_processing": "downloading",
            "queue_download": "queued",
            "queue": "queued",
        }.get(raw, "queued")
        return DownloadTaskStatus(
            task_id=handle.username,
            status=status,
            files_total=1,
            files_completed=1 if status == "completed" else 0,
            progress_percent=100.0 if status == "completed" else 0.0,
            error="Tidarr download failed" if status == "failed" else None,
            has_active_transfer=raw in ("download", "processing", "queue_processing"),
            matched_transfers=1,
        )

    async def cancel(self, handle: TaskHandle) -> bool:
        return await self._client.cancel(handle.username)

    async def list_completed_files(self, handle: TaskHandle) -> list[Path]:  # noqa: ARG002
        return []

    async def get_file_path(
        self, handle: TaskHandle, remote_filename: str, size: int | None = None
    ) -> Path | None:  # noqa: ARG002
        return None

    async def diagnose_downloads_mount(self) -> MountDiagnosis:
        return MountDiagnosis(supported=False)
