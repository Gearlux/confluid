"""Tests for ``confluid.sanitize_schema`` — the JSON-Schema downgrade that makes
the MCP tool surface callable by strict LLM function-calling APIs (Gemini /
Antigravity), which reject ``$ref``/``$defs``/``anyOf`` and other full-JSON-Schema
constructs that pydantic (and therefore FastMCP) emits.

The function must inline references, flatten ``allOf``, collapse nullable
``anyOf``, normalise odds and ends, AND never mutate its input or treat data
values (``default`` payloads) as schemas.
"""

import json
from typing import Any, List, Optional, Union

from pydantic import BaseModel

from confluid import sanitize_schema


def _no_forbidden(schema: Any) -> bool:
    """True when no Gemini-hostile keyword survives anywhere in ``schema``."""
    blob = json.dumps(schema)
    return not any(tok in blob for tok in ('"$ref"', '"$defs"', '"definitions"', '"const"', '"additionalProperties"'))


# --- $ref / $defs inlining -------------------------------------------------


def test_inlines_simple_ref_and_drops_defs() -> None:
    schema = {
        "type": "object",
        "properties": {"cfg": {"$ref": "#/$defs/Cfg"}},
        "$defs": {"Cfg": {"type": "object", "properties": {"x": {"type": "integer"}}}},
    }
    out = sanitize_schema(schema)
    assert "$defs" not in out
    assert out["properties"]["cfg"]["type"] == "object"
    assert out["properties"]["cfg"]["properties"]["x"] == {"type": "integer"}
    assert _no_forbidden(out)


def test_inlines_nested_ref_chain() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/A"}},
        "$defs": {
            "A": {"type": "object", "properties": {"b": {"$ref": "#/$defs/B"}}},
            "B": {"type": "object", "properties": {"v": {"type": "string"}}},
        },
    }
    out = sanitize_schema(schema)
    assert out["properties"]["a"]["properties"]["b"]["properties"]["v"] == {"type": "string"}
    assert _no_forbidden(out)


def test_ref_siblings_are_merged_onto_expansion() -> None:
    # pydantic attaches description/default next to a $ref via a wrapper.
    schema = {
        "type": "object",
        "properties": {"cfg": {"$ref": "#/$defs/Cfg", "description": "the config", "default": None}},
        "$defs": {"Cfg": {"type": "object", "properties": {"x": {"type": "integer"}}}},
    }
    out = sanitize_schema(schema)
    cfg = out["properties"]["cfg"]
    assert cfg["type"] == "object"
    assert cfg["description"] == "the config"
    assert cfg["default"] is None


def test_recursive_ref_is_truncated_not_infinite() -> None:
    schema = {
        "type": "object",
        "properties": {"node": {"$ref": "#/$defs/Node"}},
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/Node"}},
            }
        },
    }
    out = sanitize_schema(schema)  # must terminate
    node = out["properties"]["node"]
    assert node["type"] == "object"
    # the self-reference is collapsed to a bare object placeholder
    assert node["properties"]["child"]["type"] == "object"
    assert _no_forbidden(out)


def test_missing_ref_target_falls_back_to_object() -> None:
    schema = {"type": "object", "properties": {"x": {"$ref": "#/$defs/Gone"}}}
    out = sanitize_schema(schema)
    assert out["properties"]["x"] == {"type": "object"}


# --- allOf flattening ------------------------------------------------------


def test_flattens_allof_with_siblings() -> None:
    schema = {
        "type": "object",
        "properties": {
            "cfg": {
                "allOf": [{"$ref": "#/$defs/Cfg"}],
                "description": "wrapped",
            }
        },
        "$defs": {"Cfg": {"type": "object", "properties": {"x": {"type": "integer"}}}},
    }
    out = sanitize_schema(schema)
    cfg = out["properties"]["cfg"]
    assert "allOf" not in cfg
    assert cfg["type"] == "object"
    assert cfg["description"] == "wrapped"
    assert cfg["properties"]["x"] == {"type": "integer"}


