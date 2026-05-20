"""
Zoho Books API client — manages vendors (contacts) and ensures TRN is set.

Authentication: OAuth2 with refresh token. Access tokens expire after 1 hour;
this client refreshes transparently on 401.
"""

import time

import httpx
from trn_assistant import config


class _TokenCache:
    access_token: str | None = None
    expires_at: float = 0.0


_cache = _TokenCache()


async def _get_access_token() -> str:
    if _cache.access_token and time.time() < _cache.expires_at - 60:
        return _cache.access_token

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{config.ZOHO_ACCOUNTS_URL}/oauth/v2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": config.ZOHO_CLIENT_ID(),
                "client_secret": config.ZOHO_CLIENT_SECRET(),
                "refresh_token": config.ZOHO_REFRESH_TOKEN(),
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

    _cache.access_token = data["access_token"]
    _cache.expires_at = time.time() + int(data.get("expires_in", 3600))
    return _cache.access_token


def _org_params() -> dict:
    return {"organization_id": config.ZOHO_ORGANIZATION_ID()}


class ZohoClient:
    async def _headers(self) -> dict:
        token = await _get_access_token()
        return {
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json",
        }

    async def find_vendor(self, vendor_name: str) -> dict | None:
        """
        Search Zoho Books contacts for a vendor by name.
        Returns the contact dict or None if not found.
        """
        params = {
            **_org_params(),
            "contact_name_contains": vendor_name,
            "contact_type": "vendor",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{config.ZOHO_BOOKS_BASE_URL}/contacts",
                headers=await self._headers(),
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            contacts = resp.json().get("contacts", [])

        # Prefer exact match, fall back to first result
        for c in contacts:
            if c.get("contact_name", "").lower() == vendor_name.lower():
                return c
        return contacts[0] if contacts else None

    async def create_vendor(self, vendor_name: str, trn: str | None) -> dict:
        """
        Create a new vendor in Zoho Books.
        If TRN is provided the vendor is marked as VAT-registered.
        """
        payload: dict = {
            "contact_name": vendor_name,
            "contact_type": "vendor",
        }
        if trn:
            payload["tax_treatment"] = "vat_registered"
            payload["tax_registration_number"] = trn

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{config.ZOHO_BOOKS_BASE_URL}/contacts",
                headers=await self._headers(),
                params=_org_params(),
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()["contact"]

    async def update_vendor_trn(self, contact_id: str, trn: str) -> dict:
        """Set (or overwrite) the TRN on an existing vendor contact."""
        payload = {
            "tax_treatment": "vat_registered",
            "tax_registration_number": trn,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{config.ZOHO_BOOKS_BASE_URL}/contacts/{contact_id}",
                headers=await self._headers(),
                params=_org_params(),
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()["contact"]

    async def ensure_vendor_has_trn(
        self, vendor_name: str, trn: str
    ) -> tuple[dict, str]:
        """
        Ensure the vendor exists in Zoho Books and has the given TRN.
        Returns (contact_dict, action) where action is one of:
          "created"   — vendor was new, created with TRN
          "updated"   — vendor existed, TRN was missing/different, updated
          "unchanged" — vendor existed and already had this TRN
        """
        existing = await self.find_vendor(vendor_name)

        if existing is None:
            contact = await self.create_vendor(vendor_name, trn)
            return contact, "created"

        existing_trn = existing.get("tax_registration_number", "")
        if existing_trn == trn:
            return existing, "unchanged"

        updated = await self.update_vendor_trn(existing["contact_id"], trn)
        return updated, "updated"
