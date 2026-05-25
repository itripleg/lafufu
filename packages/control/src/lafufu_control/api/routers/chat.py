"""Chat router: read conversation history from the database."""

from datetime import datetime

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

from ...models.chat import ChatMessage

router = APIRouter()


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    text: str
    emotion: str | None
    source: str | None
    reply_delay_ms: int | None
    created_at: datetime


class ChatMessagesResponse(BaseModel):
    messages: list[ChatMessageOut]


@router.get("/messages", response_model=ChatMessagesResponse)
def list_messages(
    req: Request,
    limit: int = Query(100, description="Most recent N messages; clamped to 1-500."),
) -> ChatMessagesResponse:
    """Return messages oldest-first, clamped to 1-500."""
    limit = max(1, min(limit, 500))
    with Session(req.app.state.engine) as session:
        stmt = select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(limit)
        rows = list(session.exec(stmt).all())
    rows.reverse()
    return ChatMessagesResponse(messages=rows)
