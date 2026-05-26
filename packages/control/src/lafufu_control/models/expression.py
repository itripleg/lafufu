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
