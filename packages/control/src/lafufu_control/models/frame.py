from sqlmodel import Field, SQLModel


class Frame(SQLModel, table=True):
    name: str = Field(primary_key=True, max_length=100)
    head_lr: int
    head_ud: int
    eye: int
    jaw: int
    brow: int
    image: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=500)
