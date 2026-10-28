"""Request bodies for the bot's internal API (called by the web backend).

The bot never invents course data: everything it needs to send a message is
passed in by the web backend, which stays the source of truth.
"""
from datetime import datetime
from typing import Annotated
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, Field, model_validator


def _http_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("must be a valid http(s) URL")
    return value


HttpUrlStr = Annotated[str, Field(max_length=2000), AfterValidator(_http_url)]


class Person(BaseModel):
    """Someone we may message. chat_id is None when they haven't connected Telegram yet."""
    name: str = Field(min_length=1, max_length=120)
    chat_id: int | None = None


class PaymentConfirmed(BaseModel):
    enrollment_id: str = Field(min_length=1, max_length=100)
    course_title: str = Field(min_length=1, max_length=200)
    student: Person
    teacher: Person | None = None
    schedule_text: str | None = Field(default=None, max_length=300)
    amount: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", max_length=8)
    admin_chat_ids: list[int] = Field(default_factory=list, max_length=20)  # added to ADMIN_CHAT_IDS
    idempotency_key: str | None = Field(default=None, max_length=200)


class ClassScheduled(BaseModel):
    course_title: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    duration_minutes: int | None = Field(default=None, gt=0, le=600)
    zoom_url: HttpUrlStr
    topic: str | None = Field(default=None, max_length=300)
    is_update: bool = False
    recipients: list[Person] = Field(default_factory=list, max_length=500)
    group_chat_id: int | None = None          # optional Telegram group the bot has been added to
    idempotency_key: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _needs_recipient(self):
        if not self.recipients and self.group_chat_id is None:
            raise ValueError("provide at least one recipient or a group_chat_id")
        return self


class Attachment(BaseModel):
    url: HttpUrlStr                            # Telegram downloads it (limit ~20 MB for URLs)
    filename: str | None = Field(default=None, max_length=200)
    caption: str | None = Field(default=None, max_length=1000)


class TeacherMessage(BaseModel):
    sender_name: str = Field(min_length=1, max_length=120)
    text: str | None = Field(default=None, max_length=3500)   # Telegram's limit is 4096 incl. header
    links: list[HttpUrlStr] = Field(default_factory=list, max_length=10)
    attachments: list[Attachment] = Field(default_factory=list, max_length=5)
    recipients: list[Person] = Field(default_factory=list, max_length=500)
    group_chat_id: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _needs_content_and_recipient(self):
        if not (self.text or self.links or self.attachments):
            raise ValueError("provide text, links or attachments")
        if not self.recipients and self.group_chat_id is None:
            raise ValueError("provide at least one recipient or a group_chat_id")
        return self
