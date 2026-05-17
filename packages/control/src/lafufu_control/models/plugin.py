from sqlmodel import Field, SQLModel


class Plugin(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=200, unique=True)
    version: str = Field(max_length=50, default="0.0.0")
    enabled: bool = False
    config_json: str = Field(default="{}")
