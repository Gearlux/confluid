# Schema Export

Every `@configurable` class (or registered builder **function**) can describe
itself as a machine-readable schema — the same introspection that powers
confluid's own [constructor validation](validation.md), exposed for anything
that needs to *render* or *check* a configuration: a form generator, a tool
server advertising typed calls to an LLM, a config linter.

Everything on this page needs the pydantic extra:

```bash
pip install "confluid[pydantic]"
```

Without it, `to_pydantic` raises `ImportError` naming the extra, and the
[validation points](validation.md) degrade to `"off"` with one log line.

## `to_pydantic` — a validating model from the signature

```python
from typing import Annotated, Optional
from annotated_types import Interval
from confluid import configurable, to_pydantic

@configurable
class Trainer:
    """A trainer with documented, constrained knobs.

    Args:
        lr: Learning rate for the optimizer.
        epochs: How many passes over the training set.
    """
    def __init__(
        self,
        lr: Annotated[float, Interval(gt=0.0, le=1.0)] = 0.001,
        epochs: int = 10,
        device: Optional[str] = None,
    ) -> None: ...

Model = to_pydantic(Trainer)
Model(lr=0.01)          # validates
Model(lr=5.0)           # ValidationError — outside the declared (0, 1] range
Model.model_json_schema()["properties"]["lr"]["description"]
# "Learning rate for the optimizer."
```

What goes into the generated model:

- **Every constructor parameter** becomes a field, with its annotation, its
  default, and — when the docstring carries a Google-style `Args:` block — its
  description.
- **`__init__`-body slots** of the minimal-constructor pattern
  (`self.optimizer = PartialClass(...)`) surface as optional fields too, so a
  form can offer them ([Class Design](class-design.md)).
- **Constraints ride along**: an `Annotated[..., Field(...)]` or
  `annotated_types` range mark reaches the JSON schema
  (`exclusiveMinimum` / `maximum` …) and is enforced at validation. A range
  mark on a `(min, max)` tuple container is applied per element.
- **Un-schemable leaf types are coerced to `Any`** (tensors, arrays, exotic
  enums, `Callable` params) so schema generation never crashes on a
  registered third-party class — the config still validates structurally.
- The internal marker aliases (`Partial[T]`, `Mandatory[T]`, `NoBroadcast[T]`)
  are **stripped** — they shape engine behaviour, not the schema.

`to_pydantic` accepts a plain **builder function** as well as a class — the
function's own signature is introspected, which is what lets a registered
factory surface in a picker exactly like a class does.

Two metadata hooks ride on the generated model, so a consumer needs no side
table: `confluid_class_of(Model)` names the origin class (the `_target_`
target to emit), and `partial_param_names_of(Model)` (in
`confluid.pydantic_export`) lists the fields that stand for deferred slots —
spell those `_partial_: true` when emitting YAML.

## `parse_param_docs` — the docstring on its own

```python
from confluid import parse_param_docs

parse_param_docs(Trainer)
# {"lr": "Learning rate for the optimizer.", "epochs": "How many passes ..."}
```

The same `Args:` extraction the field descriptions use, for consumers that
skip pydantic entirely (a `--help` renderer, a lightweight form).

## `validate_model` — re-check under a policy mode

`validate_model(instance, mode)` re-validates an already-constructed model
under a [validation-policy mode](validation.md) — `"strict"` raises,
`"warn"` logs without raising, `"off"` is a no-op. It exists for tool
servers that must surface warn-mode outcomes at their own entry point rather
than at model construction.

## `sanitize_schema` — the LLM-safe downgrade

Full JSON Schema uses `$ref`/`$defs`, `allOf`, rich `anyOf`, `const` — all
valid, and all rejected by the stricter function-calling schema dialects some
LLM APIs enforce. `sanitize_schema(schema)` rewrites a schema dict into the
common subset:

```python
from confluid import sanitize_schema

safe = sanitize_schema(Model.model_json_schema())
# $ref/$defs inlined; allOf flattened; Optional[str]'s anyOf collapsed to
# {"type": "string", "nullable": True}; const -> enum; unsupported keywords
# and exotic formats dropped; every object/array declares a type.
```

It is pure (no input mutation) and rewrites only the **advertised** schema —
server-side validation still runs against the full model.

## Related surfaces

- **Runnable contracts** — `@output` properties and `Mandatory[T]` inputs,
  read via `input_specs` / `output_specs`: [I/O Contract](io-contract.md).
- **Where the schema also runs** — the constructor / YAML / tool-entry
  validation points and their strict/warn/off knobs: [Validation](validation.md).
- **Discovery** — enumerating which classes exist to generate schemas *for*:
  [Discovery](discovery.md) and [Extending the Discovery Surface](extending-discovery.md).

## Runnable example

[`examples/schema_export.py`](../examples/schema_export.py) drives one class
through the whole surface — generation, constraint enforcement, docstring
descriptions, origin metadata, and the sanitized downgrade — with
self-checking assertions.
