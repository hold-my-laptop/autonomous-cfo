"""Export the public API schema for the frontend to generate types from.

`uv run python -m api.openapi` writes docs/contracts/openapi.json; with `--check` it only
reports whether the file is out of date (exit code 1)."""

import json
import sys

from api.config import REPO_ROOT, Settings
from api.main import create_app

SCHEMA_PATH = REPO_ROOT / "docs" / "contracts" / "openapi.json"


def render() -> str:
    """The schema as stable, readable JSON. It does not depend on the environment."""
    app = create_app(Settings(database_url=None, internal_token=None))
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main(args: list[str]) -> int:
    current = render()
    if "--check" in args:
        stored = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        if stored != current:
            print(f"{SCHEMA_PATH} is out of date: run `make schema`", file=sys.stderr)
            return 1
        return 0
    SCHEMA_PATH.write_text(current, encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
