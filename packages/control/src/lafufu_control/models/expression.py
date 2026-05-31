from sqlmodel import Field, SQLModel


class Expression(SQLModel, table=True):
    name: str = Field(primary_key=True, max_length=100)
    playback: str = Field(default="once", max_length=20)
    default_duration_ms: int = 250
    default_delay_ms: int = 80
    default_easing: str = Field(default="ease-in-out", max_length=30)
    steps_json: str = Field(default="[]")
    emotion: str | None = Field(default=None, max_length=40, unique=True)
    description: str | None = Field(default=None, max_length=500)
    is_builtin: bool = Field(default=False)
    # Single image/mp4 ref ("bucket/kind/name") shown on the pet/chat screen for
    # this emotion. When set, the screen shows just this one media instead of
    # flipping through the per-frame images, while the servos still animate
    # frame-by-frame. None → fall back to the per-frame flipbook.
    # NOTE: declared LAST to match the physical column position that the
    # `ALTER TABLE ... ADD COLUMN` migration produces on already-deployed DBs —
    # keeping the model order and on-disk order identical avoids a positional
    # column mismatch in SQLAlchemy's compiled-statement cache.
    display_media: str | None = Field(default=None, max_length=200)
