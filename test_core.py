"""Stdlib-only tests for the parts that don't need Telegram installed.
Run:  python tests/test_core.py     (or: pytest)
"""
import asyncio
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import messages
from bot.config import ConfigError, load_settings
from bot.ledger import DeliveryLedger

VALID_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "WEBHOOK_BASE_URL": "https://bot.example.com/",
    "TELEGRAM_WEBHOOK_SECRET": "secret_secret_secret_1234",
    "INTERNAL_API_KEY": "k" * 32,
    "ADMIN_CHAT_IDS": "111, 222,111",
}


def test_config_valid():
    s = load_settings(VALID_ENV)
    assert s.webhook_base_url == "https://bot.example.com"       # trailing slash stripped
    assert s.admin_chat_ids == (111, 222)                          # de-duplicated, ordered
    assert s.timezone == "Africa/Addis_Ababa"


def test_config_uses_render_url_when_webhook_url_missing():
    env = {k: v for k, v in VALID_ENV.items() if k != "WEBHOOK_BASE_URL"}
    env["RENDER_EXTERNAL_URL"] = "https://azela-telegram-bot.onrender.com/"
    s = load_settings(env)
    assert s.webhook_base_url == "https://azela-telegram-bot.onrender.com"
    assert s.guide_video_url is None


def test_config_reports_every_problem_without_leaking_secrets():
    bad = {**VALID_ENV, "WEBHOOK_BASE_URL": "http://insecure", "TELEGRAM_WEBHOOK_SECRET": "short!", "ADMIN_CHAT_IDS": "abc"}
    del bad["INTERNAL_API_KEY"]
    try:
        load_settings(bad)
    except ConfigError as exc:
        text = str(exc)
        assert "INTERNAL_API_KEY is required" in text
        assert "https://" in text and "TELEGRAM_WEBHOOK_SECRET" in text and "ADMIN_CHAT_IDS" in text
        assert VALID_ENV["TELEGRAM_BOT_TOKEN"] not in text
    else:
        raise AssertionError("expected ConfigError")


def test_config_requires_backend_url_and_key_together():
    try:
        load_settings({**VALID_ENV, "WEB_BACKEND_URL": "https://api.example.com"})
    except ConfigError as exc:
        assert "set together" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_ledger_dedupes_and_persists():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ledger.sqlite3"

        async def scenario():
            ledger = DeliveryLedger(path)
            assert not await ledger.has("k1")
            await ledger.add("k1")
            await ledger.add("k1")                 # adding twice must not fail
            assert await ledger.has("k1")
            ledger.close()
            reopened = DeliveryLedger(path)        # survives a restart
            assert await reopened.has("k1")
            reopened.close()

        asyncio.run(scenario())


def test_format_when_converts_to_school_time():
    utc = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)       # 16:00 UTC = 19:00 in Addis Ababa
    assert messages.format_when(utc, "Africa/Addis_Ababa") == "Monday, 28 September 2026 at 7:00 PM (Africa/Addis_Ababa)"
    naive = datetime(2026, 9, 28, 7, 5)                            # naive = already school time
    assert "7:05 AM" in messages.format_when(naive, "Africa/Addis_Ababa")


def test_message_wording():
    staff = messages.staff_new_enrollment(
        "Abebe", "English - VIP", audience="admin", schedule_text=None, teacher_name="Sarah",
        amount=100, currency="USD", student_connected=False,
    )
    assert "Student Abebe has enrolled in English - VIP." in staff
    assert "Teacher: Sarah" in staff and "100.00 USD" in staff and "has not connected Telegram" in staff
    teacher_view = messages.staff_new_enrollment(
        "Abebe", "English - VIP", audience="teacher", schedule_text=None, teacher_name="Sarah",
        amount=100, currency="USD", student_connected=True,
    )
    assert "Payment" not in teacher_view                          # teachers don't see payment details
    assert "Abebe" in messages.welcome("Azela", "Abebe", "English - VIP", "Sarah", "Mon 7 PM")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
