"""Export all pydantic schemas to a single JSON Schema file.

Run: python -m lafufu_shared.export_schemas > web/src/shared/schemas.json
"""

import inspect
import json
import sys

from pydantic import BaseModel

from . import schemas as _schemas_mod


def collect_schemas() -> dict:
    """Walk the schemas module, emit a JSON Schema doc with all models under #/definitions."""
    out: dict = {"$schema": "http://json-schema.org/draft-07/schema#", "definitions": {}}
    for name, obj in inspect.getmembers(_schemas_mod):
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel:
            out["definitions"][name] = obj.model_json_schema(mode="serialization")
    return out


def main() -> int:
    json.dump(collect_schemas(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
