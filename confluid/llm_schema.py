"""Downgrade a JSON Schema to the subset LLM function-calling APIs accept.

Pydantic (and therefore the FastMCP tool surface that introspects pydantic
arg-models) emits *full* JSON Schema 2020-12: ``$ref``/``$defs`` for every
nested model, ``anyOf`` for ``Optional[...]`` / unions, ``allOf`` for a
``$ref`` carrying sibling metadata, ``const``, ``additionalProperties``, rich
``format`` strings, and so on.

That dialect is fine for Anthropic's tool API (it tolerates references), but it
is **not** what Google Gemini's function-calling accepts. Gemini's
``FunctionDeclaration.parameters`` is an OpenAPI-3.0 *subset* Schema with no
support for ``$ref``/``$defs`` (no references at all), only narrow ``anyOf``
support, no ``const``/``additionalProperties``/``patternProperties``, and a
short allow-list of ``format`` values. A tool whose schema is
``{"$ref": "#/$defs/X"}`` with 40-odd ``$defs`` is literally unrepresentable
there, so the tool is dropped or every call fails. Newer Anthropic models /
clients have also tightened schema validation. The Antigravity CLI (Gemini) is
the strict case that exposed this.

``sanitize_schema`` rewrites a schema into the intersection both accept:

  1. **Inline** every ``$ref`` against ``$defs``/``definitions`` and drop the
     definition blocks (cycles are truncated to a bare ``{"type": "object"}``
     so self-referential models can't loop forever).
  2. **Flatten** ``allOf`` by merging its members (the common pydantic
     "``$ref`` + description/default" wrapper).
  3. **Collapse** nullable unions: ``anyOf: [T, {"type": "null"}]`` becomes
     ``T`` with ``nullable: true`` — removing the type-less ``anyOf`` node
     Gemini rejects.
  4. Normalise odds and ends: ``const`` -> single-value ``enum``,
     ``"type": ["string", "null"]`` -> ``"type": "string"`` + ``nullable``,
     strip unsupported structural keywords, drop ``format`` values outside the
     Gemini allow-list, ensure every object/array node carries an explicit
     ``type`` (Gemini requires it) and every array declares ``items``.

This is **advertised-schema only** — it is the schema the LLM reads to decide
how to call a tool. It deliberately does NOT touch the server-side argument
validation: FastMCP validates real calls against the original pydantic
arg-model (the transport registers ``call_tool`` with ``validate_input=False``
and validates inside the tool), so simplifying the advertised schema can never
relax or break actual validation — it only makes the tool *callable* by stricter
clients.

The function is pure (no mutation of its input) and depends only on the stdlib,
so it lives in confluid — the one place that already owns AI-facing schema
introspection (``to_pydantic`` / ``parse_param_docs``) — and is shared verbatim
by every MCP server in the workspace (navigaitor, sairen, ...).
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

# --- Keyword classification ------------------------------------------------
#
# Only *subschema-bearing* positions are recursed into. Crucially, data-valued
# keywords (``default``, ``const``, ``enum``, ``examples``) are NOT recursed —
# a ``default`` may itself be a JSON object and must be copied verbatim, never
# treated as a schema to rewrite.
_DICT_OF_SCHEMA_KEYS = ("properties",)
_LIST_OF_SCHEMA_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")
_SINGLE_SCHEMA_KEYS = ("items", "additionalProperties", "not", "contains")

# Structural keywords with no representation in the Gemini / OpenAPI-3.0 subset.
# They are dropped after dereferencing/flattening (which consume $ref/$defs/allOf).
_STRIP_KEYS = frozenset(
    {
        "$schema",
        "$id",
        "$anchor",
        "$comment",
        "$ref",
        "$defs",
        "definitions",
        "$dynamicRef",
        "$dynamicAnchor",
        "additionalProperties",
        "unevaluatedProperties",
        "patternProperties",
        "propertyNames",
        "additionalItems",
        "unevaluatedItems",
        "dependentSchemas",
        "dependentRequired",
        "if",
        "then",
        "else",
        "not",
        "allOf",
        "examples",
        "$vocabulary",
    }
)

# ``format`` values Gemini's schema accepts. Anything else (``path``, ``uuid``,
# ``binary``, ``email`` on older endpoints, ...) is advisory-only and is dropped
# rather than risk an "unsupported format" rejection.
_SAFE_FORMATS = frozenset({"date-time", "date", "time", "float", "double", "int32", "int64", "enum"})

__all__ = ["sanitize_schema"]


def sanitize_schema(schema: Any) -> Any:
    """Return an LLM-/Gemini-safe copy of ``schema``.

    Non-dict inputs are returned unchanged. The input is never mutated.

    Args:
        schema: A JSON Schema dict (e.g. an MCP tool's ``inputSchema``).

    Returns:
        A new schema dict with ``$ref``/``$defs`` inlined, ``allOf`` flattened,
        nullable ``anyOf`` collapsed, and unsupported keywords removed.
    """
    if not isinstance(schema, dict):
        return schema

    defs: Dict[str, Any] = {}
    defs.update(schema.get("definitions") or {})
    defs.update(schema.get("$defs") or {})

    dereferenced = _deref(schema, defs, ())
    return _normalize(dereferenced)


# --- Pass A: inline $ref + flatten allOf -----------------------------------


def _deref(node: Any, defs: Dict[str, Any], stack: Tuple[str, ...]) -> Any:
    """Inline ``$ref`` against ``defs`` and flatten ``allOf`` into plain dicts.

    ``stack`` carries the chain of definition names currently being expanded so
    a recursive model (a ref that reaches itself) is truncated instead of
    looping forever.
    """
    if isinstance(node, list):
        return [_deref(item, defs, stack) for item in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        name = str(node["$ref"]).split("/")[-1]
        siblings = _deref_children({k: v for k, v in node.items() if k != "$ref"}, defs, stack)
        if name in stack:
            # Cycle: stop expanding, keep a usable placeholder.
            base: Dict[str, Any] = {
                "type": "object",
                "description": "(recursive schema truncated for LLM compatibility)",
            }
            return _merge_into(base, siblings)
        target = defs.get(name)
        if target is None:
            return _merge_into({"type": "object"}, siblings)
        expanded = _deref(target, defs, stack + (name,))
        # Sibling keywords on the $ref node (description/default/title) augment
        # the expanded definition.
        return _merge_into(dict(expanded) if isinstance(expanded, dict) else {"type": "object"}, siblings)

    if "allOf" in node:
        merged: Dict[str, Any] = {}
        for member in node["allOf"]:
            part = _deref(member, defs, stack)
            if isinstance(part, dict):
                _merge_into(merged, part)
        rest = _deref_children({k: v for k, v in node.items() if k != "allOf"}, defs, stack)
        return _merge_into(merged, rest)

    return _deref_children(node, defs, stack)


def _deref_children(node: Dict[str, Any], defs: Dict[str, Any], stack: Tuple[str, ...]) -> Dict[str, Any]:
    """Recurse ``_deref`` into subschema positions only; copy everything else."""
    out: Dict[str, Any] = {}
    for key, value in node.items():
        if key in ("$defs", "definitions"):
            continue  # consumed at the root; never re-emit
        if key in _DICT_OF_SCHEMA_KEYS and isinstance(value, dict):
            out[key] = {k: _deref(v, defs, stack) for k, v in value.items()}
        elif key in _LIST_OF_SCHEMA_KEYS and isinstance(value, list):
            out[key] = [_deref(v, defs, stack) for v in value]
        elif key in _SINGLE_SCHEMA_KEYS and isinstance(value, (dict, list)):
            out[key] = _deref(value, defs, stack)
        else:
            out[key] = value
    return out


def _merge_into(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow-merge ``extra`` into ``base`` in place; combine properties/required.

    Metadata keywords (``description``/``default``/``title``) from ``extra``
    win; structural keywords already present in ``base`` are preserved.
    """
    for key, value in extra.items():
        if key == "properties" and isinstance(base.get(key), dict) and isinstance(value, dict):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        elif key == "required" and isinstance(base.get(key), list) and isinstance(value, list):
            base[key] = list(dict.fromkeys([*base[key], *value]))
        elif key not in base or key in ("description", "default", "title"):
            base[key] = value
    return base


# --- Pass B: normalise unions / keywords / types ---------------------------


def _normalize(node: Any) -> Any:
    """Collapse nullable unions and strip keywords outside the Gemini subset."""
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    if not isinstance(node, dict):
        return node

    node = dict(node)

    # const -> single-value enum (the subset has no `const`).
    if "const" in node:
        node["enum"] = [node.pop("const")]

    # "type": ["string", "null"] -> "type": "string" + nullable.
    type_value = node.get("type")
    if isinstance(type_value, list):
        non_null = [t for t in type_value if t != "null"]
        if "null" in type_value:
            node["nullable"] = True
        node["type"] = non_null[0] if non_null else "object"

    # Collapse nullable anyOf/oneOf into a single typed (nullable) schema.
    for key in ("anyOf", "oneOf"):
        if key in node:
            members = [_normalize(sub) for sub in node[key]]
            non_null = [m for m in members if not (isinstance(m, dict) and m.get("type") == "null")]
            has_null = len(non_null) < len(members)
            siblings = {k: v for k, v in node.items() if k != key}
            if len(non_null) == 1:
                node = {**siblings, **non_null[0]}
            elif not non_null:
                node = {**siblings, "type": "object"}
            else:
                # Genuine multi-type union (rare here): keep the cleaned members.
                node = {**siblings, key: non_null}
            if has_null:
                node["nullable"] = True
            break

    # Recurse the remaining subschema positions.
    if isinstance(node.get("properties"), dict):
        node["properties"] = {k: _normalize(v) for k, v in node["properties"].items()}
    if "items" in node and isinstance(node["items"], (dict, list)):
        node["items"] = _normalize(node["items"])
    if isinstance(node.get("prefixItems"), list):
        node["prefixItems"] = [_normalize(v) for v in node["prefixItems"]]
    for key in ("anyOf", "oneOf"):
        if isinstance(node.get(key), list):
            node[key] = [_normalize(v) for v in node[key]]

    # Drop unsupported structural keywords.
    for key in _STRIP_KEYS:
        node.pop(key, None)

    # Drop `format` values the subset does not recognise (advisory only).
    if "format" in node and node["format"] not in _SAFE_FORMATS:
        node.pop("format")

    # Gemini requires an explicit type wherever one is structurally implied.
    if "type" not in node:
        if "properties" in node:
            node["type"] = "object"
        elif "items" in node or "prefixItems" in node:
            node["type"] = "array"
        elif node.get("enum"):
            node["type"] = _infer_type(node["enum"][0])

    # Arrays must declare `items`; synthesise from a tuple schema or fall back.
    if node.get("type") == "array" and "items" not in node:
        prefix = node.get("prefixItems")
        node["items"] = prefix[0] if isinstance(prefix, list) and prefix else {"type": "string"}

    return node


def _infer_type(value: Any) -> str:
    """Best-effort JSON-Schema type name for an enum member value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"
