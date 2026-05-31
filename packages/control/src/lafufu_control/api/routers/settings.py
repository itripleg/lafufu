"""Settings CRUD. PATCH/PUT publish `config.changed.<key>` to NATS."""

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ...bootstrap import DEFAULTS as BOOTSTRAP_DEFAULTS
from ...models.setting import Setting, is_internal_key

router = APIRouter()

# Build the allowlist once at module load from the bootstrap defaults.
_VALID_KEYS: frozenset[str] = frozenset(k for k, _, _, _ in BOOTSTRAP_DEFAULTS)

# `is_internal_key` (defined on the Setting model so the snapshot router and the
# config.changed rebroadcast share the same predicate) hides internal
# bookkeeping rows from CRUD.
#
# Design note (422-vs-404 existence leak): PUT/PATCH below let FastAPI parse
# the Pydantic `SettingIn` body BEFORE the `is_internal_key` check. That is
# intentional: malformed bodies return 422 for BOTH internal and unknown
# non-internal keys, so a prober sending garbage to `/api/settings/bootstrap.x`
# can't distinguish "reserved prefix" from "unknown key" via the response
# code. Reordering existence-check-before-body-validation would re-open this
# leak (internal→404 on garbage, unknown→422 on garbage). See
# test_put_internal_key_does_not_leak_via_404_vs_422_split for the lock.


class SettingIn(BaseModel):
    value: Any
    value_type: Literal["str", "int", "float", "bool", "json"] = "str"


class SettingOut(BaseModel):
    key: str
    value: str
    value_type: str
    description: str | None = None


def _encode(value: Any, vt: str) -> str:
    if vt == "json":
        return json.dumps(value)
    if vt == "bool":
        # Normalize to lowercase so the JS front-end's case-sensitive check
        # (row.value === "true") matches; otherwise Python's str(True) = "True"
        # which would render as unchecked in the bool widget.
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            return "true" if value.strip().lower() in ("true", "1", "yes", "on") else "false"
        return "true" if value else "false"
    return str(value)


@router.get("/_defaults", response_model=list[SettingOut])
def list_defaults():
    """Factory defaults from bootstrap. Used by admin UI for reset-to-default."""
    return [
        SettingOut(key=k, value=v, value_type=vt, description=desc)
        for (k, v, vt, desc) in BOOTSTRAP_DEFAULTS
    ]


@router.get("", response_model=list[SettingOut])
def list_settings(req: Request):
    with Session(req.app.state.engine) as s:
        rows = s.exec(select(Setting)).all()
        return [SettingOut(**r.model_dump()) for r in rows if not is_internal_key(r.key)]


@router.get("/{key}", response_model=SettingOut)
def get_setting(key: str, req: Request):
    if is_internal_key(key):
        raise HTTPException(
            404, detail={"error_code": "not_found", "message": f"setting {key} not found"}
        )
    with Session(req.app.state.engine) as s:
        row = s.get(Setting, key)
        if not row:
            raise HTTPException(
                404, detail={"error_code": "not_found", "message": f"setting {key} not found"}
            )
        return SettingOut(**row.model_dump())


@router.put("/{key}", response_model=SettingOut)
def put_setting(key: str, body: SettingIn, req: Request):
    if is_internal_key(key):
        raise HTTPException(
            404, detail={"error_code": "not_found", "message": f"setting {key} not found"}
        )
    if key not in _VALID_KEYS:
        raise HTTPException(
            404, detail={"error_code": "not_found", "message": f"setting {key} not found"}
        )
    encoded = _encode(body.value, body.value_type)
    with Session(req.app.state.engine) as s:
        row = s.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=encoded, value_type=body.value_type)
            s.add(row)
        else:
            row.value = encoded
            row.value_type = body.value_type
        s.commit()
        s.refresh(row)
        out = SettingOut(**row.model_dump())
    req.app.state.nats_publish(
        f"config.changed.{key}", {"key": key, "value": body.value, "source": "admin"}
    )
    return out


@router.patch("/{key}", response_model=SettingOut)
def patch_setting(key: str, body: SettingIn, req: Request):
    if is_internal_key(key):
        raise HTTPException(
            404, detail={"error_code": "not_found", "message": f"setting {key} not found"}
        )
    if key not in _VALID_KEYS:
        raise HTTPException(
            404, detail={"error_code": "not_found", "message": f"setting {key} not found"}
        )
    with Session(req.app.state.engine) as s:
        row = s.get(Setting, key)
        if not row:
            raise HTTPException(
                404, detail={"error_code": "not_found", "message": f"setting {key} not found"}
            )
        row.value = _encode(body.value, body.value_type)
        if body.value_type:
            row.value_type = body.value_type
        s.add(row)
        s.commit()
        s.refresh(row)
        out = SettingOut(**row.model_dump())
    req.app.state.nats_publish(
        f"config.changed.{key}", {"key": key, "value": body.value, "source": "admin"}
    )
    return out


@router.delete("/{key}", status_code=204)
def delete_setting(key: str, req: Request):
    if is_internal_key(key):
        raise HTTPException(404)
    if key not in _VALID_KEYS:
        raise HTTPException(404)
    with Session(req.app.state.engine) as s:
        row = s.get(Setting, key)
        if not row:
            raise HTTPException(404)
        s.delete(row)
        s.commit()
