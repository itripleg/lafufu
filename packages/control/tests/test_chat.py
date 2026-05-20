from datetime import datetime

from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.models.chat import ChatMessage
from sqlmodel import Session, select


def test_chat_message_round_trips(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)

    with Session(engine) as session:
        session.add(
            ChatMessage(
                role="lafufu",
                text="the city remembers you",
                emotion="neutral",
                source="llm",
                reply_delay_ms=1234,
            )
        )
        session.commit()

    with Session(engine) as session:
        msg = session.exec(select(ChatMessage)).one()

    assert msg.id is not None
    assert msg.role == "lafufu"
    assert msg.text == "the city remembers you"
    assert msg.emotion == "neutral"
    assert msg.source == "llm"
    assert msg.reply_delay_ms == 1234
    assert isinstance(msg.created_at, datetime)
