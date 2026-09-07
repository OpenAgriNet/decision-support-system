"""The generated OpenAPI document must be self-consistent.

The endpoint parses the body itself, so FastAPI cannot infer the schema and it is
declared explicitly. That declaration is generated from the wire models — which
means their internal `$ref`s have to be rewritten: Pydantic emits
`#/$defs/Location`, and inside an OpenAPI document a `#/...` ref resolves against
the document root, where no `$defs` exists.
"""

from __future__ import annotations

from typing import Any

from tests.support.fakes import FakeRunner

from dss.config.settings import Settings
from dss.entrypoint.app import build_app


def _document() -> dict[str, Any]:
    return build_app(runner=FakeRunner([]), settings=Settings()).openapi()


def _refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        return found + [r for value in node.values() for r in _refs(value)]
    if isinstance(node, list):
        return [r for item in node for r in _refs(item)]
    return []


def _resolve(document: dict[str, Any], ref: str) -> Any:
    node: Any = document
    for key in ref.removeprefix("#/").split("/"):
        node = node[key]
    return node


def test_the_document_has_references_to_check():
    assert _refs(_document()), "no $ref found — this test would be vacuous"


def test_no_reference_points_at_a_pydantic_defs_block():
    """`#/$defs/...` is the shape of the bug: valid inside a standalone JSON
    Schema, dangling inside an OpenAPI document."""

    dangling = [ref for ref in _refs(_document()) if ref.startswith("#/$defs/")]

    assert not dangling


def test_every_reference_resolves():
    document = _document()

    unresolved = []
    for ref in set(_refs(document)):
        try:
            _resolve(document, ref)
        except (KeyError, TypeError):
            unresolved.append(ref)

    assert not unresolved


def test_the_turn_models_are_published_as_components():
    schemas = _document()["components"]["schemas"]

    assert {"TurnRequest", "TurnResponse"} <= set(schemas)
    assert "sessionId" in schemas["Context"]["properties"]
