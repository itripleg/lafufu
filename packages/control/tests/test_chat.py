from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.models.chat import ChatMessage
from sqlmodel import Session, select


def _client(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: None)
    return TestClient(app), engine


def _seed(engine, rows):
    with Session(engine) as session:
        for row in rows:
            session.add(row)
        session.commit()


def test_messages_endpoint_returns_oldest_first(tmp_path):
    client, engine = _client(tmp_path)
    base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    _seed(
        engine,
        [
            ChatMessage(role="user", text="first", created_at=base),
            ChatMessage(role="lafufu", text="second", created_at=base + timedelta(seconds=1)),
            ChatMessage(role="lafufu", text="third", created_at=base + timedelta(seconds=2)),
        ],
    )
    r = client.get("/api/chat/messages")
    assert r.status_code == 200
    assert [m["text"] for m in r.json()["messages"]] == ["first", "second", "third"]


def test_messages_endpoint_clamps_limit(tmp_path):
    client, engine = _client(tmp_path)
    base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    _seed(
        engine,
        [
            ChatMessage(role="user", text=f"msg{i}", created_at=base + timedelta(seconds=i))
            for i in range(5)
        ],
    )
    assert len(client.get("/api/chat/messages?limit=2").json()["messages"]) == 2
    assert (
        len(client.get("/api/chat/messages?limit=0").json()["messages"]) == 1
    )  # 0 clamps up to the minimum of 1
    assert len(client.get("/api/chat/messages?limit=9999").json()["messages"]) == 5


def test_messages_endpoint_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/api/chat/messages")
    assert r.status_code == 200
    assert r.json() == {"messages": []}


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
