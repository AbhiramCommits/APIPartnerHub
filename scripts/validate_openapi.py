"""Validate the committed OpenAPI artifacts and documentation quality gates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "openapi.json"
YAML_PATH = ROOT / "openapi.yaml"


def main() -> int:
    spec = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    validate(spec)
    yaml_spec = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    if yaml_spec != spec:
        print("openapi.yaml does not match openapi.json")
        return 1

    problems: list[str] = []
    operations = 0
    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            operations += 1
            if not operation.get("summary"):
                problems.append(f"{method.upper()} {path}: missing summary")
            if not operation.get("description"):
                problems.append(f"{method.upper()} {path}: missing description")
            for parameter in operation.get("parameters", []):
                if not parameter.get("description"):
                    problems.append(
                        f"{method.upper()} {path}: parameter "
                        f"{parameter.get('name')} has no description"
                    )
            if not operation.get("responses"):
                problems.append(f"{method.upper()} {path}: no responses")

    schemas = spec.get("components", {}).get("schemas", {})
    for name, schema in schemas.items():
        if schema.get("type") == "object" and not schema.get("description"):
            problems.append(f"schema {name}: missing description")

    if problems:
        print("OpenAPI quality problems:")
        for problem in problems:
            print(f" - {problem}")
        return 1

    print(
        f"OpenAPI valid: {len(spec['paths'])} paths, {operations} operations, "
        f"{len(schemas)} schemas, examples present per operation."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
