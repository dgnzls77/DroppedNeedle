import httpx
import pytest

from repositories.protocols.download_client import EnqueueRequest, TaskHandle
from repositories.tidarr import TidarrClient
from repositories.tidarr.tidarr_download_client import TidarrDownloadClient


def _http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_search_parses_tidal_track_results():
    def handler(request):
        assert request.headers["X-Api-Key"] == "key"
        assert request.url.params["countryCode"] == "US"
        return httpx.Response(200, json={"tracks": {"items": [{
            "id": 42, "title": "Track", "artists": [{"name": "Artist"}],
            "album": {"title": "Album"}, "duration": 201,
        }]}})

    client = TidarrClient(_http(handler), "http://tidarr:8484", "key")
    found = await client.search("Artist Track", "track")
    assert [(item.id, item.title, item.artist, item.album) for item in found] == [
        ("42", "Track", "Artist", "Album")
    ]


@pytest.mark.asyncio
async def test_enqueue_forces_max_stereo_tiddl_settings():
    captured = {}

    def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(201, json={})

    client = TidarrClient(_http(handler), "http://tidarr:8484", "key")
    await client.enqueue("42", "album")
    item = captured["item"]
    assert item["quality"] == "max"
    assert item["atmosFilter"] == "none"
    assert item["url"] == "https://listen.tidal.com/album/42"


@pytest.mark.asyncio
async def test_download_adapter_maps_finished_queue_item():
    def handler(request):
        return httpx.Response(200, json={"queue": [{"id": "42", "status": "finished"}]})

    adapter = TidarrDownloadClient(TidarrClient(_http(handler), "http://tidarr:8484", "key"))
    status = await adapter.get_status(TaskHandle(source="tidarr", username="42"))
    assert status.status == "completed"
    assert status.files_completed == 1


@pytest.mark.asyncio
async def test_download_adapter_enqueue_uses_tidal_identity():
    seen = {}

    def handler(request):
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(201, json={})

    adapter = TidarrDownloadClient(TidarrClient(_http(handler), "http://tidarr:8484", "key"))
    handle = await adapter.enqueue(
        EnqueueRequest(task_id="task", source="tidarr", nzb_url="42", job_name="track")
    )
    assert handle.username == "42"
    assert handle.job_name == "track"
    assert seen["body"]["item"]["type"] == "track"