# --- nullable anyOf collapse ----------------------------------------------


def test_collapses_optional_anyof_to_nullable() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}},
    }
    out = sanitize_schema(schema)
    name = out["properties"]["name"]
    assert "anyOf" not in name
    assert name["type"] == "string"
    assert name["nullable"] is True
    assert name["default"] is None


def test_optional_nested_model_collapses_and_inlines() -> None:
    schema = {
        "type": "object",
        "properties": {"cfg": {"anyOf": [{"$ref": "#/$defs/Cfg"}, {"type": "null"}], "default": None}},
        "$defs": {"Cfg": {"type": "object", "properties": {"x": {"type": "integer"}}}},
    }
    out = sanitize_schema(schema)
    cfg = out["properties"]["cfg"]
    assert "anyOf" not in cfg
    assert cfg["type"] == "object"
    assert cfg["nullable"] is True
    assert cfg["properties"]["x"] == {"type": "integer"}


def test_genuine_multitype_union_is_preserved() -> None:
    schema = {
        "type": "object",
        "properties": {"v": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
    }
    out = sanitize_schema(schema)
    v = out["properties"]["v"]
    assert "anyOf" in v
    assert {"type": "string"} in v["anyOf"] and {"type": "integer"} in v["anyOf"]


# --- keyword normalisation -------------------------------------------------


def test_const_becomes_enum() -> None:
    out = sanitize_schema({"type": "object", "properties": {"k": {"const": "fixed"}}})
    k = out["properties"]["k"]
    assert "const" not in k
    assert k["enum"] == ["fixed"]


def test_type_list_collapses_to_single_plus_nullable() -> None:
    out = sanitize_schema({"type": "object", "properties": {"x": {"type": ["string", "null"]}}})
    x = out["properties"]["x"]
    assert x["type"] == "string"
    assert x["nullable"] is True


def test_strips_additional_properties() -> None:
    out = sanitize_schema({"type": "object", "additionalProperties": True, "properties": {}})
    assert "additionalProperties" not in out


def test_drops_unsupported_format_keeps_supported() -> None:
    out = sanitize_schema(
        {
            "type": "object",
            "properties": {
                "p": {"type": "string", "format": "path"},
                "t": {"type": "string", "format": "date-time"},
            },
        }
    )
    assert "format" not in out["properties"]["p"]
    assert out["properties"]["t"]["format"] == "date-time"


def test_ensures_object_type_when_only_properties_present() -> None:
    out = sanitize_schema({"properties": {"x": {"type": "integer"}}})
    assert out["type"] == "object"


def test_array_gets_items_from_prefixitems() -> None:
    out = sanitize_schema(
        {"type": "object", "properties": {"pair": {"type": "array", "prefixItems": [{"type": "number"}]}}}
    )
    pair = out["properties"]["pair"]
    assert pair["items"] == {"type": "number"}


# --- safety properties -----------------------------------------------------


def test_does_not_mutate_input() -> None:
    schema = {
        "type": "object",
        "properties": {"cfg": {"$ref": "#/$defs/Cfg"}},
        "$defs": {"Cfg": {"type": "object", "properties": {"x": {"type": "integer"}}}},
    }
    before = json.dumps(schema, sort_keys=True)
    sanitize_schema(schema)
    assert json.dumps(schema, sort_keys=True) == before


def test_default_dict_payload_is_preserved_not_treated_as_schema() -> None:
    # A `default` that is itself a JSON object must be copied verbatim — never
    # recursed into and stripped as if it were a subschema.
    schema = {
        "type": "object",
        "properties": {
            "opts": {
                "type": "object",
                "default": {"$ref": "keep-me", "const": "data", "additionalProperties": "x"},
            }
        },
    }
    out = sanitize_schema(schema)
    assert out["properties"]["opts"]["default"] == {
        "$ref": "keep-me",
        "const": "data",
        "additionalProperties": "x",
    }


def test_is_idempotent() -> None:
    schema = {
        "type": "object",
        "properties": {
            "cfg": {"anyOf": [{"$ref": "#/$defs/Cfg"}, {"type": "null"}], "default": None},
            "name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "$defs": {"Cfg": {"type": "object", "properties": {"x": {"const": 1}}}},
    }
    once = sanitize_schema(schema)
    twice = sanitize_schema(once)
    assert once == twice


def test_non_dict_input_returned_unchanged() -> None:
    assert sanitize_schema("not a schema") == "not a schema"
    assert sanitize_schema(None) is None


# --- edge cases (list-valued items, allOf merges, type inference) ----------


def test_tuple_items_list_is_recursed() -> None:
    # Draft-04 tuple validation: `items` is a LIST of schemas (each recursed),
    # and boolean subschemas (a valid JSON-Schema form) pass through untouched.
    schema = {
        "type": "object",
        "properties": {
            "pair": {"type": "array", "items": [{"$ref": "#/$defs/Cfg"}, True]},
        },
        "$defs": {"Cfg": {"type": "object", "properties": {"x": {"const": 5}}}},
    }
    out = sanitize_schema(schema)
    items = out["properties"]["pair"]["items"]
    assert items[0]["properties"]["x"]["enum"] == [5]
    assert items[1] is True
    assert _no_forbidden(out)


def test_allof_merges_properties_and_required() -> None:
    schema = {
        "allOf": [
            {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
            {"type": "object", "properties": {"b": {"type": "string"}}, "required": ["b"]},
        ]
    }
    out = sanitize_schema(schema)
    assert set(out["properties"]) == {"a", "b"}
    assert out["required"] == ["a", "b"]


def test_allof_skips_non_dict_member() -> None:
    # A boolean subschema member (valid JSON Schema) is not mergeable; skip it.
    schema = {"allOf": [{"type": "object", "properties": {"a": {"type": "integer"}}}, True]}
    out = sanitize_schema(schema)
    assert out["properties"]["a"] == {"type": "integer"}


def test_all_null_anyof_becomes_object() -> None:
    out = sanitize_schema({"type": "object", "properties": {"x": {"anyOf": [{"type": "null"}]}}})
    x = out["properties"]["x"]
    assert x["type"] == "object"
    assert x["nullable"] is True


def test_type_list_without_null_picks_first() -> None:
    out = sanitize_schema({"type": "object", "properties": {"x": {"type": ["string", "integer"]}}})
    x = out["properties"]["x"]
    assert x["type"] == "string"
    assert "nullable" not in x


def test_items_without_type_infers_array() -> None:
    out = sanitize_schema({"type": "object", "properties": {"xs": {"items": {"type": "string"}}}})
    assert out["properties"]["xs"]["type"] == "array"


def test_enum_type_inference_covers_all_kinds() -> None:
    cases = {
        "b": (True, "boolean"),
        "f": (1.5, "number"),
        "lst": ([1, 2], "array"),
        "obj": ({"k": "v"}, "object"),
    }
    schema = {"type": "object", "properties": {k: {"enum": [v]} for k, (v, _) in cases.items()}}
    out = sanitize_schema(schema)
    for k, (_, expected) in cases.items():
        assert out["properties"][k]["type"] == expected


# --- end-to-end against a real pydantic schema -----------------------------


def test_real_pydantic_optional_and_nested_model_is_clean() -> None:
    class Inner(BaseModel):
        x: int = 0
        tags: List[str] = []

    class Outer(BaseModel):
        name: Optional[str] = None
        inner: Optional[Inner] = None
        choice: Union[str, int] = "a"

    raw = Outer.model_json_schema()
    assert "$defs" in raw  # precondition: pydantic emits the hostile shape
    out = sanitize_schema(raw)
    assert _no_forbidden(out)
    # nullable fields survived as typed + nullable
    assert out["properties"]["name"]["type"] == "string"
    assert out["properties"]["name"]["nullable"] is True
    assert out["properties"]["inner"]["type"] == "object"
    assert out["properties"]["inner"]["properties"]["x"]["type"] == "integer"
