"""SQLModel for conversation messages exchanged between user and Lafufu."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ChatMessage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    role: str = Field(max_length=16)  # "user" | "lafufu" | "puppet"
    text: str = Field(max_length=8000)
    emotion: str | None = Field(default=None, max_length=32)
    source: str | None = Field(
        default=None, max_length=32
    )  # "llm"|"puppet"|"system"; None for user
    reply_delay_ms: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
