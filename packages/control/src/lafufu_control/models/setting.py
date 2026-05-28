from sqlmodel import Field, SQLModel

# Key prefixes for rows that are internal bookkeeping (migration flags, etc.),
# not operator-tunable config. Hidden from the settings API, the
# /api/state/snapshot payload, and the config.changed rebroadcast so the admin
# UI never shows them and an operator can't edit/delete them. The DB rows still
# exist — only their exposure is suppressed.
INTERNAL_KEY_PREFIXES: tuple[str, ...] = ("bootstrap.",)


def is_internal_key(key: str) -> bool:
    return any(key.startswith(p) for p in INTERNAL_KEY_PREFIXES)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True, max_length=200)
    value: str = Field(max_length=4000)
    value_type: str = Field(max_length=32)
    description: str | None = Field(default=None, max_length=500)
