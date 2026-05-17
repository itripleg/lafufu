from sqlmodel import Field, SQLModel


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True, max_length=200)
    value: str = Field(max_length=4000)
    value_type: str = Field(max_length=32)
    description: str | None = Field(default=None, max_length=500)
