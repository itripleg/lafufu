from sqlmodel import Field, SQLModel

# Key prefixes for internal bookkeeping rows (one-shot migration markers, etc.)
# that are NOT operator-tunable config. They must be hidden from every outward
# surface: the settings CRUD API, the /api/state/snapshot payload, AND the
# config.changed rebroadcast (which crosses the WS bridge into the browser).
# The DB rows still exist — only their exposure is suppressed. Centralized here
# so all three call sites share one predicate and can't drift apart.
INTERNAL_KEY_PREFIXES: tuple[str, ...] = ("bootstrap.",)


def is_internal_key(key: str) -> bool:
    return any(key.startswith(p) for p in INTERNAL_KEY_PREFIXES)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True, max_length=200)
    value: str = Field(max_length=4000)
    value_type: str = Field(max_length=32)
    description: str | None = Field(default=None, max_length=500)
