"""The ONLY place that talks to Telegram to deliver messages.

Every send goes through `_attempt`, which gives us consistent behaviour:
  * user blocked the bot / chat closed   -> logged, reported, NOT retried
  * chat not found (never pressed Start) -> logged, reported, NOT retried
  * flood control (RetryAfter)           -> wait the requested time, retry
  * network glitch / timeout             -> exponential backoff, retry
  * anything else                        -> logged and reported

Nothing here raises for a normal delivery problem: callers get a
DeliveryResult so one failing student never breaks a whole broadcast, and a
Telegram problem can never be mistaken for a payment/enrollment problem.

Trade-off worth knowing: if a request times out AFTER Telegram already
accepted it, the retry can produce a duplicate message. For class
notifications a rare duplicate is better than a missing message.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from telegram import Bot, InlineKeyboardMarkup, LinkPreviewOptions
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


@dataclass
class DeliveryResult:
    chat_id: int | None
    ok: bool
    error_code: str | None = None
    error: str | None = None
    deduplicated: bool = False        # already delivered earlier, so not sent again
    skipped: bool = False             # we couldn't even try (e.g. user not connected)
    value: Any = field(default=None, repr=False, compare=False)   # raw Telegram response

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "ok": self.ok,
            "error_code": self.error_code,
            "error": self.error,
            "deduplicated": self.deduplicated,
            "skipped": self.skipped,
        }


def _retry_after_seconds(exc: RetryAfter) -> float:
    ra = exc.retry_after                      # int in PTB 21, timedelta in newer releases
    return ra.total_seconds() if isinstance(ra, timedelta) else float(ra)


async def _attempt(chat_id: int, action: Callable[[], Awaitable[Any]], what: str) -> DeliveryResult:
    last_error = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            value = await action()
            return DeliveryResult(chat_id=chat_id, ok=True, value=value)

        except Forbidden as exc:
            # The user blocked the bot, deleted the chat, or never allowed messages.
            logger.warning("Cannot send %s to chat %s: blocked/forbidden (%s)", what, chat_id, exc)
            return DeliveryResult(chat_id, False, "blocked_or_forbidden", str(exc))

        except BadRequest as exc:                 # NB: BadRequest subclasses NetworkError, so catch it first
            code = "chat_not_found" if "chat not found" in str(exc).lower() else "bad_request"
            logger.error("Telegram rejected %s for chat %s: %s", what, chat_id, exc)
            return DeliveryResult(chat_id, False, code, str(exc))

        except RetryAfter as exc:
            delay = _retry_after_seconds(exc) + 0.5
            last_error = f"rate limited by Telegram (retry after {delay:.0f}s)"
            logger.warning("Flood control sending %s to chat %s; attempt %d/%d", what, chat_id, attempt, MAX_ATTEMPTS)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(delay)

        except (TimedOut, NetworkError) as exc:
            last_error = f"network problem: {exc}"
            logger.warning("Network problem sending %s to chat %s (attempt %d/%d): %s", what, chat_id, attempt, MAX_ATTEMPTS, exc)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(2 ** (attempt - 1))       # 1s, 2s

        except TelegramError as exc:
            logger.error("Telegram error sending %s to chat %s: %s", what, chat_id, exc)
            return DeliveryResult(chat_id, False, "telegram_error", str(exc))

    logger.error("Giving up sending %s to chat %s after %d attempts: %s", what, chat_id, MAX_ATTEMPTS, last_error)
    return DeliveryResult(chat_id, False, "retries_exhausted", last_error)


async def send_text(
    bot: Bot, chat_id: int, text: str, *, reply_markup: InlineKeyboardMarkup | None = None
) -> DeliveryResult:
    """bot.send_message(chat_id, text) with retries and error classification."""
    return await _attempt(
        chat_id,
        lambda: bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        ),
        "text message",
    )


async def send_document(
    bot: Bot, chat_id: int, source: str, *, filename: str | None = None, caption: str | None = None
) -> DeliveryResult:
    """Send a file by URL (Telegram fetches it) or by an existing Telegram file_id."""
    return await _attempt(
        chat_id,
        lambda: bot.send_document(
            chat_id=chat_id,
            document=source,
            filename=filename,
            caption=caption[:1000] if caption else None,
            write_timeout=60,
        ),
        "document",
    )


class GuideVideo:
    """The onboarding video sent to every new student.

    Source, in order of preference:
      1. a Telegram file_id (GUIDE_VIDEO_FILE_ID) - instant, nothing to upload
      2. a public https link (GUIDE_VIDEO_URL, up to ~20 MB) - Telegram downloads it itself
      3. a local file (assets/guide.mp4) - uploaded once
    After the first successful send Telegram returns a file_id which we cache, so every later
    student gets the video instantly. The bot logs that id so you can pin it in GUIDE_VIDEO_FILE_ID.
    """

    def __init__(self, path: Path, file_id: str | None = None, url: str | None = None):
        self._path = path
        self._file_id = file_id
        self._url = url

    def _remember(self, result: DeliveryResult) -> None:
        video = getattr(result.value, "video", None)
        if result.ok and video is not None and not self._file_id:
            self._file_id = video.file_id
            logger.info("Guide video sent once; cached file_id for reuse. Set GUIDE_VIDEO_FILE_ID=%s to keep it across restarts.", video.file_id)

    async def send(self, bot: Bot, chat_id: int, caption: str) -> DeliveryResult:
        remote = self._file_id or self._url
        if remote:
            result = await _attempt(
                chat_id,
                lambda: bot.send_video(chat_id=chat_id, video=remote, caption=caption, supports_streaming=True, read_timeout=120, write_timeout=120),
                "guide video",
            )
            self._remember(result)
            return result

        if not self._path.is_file():
            logger.error("Guide video not configured: set GUIDE_VIDEO_URL / GUIDE_VIDEO_FILE_ID or place a file at %s", self._path)
            return DeliveryResult(chat_id, False, "video_unavailable", "guide video is not configured on the bot")

        async def upload() -> Any:
            with self._path.open("rb") as fh:
                return await bot.send_video(
                    chat_id=chat_id, video=fh, caption=caption, supports_streaming=True,
                    read_timeout=120, write_timeout=120,
                )

        result = await _attempt(chat_id, upload, "guide video (upload)")
        self._remember(result)
        return result
