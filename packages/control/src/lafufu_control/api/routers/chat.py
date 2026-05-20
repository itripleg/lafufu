"""Chat router: read conversation history from the database."""

from fastapi import APIRouter, Request
from sqlmodel import Session, select

from ...models.chat import ChatMessage

router = APIRouter()


@router.get("/messages")
def list_messages(req: Request, limit: int = 100) -> dict:
    """Return messages oldest-first, clamped to 1-500."""
    limit = max(1, min(limit, 500))
    with Session(req.app.state.engine) as session:
        stmt = select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(limit)
        rows = list(session.exec(stmt).all())
    rows.reverse()
    return {
        "messages": [
            {
                "id": r.id,
                "role": r.role,
                "text": r.text,
                "emotion": r.emotion,
                "source": r.source,
                "reply_delay_ms": r.reply_delay_ms,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }
