from sqlmodel import Field, SQLModel


class Behavior(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=200, unique=True)
    trigger_json: str = Field(default="{}")
    actions_json: str = Field(default="[]")
    enabled: bool = True
