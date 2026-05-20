"""Dext API client — fetches item metadata and downloads invoice documents."""

import httpx
from trn_assistant import config


class DextClient:
    def __init__(self):
        self._base = config.DEXT_API_BASE_URL

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {config.DEXT_API_KEY()}",
            "Accept": "application/json",
        }

    async def get_item(self, item_id: str) -> dict:
        """Return item metadata including supplier name and document reference."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/items/{item_id}",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_document_bytes(self, item_id: str) -> tuple[bytes, str]:
        """
        Download the original invoice document for an item.
        Returns (raw_bytes, content_type).
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/items/{item_id}/document",
                headers=self._headers(),
                timeout=60,
                follow_redirects=True,
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "application/pdf")
            return resp.content, content_type

    async def get_document_bytes_from_url(self, url: str) -> tuple[bytes, str]:
        """Download a document from an arbitrary URL (e.g. pre-signed S3 link from webhook)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                timeout=60,
                follow_redirects=True,
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "application/pdf")
            return resp.content, content_type
