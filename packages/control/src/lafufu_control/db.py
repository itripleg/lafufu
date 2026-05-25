"""SQLite engine + session helpers."""

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine


def create_engine_for_path(path: str):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def init_db(engine) -> None:
    from .models import behavior, chat, expression, plugin, setting  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session(engine) -> Generator[Session]:
    with Session(engine) as session:
        yield session
