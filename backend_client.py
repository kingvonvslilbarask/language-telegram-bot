"""Client for OUR web backend (the source of truth).

The bot only needs two things from it:
  * link a Telegram chat to a website account after the student presses Start
  * (optional) look up a person's upcoming classes for /myclasses

Contract (see README): the bot authenticates with the X-Api-Key header.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class BackendError(Exception):
    """The web backend is unreachable or returned an unexpected response."""


class InvalidLinkToken(BackendError):
    """The deep-link token is unknown, expired or already used."""


@dataclass
class LinkResult:
    display_name: str
    role: str          # "student" | "teacher" | "admin"


class WebBackendClient:
    def __init__(self, base_url: str | None, api_key: str | None):
        self.enabled = bool(base_url and api_key)
        self._client: httpx.AsyncClient | None = None
        if self.enabled:
            self._client = httpx.AsyncClient(
                base_url=base_url.rstrip("/") + "/",
                headers={"X-Api-Key": api_key, "User-Agent": "azela-telegram-bot/1.0"},
                timeout=httpx.Timeout(10.0, connect=5.0),
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def link_telegram(self, *, token: str, chat_id: int, username: str | None, first_name: str | None) -> LinkResult:
        """Tell the web backend: 'the person who owns <token> is Telegram chat <chat_id>'.

        The backend must verify the token (unknown/expired/used -> 404/410) and,
        if valid, store chat_id on that user's profile. chat_id - NOT the
        username - is the permanent identifier the bot uses to message people.
        """
        data = await self._request(
            "POST", "telegram-link",
            json={"token": token, "chat_id": chat_id, "username": username, "first_name": first_name},
            invalid_statuses={400, 404, 410, 422},
        )
        try:
            return LinkResult(display_name=str(data.get("display_name") or first_name or "there"), role=str(data.get("role") or "student"))
        except AttributeError as exc:
            raise BackendError("unexpected response shape from /telegram/link") from exc

    async def get_classes(self, chat_id: int) -> list[dict[str, Any]]:
        data = await self._request("GET", "telegram-classes", params={"chat_id": chat_id})
        classes = data.get("classes", []) if isinstance(data, dict) else []
        return [c for c in classes if isinstance(c, dict)]

    async def _request(self, method: str, path: str, *, invalid_statuses: set[int] | None = None, **kwargs: Any) -> Any:
        if not self.enabled or self._client is None:
            raise BackendError("web backend is not configured (WEB_BACKEND_URL / WEB_BACKEND_API_KEY)")
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise BackendError(f"could not reach web backend: {exc.__class__.__name__}") from exc

        if invalid_statuses and resp.status_code in invalid_statuses:
            raise InvalidLinkToken(f"backend rejected the token (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise BackendError(f"web backend returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise BackendError("web backend returned invalid JSON") from exc
