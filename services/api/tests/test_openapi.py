import json
from pathlib import Path

import pytest

from api import openapi
from api.openapi import SCHEMA_PATH, main, render

SCHEMA = json.loads(render())
OPERATIONS = [
    (path, method, op) for path, ops in SCHEMA["paths"].items() for method, op in ops.items()
]


def test_exported_file_matches_the_app() -> None:
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(), "run `make schema`"


def test_every_operation_returns_a_typed_body() -> None:
    for path, method, op in OPERATIONS:
        success = [code for code in op["responses"] if code.startswith("2")]
        assert len(success) == 1, (method, path)
        content = op["responses"][success[0]]["content"]["application/json"]
        assert content["schema"], (method, path)


def test_operation_ids_are_the_handler_names() -> None:
    ids = [op["operationId"] for _, _, op in OPERATIONS]

    assert len(ids) == len(set(ids))
    assert "get_forecast" in ids and "approve_recommendation" in ids
    assert not any("api_v1" in i for i in ids)


def test_only_the_public_api_is_exported() -> None:
    assert all(path.startswith("/api/v1/") for path, _, _ in OPERATIONS)


def test_errors_are_typed_problem_details() -> None:
    problem = {"schema": {"$ref": "#/components/schemas/Problem"}}
    for path, method, op in OPERATIONS:
        assert op["responses"]["default"]["content"]["application/problem+json"] == problem, (
            method,
            path,
        )


def test_money_is_a_digit_string_in_the_schema() -> None:
    balance = SCHEMA["components"]["schemas"]["Company"]["properties"]["balance"]
    assert (balance["type"], balance["pattern"]) == ("string", "^-?[0-9]{1,15}$")


def test_check_reports_an_outdated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr(openapi, "SCHEMA_PATH", target)

    assert main(["--check"]) == 1
    assert main([]) == 0
    assert target.read_text(encoding="utf-8") == render()
    assert main(["--check"]) == 0
    assert "out of date" in capsys.readouterr().err
