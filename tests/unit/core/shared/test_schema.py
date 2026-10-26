from __future__ import annotations

from dss.core.shared.schema import follow_ref, resolve_schema


def _document(**schemas: dict) -> dict:
    return {"components": {"schemas": schemas}}


def test_all_of_members_merge_into_one_object() -> None:
    schema = {
        "allOf": [
            {"required": ["a"], "properties": {"a": {"type": "string"}}},
            {"required": ["b"], "properties": {"b": {"type": "integer"}}},
        ]
    }

    resolved = resolve_schema(schema, home=_document(), shared=None)

    assert resolved["properties"] == {"a": {"type": "string"}, "b": {"type": "integer"}}
    assert resolved["required"] == ["a", "b"]


def test_a_ref_inside_all_of_is_followed() -> None:
    document = _document(Code={"type": "string", "pattern": "^[0-9]+$"})

    resolved = resolve_schema(
        {"allOf": [{"$ref": "#/components/schemas/Code"}]}, home=document, shared=None
    )

    assert resolved == {"type": "string", "pattern": "^[0-9]+$"}


def test_keys_beside_a_ref_override_its_target() -> None:
    document = _document(Name={"type": "string", "maxLength": 200})

    resolved = follow_ref(
        {"$ref": "#/components/schemas/Name", "maxLength": 50},
        home=document,
        shared=None,
    )

    assert resolved == {"type": "string", "maxLength": 50}


def test_a_ref_into_the_shared_file_resolves_its_own_refs_there() -> None:
    shared = _document(
        Outer={"properties": {"inner": {"$ref": "#/components/schemas/Inner"}}},
        Inner={"type": "string", "minLength": 1},
    )
    pack = _document()

    outer = resolve_schema(
        {"$ref": "other.yaml#/components/schemas/Outer"}, home=pack, shared=shared
    )
    inner = resolve_schema(outer["properties"]["inner"], home=pack, shared=shared)

    assert inner == {"type": "string", "minLength": 1}


def test_a_network_ref_is_left_as_it_is() -> None:
    node = {"$ref": "https://schema.beckn.io/Contact/v2.0/attributes.yaml"}

    assert resolve_schema(node, home=_document(), shared=None) == node


def test_follow_ref_leaves_all_of_in_place() -> None:
    node = {"allOf": [{"type": "string"}]}

    assert follow_ref(node, home=_document(), shared=None) == node
