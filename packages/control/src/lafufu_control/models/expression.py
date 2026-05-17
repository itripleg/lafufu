from sqlmodel import Field, SQLModel


class Expression(SQLModel, table=True):
    name: str = Field(primary_key=True, max_length=100)
    head_lr_offset: int = 0
    head_ud_offset: int = 0
    eye_offset: int = 0
    jaw_offset: int = 0
    brow_offset: int = 0
    description: str | None = Field(default=None, max_length=500)
