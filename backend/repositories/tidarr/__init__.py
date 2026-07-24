"""Tidarr-backed Tidal acquisition client."""

from .tidarr_client import TidarrClient, TidarrError, TidarrSearchResult

__all__ = ["TidarrClient", "TidarrError", "TidarrSearchResult"]
