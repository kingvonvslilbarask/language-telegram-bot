"""Demo trigger - plays the role of the web backend so you can SHOW the bot working.

    python demo.py payment                       student pays -> welcome + video, teacher/admin notified
    python demo.py class                         teacher posts a Zoom link -> student gets Join button
    python demo.py message "Homework tomorrow"   teacher sends a custom message to the student
    add --dry-run to print what would be sent without sending anything

Reads INTERNAL_API_KEY, DEMO_CHAT_ID (and optional extras) from your .env file.
Each run uses a fresh idempotency key, so you can repeat it as often as you like
(re-using a key would - correctly - send nothing, because it was already delivered).
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows consoles choke on emoji otherwise


def build_payload(kind: str, args: argparse.Namespace, env) -> tuple[str, dict]:
    ts = int(time.time())
    student_id = int(env["DEMO_CHAT_ID"])
    teacher_id = int(env.get("DEMO_TEACHER_CHAT_ID") or student_id)
    student_name = env.get("DEMO_STUDENT_NAME") or "Abebe Kebede"
    teacher = {"name": env.get("DEMO_TEACHER_NAME") or "Sarah Bekele", "chat_id": teacher_id}
    student = {"name": student_name, "chat_id": student_id}

    if kind == "payment":
        return "/internal/payment-confirmed", {
            "enrollment_id": f"demo-{ts}",
            "course_title": "English - VIP",
            "student": student,
            "teacher": teacher,
            "schedule_text": "Mon, Tue, Thu, Sat at 7:00 PM",
            "amount": 100,
            "currency": "USD",
        }
    if kind == "class":
        tz = ZoneInfo(env.get("SCHOOL_TIMEZONE") or "Africa/Addis_Ababa")
        tomorrow_7pm = (datetime.now(tz) + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
        return "/internal/class-scheduled", {
            "course_title": "English - VIP",
            "starts_at": tomorrow_7pm.isoformat(),
            "duration_minutes": 60,
            "zoom_url": "https://zoom.us/j/123456789",          # demo link - not a real meeting
            "topic": "Past tense practice",
            "recipients": [student],
            "idempotency_key": f"demo-class-{ts}",
        }
    return "/internal/teacher-message", {
        "sender_name": teacher["name"],
        "text": args.text or "Hello everyone! Our next class is tomorrow at 7:00 PM. See you there!",
        "links": [args.link] if args.link else [],
        "recipients": [student],
        "idempotency_key": f"demo-msg-{ts}",
    }


def summarize(kind: str, data: dict) -> None:
    def line(label: str, r: dict) -> None:
        if r.get("ok"):
            status = "OK" + (" (already sent earlier)" if r.get("deduplicated") else "")
        else:
            status = f"NOT DELIVERED - {r.get('error_code')}"
        print(f"  {label:<18} {status}")

    if kind == "payment":
        line("student welcome", data["student"]["welcome"])
        line("student video", data["student"]["video"])
        line("teacher", data["teacher"])
        for i, admin in enumerate(data["admins"], 1):
            line(f"admin {i}", admin)
        if not data["admins"]:
            print("  admin              (none - set ADMIN_CHAT_IDS in .env)")
    else:
        for d in data["details"]:
            extra = f" - {d['error_code']}" if d.get("error_code") else ""
            print(f"  {d['name']:<18} {d['status'].upper()}{extra}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=["payment", "class", "message"])
    parser.add_argument("text", nargs="?", help="message text (for 'message')")
    parser.add_argument("--link", help="optional link to include (for 'message')")
    parser.add_argument("--dry-run", action="store_true", help="print the request instead of sending it")
    args = parser.parse_args()

    load_dotenv()
    missing = [k for k in ("INTERNAL_API_KEY", "DEMO_CHAT_ID") if not os.getenv(k)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")

    path, payload = build_payload(args.kind, args, os.environ)
    base = os.getenv("BOT_URL") or f"http://localhost:{os.getenv('PORT', '8080')}"

    if args.dry_run:
        print(f"POST {base}{path}\n{json.dumps(payload, indent=2)}")
        return

    import httpx   # imported here so --dry-run works even before `pip install`

    print(f"Sending '{args.kind}' to the bot at {base} ...")
    try:
        resp = httpx.post(base + path, json=payload, headers={"X-Api-Key": os.environ["INTERNAL_API_KEY"]}, timeout=120)
    except httpx.ConnectError:
        sys.exit("Could not reach the bot. Is it running?  ->  python -m bot.main")

    if resp.status_code == 401:
        sys.exit("401: INTERNAL_API_KEY in .env doesn't match the running bot. Restart the bot after editing .env.")
    if resp.status_code >= 400:
        sys.exit(f"Bot returned HTTP {resp.status_code}: {resp.text}")
    summarize(args.kind, resp.json())


if __name__ == "__main__":
    main()
