"""All user-facing text lives here so wording is easy to change.

Messages are sent as PLAIN TEXT (no Markdown/HTML parse mode) on purpose:
student names and course titles can contain characters like _ * [ that would
otherwise break formatting and cause Telegram to reject the message.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def format_when(dt: datetime, tz_name: str) -> str:
    """Render a datetime in the school's timezone, e.g. 'Monday, 28 September 2026 at 7:00 PM (Africa/Addis_Ababa)'."""
    tz = ZoneInfo(tz_name)
    if dt.tzinfo is None:          # naive input is assumed to already be school time
        dt = dt.replace(tzinfo=tz)
    local = dt.astimezone(tz)
    time_part = local.strftime("%I:%M %p").lstrip("0")
    return f"{local:%A}, {local.day} {local:%B %Y} at {time_part} ({tz_name})"


# ---- notifications triggered by requests from the web backend --------------

def welcome(school: str, student_name: str, course: str, teacher_name: str | None, schedule_text: str | None) -> str:
    lines = [f"🎉 Welcome to {school}, {student_name}!", "", f"Your enrollment in {course} is confirmed."]
    if teacher_name:
        lines.append(f"Teacher: {teacher_name}")
    if schedule_text:
        lines.append(f"Schedule: {schedule_text}")
    lines += [
        "",
        "Your Zoom links and class reminders will be sent right here before each session.",
        "Watch the short video below to see how your classes work on Telegram. 👇",
    ]
    return "\n".join(lines)


def video_caption(school: str) -> str:
    return f"How to take your classes with {school} on Telegram"


def staff_new_enrollment(
    student_name: str,
    course: str,
    *,
    audience: str,                    # "teacher" or "admin"
    schedule_text: str | None,
    teacher_name: str | None,
    amount: float | None,
    currency: str,
    student_connected: bool,
) -> str:
    lines = ["🆕 New enrollment", "", f"Student {student_name} has enrolled in {course}."]
    if schedule_text:
        lines.append(f"Schedule: {schedule_text}")
    if audience == "admin":
        if teacher_name:
            lines.append(f"Teacher: {teacher_name}")
        if amount is not None:
            lines.append(f"Payment: {amount:.2f} {currency} (confirmed)")
    if not student_connected:
        lines += ["", "⚠️ This student has not connected Telegram yet, so they have not received their welcome message."]
    return "\n".join(lines)


def class_scheduled(
    course: str, when_text: str, duration_minutes: int | None, topic: str | None, zoom_url: str, is_update: bool
) -> str:
    lines = ["🔄 Class updated" if is_update else "📅 New class scheduled", "", course, f"🕖 {when_text}"]
    if duration_minutes:
        lines.append(f"⏱ {duration_minutes} minutes")
    if topic:
        lines.append(f"📝 Topic: {topic}")
    lines += ["", "Join with the button below, or use this link:", zoom_url]
    return "\n".join(lines)


def teacher_message(sender_name: str, text: str | None, links: list[str]) -> str:
    lines = [f"📢 Message from {sender_name}"]
    if text:
        lines += ["", text]
    if links:
        lines.append("")
        lines += [f"🔗 {link}" for link in links]
    return "\n".join(lines)


# ---- bot conversation ------------------------------------------------------

def start_greeting(school: str) -> str:
    return (
        f"Hello! 👋 I'm the {school} class assistant.\n\n"
        "I send your class links, reminders and messages from your teachers.\n\n"
        "To connect your account, open the Telegram link shown on the website "
        "after you enroll and press Start.\n\n"
        "Type /help to see what I can do."
    )


def linked_ok(display_name: str, role: str) -> str:
    if role == "teacher":
        return f"✅ Connected! Hi {display_name}. You'll get a message here whenever a new student enrolls in your class."
    if role == "admin":
        return f"✅ Connected! Hi {display_name}. You'll get a message here for every new enrollment."
    return f"✅ Connected! Hi {display_name}. Your class updates and Zoom links will arrive here."


LINK_INVALID = (
    "That connection link is invalid or has expired.\n"
    "Please generate a new one from the website and try again."
)
LINK_ERROR = (
    "I couldn't complete the connection right now. Your enrollment is not affected.\n"
    "Please try again in a few minutes."
)
LINK_UNAVAILABLE = "Account connection is not available right now. Please contact the school."

HELP_TEXT = (
    "Here's what I can do:\n\n"
    "/myclasses - see your upcoming classes\n"
    "/id - show this chat's ID (teachers/admins need it for setup)\n"
    "/help - show this message\n\n"
    "Class links and messages from your teachers arrive here automatically."
)


def id_text(chat_id: int) -> str:
    return f"This chat's ID is: {chat_id}"


NO_CLASSES = "You have no upcoming classes yet."
CLASSES_UNAVAILABLE = "I can't look up your classes right now. Please try again shortly."
FALLBACK = "I only send class notifications, so I can't answer messages. Type /help to see what I can do."
