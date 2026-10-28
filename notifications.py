"""The business flows the web backend triggers:

  payment_confirmed  -> welcome + guide video to the student, and
                        "Student X has enrolled in Course Y" to teacher + admins
  class_scheduled    -> Zoom link to every student (individually) and/or a group
  teacher_message    -> custom text / links / documents to every student

Rules that keep communication accurate and reliable:
  * Each recipient is handled independently; one failure never blocks others.
  * Every send is recorded in the ledger under an idempotency key, so retried
    requests never send duplicates and partial failures can be safely re-run.
  * Results are returned per recipient so the web backend can store the
    NOTIFICATION status separately from payment/enrollment status. A failed
    Telegram message must never make a paid enrollment look failed.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from . import messages
from .config import Settings
from .ledger import DeliveryLedger
from .schemas import ClassScheduled, PaymentConfirmed, Person, TeacherMessage
from .sender import DeliveryResult, GuideVideo, send_document, send_text

logger = logging.getLogger(__name__)

BROADCAST_DELAY = 0.05     # ~20 messages/second, safely under Telegram's ~30/s limit
DEAD_CHAT_CODES = {"blocked_or_forbidden", "chat_not_found"}


def _skipped(chat_id: int | None, reason: str) -> DeliveryResult:
    return DeliveryResult(chat_id=chat_id, ok=False, skipped=True, error_code=reason, error=reason.replace("_", " "))


def _detail(name: str, chat_id: int | None, results: list[DeliveryResult]) -> dict[str, Any]:
    failed = next((r for r in results if not r.ok), None)
    if failed is None:
        status = "duplicate" if results and all(r.deduplicated for r in results) else "delivered"
        return {"name": name, "chat_id": chat_id, "status": status}
    return {
        "name": name, "chat_id": chat_id,
        "status": "skipped" if failed.skipped else "failed",
        "error_code": failed.error_code, "error": failed.error,
    }


class Notifier:
    def __init__(self, bot: Bot, settings: Settings, ledger: DeliveryLedger, video: GuideVideo):
        self.bot = bot
        self.settings = settings
        self.ledger = ledger
        self.video = video

    # ---- helpers -----------------------------------------------------------

    async def _once(self, key: str | None, chat_id: int, send: Callable[[], Awaitable[DeliveryResult]]) -> DeliveryResult:
        """Send at most once per idempotency key; only mark as delivered on success."""
        if key and await self.ledger.has(key):
            return DeliveryResult(chat_id=chat_id, ok=True, deduplicated=True)
        result = await send()
        if result.ok and key:
            await self.ledger.add(key)
        return result

    async def _text_once(self, key: str | None, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> DeliveryResult:
        return await self._once(key, chat_id, lambda: send_text(self.bot, chat_id, text, reply_markup=reply_markup))

    async def _broadcast(
        self, recipients: list[Person], group_chat_id: int | None,
        send_to: Callable[[int], Awaitable[list[DeliveryResult]]],
    ) -> dict[str, Any]:
        details: list[dict[str, Any]] = []
        if group_chat_id is not None:
            details.append(_detail("group", group_chat_id, await send_to(group_chat_id)))
            await asyncio.sleep(BROADCAST_DELAY)

        seen: set[int] = set()
        for person in recipients:
            if person.chat_id is None:
                details.append(_detail(person.name, None, [_skipped(None, "not_connected")]))
                continue
            if person.chat_id in seen:            # same chat listed twice in the request
                continue
            seen.add(person.chat_id)
            details.append(_detail(person.name, person.chat_id, await send_to(person.chat_id)))
            await asyncio.sleep(BROADCAST_DELAY)

        count = lambda status: sum(1 for d in details if d["status"] == status)  # noqa: E731
        return {
            "total": len(details),
            "delivered": count("delivered"),
            "duplicates": count("duplicate"),
            "failed": count("failed"),
            "skipped": count("skipped"),
            "details": details,
        }

    # ---- 1. payment confirmed ---------------------------------------------

    async def payment_confirmed(self, p: PaymentConfirmed) -> dict[str, Any]:
        s = self.settings
        base = p.idempotency_key or f"payment:{p.enrollment_id}"
        teacher_name = p.teacher.name if p.teacher else None
        student_connected = p.student.chat_id is not None

        async def student_flow() -> dict[str, DeliveryResult]:
            if p.student.chat_id is None:
                return {"welcome": _skipped(None, "student_not_connected"), "video": _skipped(None, "student_not_connected")}
            chat_id = p.student.chat_id
            welcome = await self._text_once(
                f"{base}:student:welcome", chat_id,
                messages.welcome(s.school_name, p.student.name, p.course_title, teacher_name, p.schedule_text),
            )
            if welcome.ok:
                video = await self._once(
                    f"{base}:student:video", chat_id,
                    lambda: self.video.send(self.bot, chat_id, messages.video_caption(s.school_name)),
                )
            else:
                video = _skipped(chat_id, "welcome_failed")
            return {"welcome": welcome, "video": video}

        async def staff_flow() -> dict[str, Any]:
            def text_for(audience: str) -> str:
                return messages.staff_new_enrollment(
                    p.student.name, p.course_title, audience=audience, schedule_text=p.schedule_text,
                    teacher_name=teacher_name, amount=p.amount, currency=p.currency,
                    student_connected=student_connected,
                )

            if p.teacher is None:
                teacher_result = _skipped(None, "no_teacher_assigned")
            elif p.teacher.chat_id is None:
                teacher_result = _skipped(None, "teacher_not_connected")
            else:
                teacher_result = await self._text_once(f"{base}:teacher", p.teacher.chat_id, text_for("teacher"))

            admin_ids = tuple(dict.fromkeys([*s.admin_chat_ids, *p.admin_chat_ids]))
            if not admin_ids:
                logger.warning("No admin chat IDs configured (ADMIN_CHAT_IDS); nobody on staff was told about enrollment %s", p.enrollment_id)
            admin_results = [await self._text_once(f"{base}:admin:{aid}", aid, text_for("admin")) for aid in admin_ids]
            return {"teacher": teacher_result, "admins": admin_results}

        # Student and staff messages go out concurrently so staff hear about the
        # enrollment right away, even while the (larger) video is uploading.
        student, staff = await asyncio.gather(student_flow(), staff_flow())

        everything = [*student.values(), staff["teacher"], *staff["admins"]]
        logger.info("payment_confirmed %s: %d/%d deliveries ok", p.enrollment_id, sum(r.ok for r in everything), len(everything))
        return {
            "enrollment_id": p.enrollment_id,
            "student_connected": student_connected,
            "student": {k: v.to_dict() for k, v in student.items()},
            "teacher": staff["teacher"].to_dict(),
            "admins": [r.to_dict() for r in staff["admins"]],
            "all_delivered": all(r.ok for r in everything),
        }

    # ---- 2. class scheduled / updated -------------------------------------

    async def class_scheduled(self, c: ClassScheduled) -> dict[str, Any]:
        text = messages.class_scheduled(
            c.course_title, messages.format_when(c.starts_at, self.settings.timezone),
            c.duration_minutes, c.topic, c.zoom_url, c.is_update,
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎥 Join Zoom", url=c.zoom_url)]])

        async def send_to(chat_id: int) -> list[DeliveryResult]:
            key = f"{c.idempotency_key}:{chat_id}" if c.idempotency_key else None
            return [await self._text_once(key, chat_id, text, keyboard)]

        result = await self._broadcast(c.recipients, c.group_chat_id, send_to)
        logger.info("class_scheduled: %d delivered, %d failed, %d skipped", result["delivered"], result["failed"], result["skipped"])
        return result

    # ---- 3. custom teacher message ----------------------------------------

    async def teacher_message(self, m: TeacherMessage) -> dict[str, Any]:
        body = messages.teacher_message(m.sender_name, m.text, m.links) if (m.text or m.links) else None

        async def send_to(chat_id: int) -> list[DeliveryResult]:
            prefix = f"{m.idempotency_key}:{chat_id}" if m.idempotency_key else None
            out: list[DeliveryResult] = []
            if body:
                out.append(await self._once(f"{prefix}:text" if prefix else None, chat_id, lambda: send_text(self.bot, chat_id, body)))
            for i, att in enumerate(m.attachments):
                if out and not out[-1].ok and out[-1].error_code in DEAD_CHAT_CODES:
                    break                                    # don't keep hammering a chat that can't receive
                caption = att.caption or f"📎 From {m.sender_name}"
                out.append(await self._once(
                    f"{prefix}:att{i}" if prefix else None, chat_id,
                    lambda att=att, caption=caption: send_document(self.bot, chat_id, att.url, filename=att.filename, caption=caption),
                ))
            return out

        result = await self._broadcast(m.recipients, m.group_chat_id, send_to)
        logger.info("teacher_message: %d delivered, %d failed, %d skipped", result["delivered"], result["failed"], result["skipped"])
        return result
