"""Configuration, loaded from environment variables.

Nothing secret is ever hard-coded. Copy `.env.example` to `.env` for local
development; in production set real environment variables on the host.
Startup fails loudly (listing every problem at once) if anything is wrong.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
# Telegram only allows these characters in the webhook secret_token.
_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    bot_token: str
    webhook_base_url: str           # public https URL Telegram will call
    webhook_secret: str             # validates that requests really come from Telegram
    internal_api_key: str           # validates that requests really come from OUR web backend
    web_backend_url: str | None     # our web backend (for /start linking and /myclasses)
    web_backend_api_key: str | None
    admin_chat_ids: tuple[int, ...]
    school_name: str
    timezone: str
    guide_video_path: Path
    guide_video_file_id: str | None
    guide_video_url: str | None     # public https link to the video (used when the file isn't in the repo)
    host: str
    port: int
    log_level: str
    ledger_path: Path


def _parse_chat_ids(raw: str, errors: list[str]) -> tuple[int, ...]:
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            errors.append(f"ADMIN_CHAT_IDS contains a non-numeric value: {part!r}")
    return tuple(dict.fromkeys(ids))  # de-duplicate, keep order


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    if environ is None:
        load_dotenv()
        environ = os.environ

    errors: list[str] = []

    def get(name: str, default: str = "") -> str:
        return (environ.get(name) or default).strip()

    def required(name: str) -> str:
        value = get(name)
        if not value:
            errors.append(f"{name} is required")
        return value

    # NOTE: error messages never include secret values.
    bot_token = required("TELEGRAM_BOT_TOKEN")
    if bot_token and not _TOKEN_RE.match(bot_token):
        errors.append("TELEGRAM_BOT_TOKEN does not look like a valid BotFather token")

    # On Render the public address is provided automatically as RENDER_EXTERNAL_URL.
    webhook_base_url = (get("WEBHOOK_BASE_URL") or get("RENDER_EXTERNAL_URL")).rstrip("/")
    if not webhook_base_url:
        errors.append("WEBHOOK_BASE_URL is required (on Render it is filled in automatically)")
    if webhook_base_url and not webhook_base_url.startswith("https://"):
        errors.append("WEBHOOK_BASE_URL must start with https:// (Telegram only calls HTTPS webhooks)")

    webhook_secret = required("TELEGRAM_WEBHOOK_SECRET")
    if webhook_secret and not _SECRET_RE.match(webhook_secret):
        errors.append("TELEGRAM_WEBHOOK_SECRET must be 16-256 chars of A-Z a-z 0-9 _ -")

    internal_api_key = required("INTERNAL_API_KEY")
    if internal_api_key and len(internal_api_key) < 24:
        errors.append("INTERNAL_API_KEY must be at least 24 characters (use a long random value)")

    web_backend_url = get("WEB_BACKEND_URL") or None
    web_backend_api_key = get("WEB_BACKEND_API_KEY") or None
    if bool(web_backend_url) != bool(web_backend_api_key):
        errors.append("WEB_BACKEND_URL and WEB_BACKEND_API_KEY must be set together (or both left empty)")
    if web_backend_url and not web_backend_url.startswith(("https://", "http://")):
        errors.append("WEB_BACKEND_URL must start with http:// or https://")

    guide_video_url = get("GUIDE_VIDEO_URL") or None
    if guide_video_url and not guide_video_url.startswith("https://"):
        errors.append("GUIDE_VIDEO_URL must start with https://")

    admin_chat_ids = _parse_chat_ids(get("ADMIN_CHAT_IDS"), errors)

    timezone = get("SCHOOL_TIMEZONE", "Africa/Addis_Ababa")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"SCHOOL_TIMEZONE {timezone!r} is not a valid IANA timezone")

    try:
        port = int(get("PORT", "8080"))
    except ValueError:
        port = 8080
        errors.append("PORT must be an integer")

    if errors:
        raise ConfigError("Invalid configuration:\n - " + "\n - ".join(errors))

    return Settings(
        bot_token=bot_token,
        webhook_base_url=webhook_base_url,
        webhook_secret=webhook_secret,
        internal_api_key=internal_api_key,
        web_backend_url=web_backend_url,
        web_backend_api_key=web_backend_api_key,
        admin_chat_ids=admin_chat_ids,
        school_name=get("SCHOOL_NAME", "Azela Language School"),
        timezone=timezone,
        guide_video_path=Path(get("GUIDE_VIDEO_PATH", "assets/guide.mp4")),
        guide_video_file_id=get("GUIDE_VIDEO_FILE_ID") or None,
        guide_video_url=guide_video_url,
        host=get("HOST", "0.0.0.0"),
        port=port,
        log_level=get("LOG_LEVEL", "INFO").upper(),
        ledger_path=Path(get("LEDGER_PATH", "data/ledger.sqlite3")),
    )
