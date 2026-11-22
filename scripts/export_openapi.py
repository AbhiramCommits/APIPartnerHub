"""Export the live FastAPI OpenAPI schema to openapi.json and openapi.yaml.

Usage:
    python -m scripts.export_openapi            # write both artifacts
    python -m scripts.export_openapi --check    # exit 1 if committed artifacts are stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from app.main import app

ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "openapi.json"
YAML_PATH = ROOT / "openapi.yaml"

CANONICAL_SERVERS = [
    {"url": "http://localhost:8000", "description": "Local development"},
    {"url": "https://api.apipartnerhub.dev", "description": "Production"},
]


def build_spec() -> dict[str, object]:
    spec = app.openapi()
    # Keep the committed artifact independent of the machine's .env overrides.
    spec["servers"] = CANONICAL_SERVERS
    return spec


def render(spec: dict[str, object]) -> tuple[str, str]:
    json_text = json.dumps(spec, indent=2, sort_keys=False) + "\n"
    yaml_text = str(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
    return json_text, yaml_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed artifacts match the live app instead of writing them.",
    )
    args = parser.parse_args()

    json_text, yaml_text = render(build_spec())

    if args.check:
        stale = []
        if not JSON_PATH.exists() or JSON_PATH.read_text(encoding="utf-8") != json_text:
            stale.append(str(JSON_PATH.name))
        if not YAML_PATH.exists() or YAML_PATH.read_text(encoding="utf-8") != yaml_text:
            stale.append(str(YAML_PATH.name))
        if stale:
            print(f"OpenAPI artifacts are stale: {', '.join(stale)}. Run `make openapi`.")
            return 1
        print("OpenAPI artifacts are up to date.")
        return 0

    JSON_PATH.write_text(json_text, encoding="utf-8")
    YAML_PATH.write_text(yaml_text, encoding="utf-8")
    info = build_spec()["info"]
    assert isinstance(info, dict)
    print(f"Wrote {JSON_PATH.name} and {YAML_PATH.name} for {info['title']} {info['version']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
