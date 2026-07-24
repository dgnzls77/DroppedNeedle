"""Small authenticated client for Tidarr's Tidal proxy and download queue."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from core.exceptions import ExternalServiceError


class TidarrError(ExternalServiceError):
    pass


@dataclass(frozen=True)
class TidarrSearchResult:
    id: str
    media_type: str
    title: str
    artist: str
    album: str = ""
    year: int | None = None
    track_count: int | None = None
    duration_seconds: float | None = None


class TidarrClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str, country_code: str = "US"):
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._country_code = country_code.strip().upper() or "US"

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._api_key)

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self._api_key}

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.request(
                method, f"{self._base_url}{path}", headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as exc:
            raise TidarrError("Could not reach Tidarr") from exc
        if response.status_code in (401, 403):
            raise TidarrError("Tidarr API key was rejected")
        if response.status_code >= 400:
            raise TidarrError(f"Tidarr returned HTTP {response.status_code}")
        return response

    async def health(self) -> str:
        response = await self._request("GET", "/api/queue/status", timeout=15.0)
        data = response.json()
        return str(data.get("version") or data.get("status") or "available")

    async def search(self, query: str, media_type: str) -> list[TidarrSearchResult]:
        response = await self._request(
            "GET",
            "/proxy/tidal/v2/search",
            params={
                "query": query,
                "countryCode": self._country_code,
                "limit": "20",
                "offset": "0",
            },
            timeout=30.0,
        )
        data = response.json()
        bucket = "tracks" if media_type == "track" else "albums"
        items = ((data.get(bucket) or {}).get("items") or []) if isinstance(data, dict) else []
        results: list[TidarrSearchResult] = []
        for item in items:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            artists = item.get("artists") or []
            artist = item.get("artist") or (artists[0] if artists else {})
            album = item.get("album") or {}
            release_date = str(item.get("releaseDate") or album.get("releaseDate") or "")
            results.append(
                TidarrSearchResult(
                    id=str(item["id"]),
                    media_type=media_type,
                    title=str(item.get("title") or ""),
                    artist=str(artist.get("name") or item.get("artistName") or ""),
                    album=str(album.get("title") or ""),
                    year=int(release_date[:4]) if release_date[:4].isdigit() else None,
                    track_count=(int(item["numberOfTracks"]) if item.get("numberOfTracks") else None),
                    duration_seconds=(float(item["duration"]) if item.get("duration") else None),
                )
            )
        return results

    async def enqueue(self, tidal_id: str, media_type: str) -> None:
        await self._request(
            "POST",
            "/api/save",
            json={
                "item": {
                    "id": tidal_id,
                    "url": f"https://listen.tidal.com/{media_type}/{tidal_id}",
                    "type": media_type,
                    "status": "queue_download",
                    "quality": "max",
                    "atmosFilter": "none",
                }
            },
            timeout=30.0,
        )

    async def queue_item(self, tidal_id: str) -> dict[str, Any] | None:
        response = await self._request("GET", "/api/queue/list", timeout=30.0)
        data = response.json()
        items = data.get("queue", data) if isinstance(data, dict) else data
        for item in items if isinstance(items, list) else []:
            if str(item.get("id")) == tidal_id:
                return item
        return None

    async def cancel(self, tidal_id: str) -> bool:
        await self._request("DELETE", "/api/remove", json={"id": tidal_id}, timeout=30.0)
        return True
