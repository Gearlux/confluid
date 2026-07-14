# Discovery: Categories, Groups & Behavioral Marks

`@configurable` accepts declarative tags that let downstream tools — MCP discovery
services, form-spec builders, visual-editor node palettes — *discover* classes
instead of grepping module paths.

**Discovery category** — group classes by taxonomy so a discovery service can filter them (`get_registry().list_classes(category="loss")`):

```python
@configurable(category="loss")
class FocalLoss:
    def __init__(self, gamma: float = 2.0) -> None: ...
```

**Presentation group** — an optional free-form, path-like sub-grouping *within* a category, for visual editors. Unlike `category` / `task` / `role` (which gate *what* a consumer is offered), `group` only organises presentation — a visual editor nests a node's palette folder as `<Package>/<Category>/<group>`:

```python
@configurable(category="op", group="numpy")        # palette: <Package>/Op/numpy
class StandardizeOp: ...

@configurable(category="op", group="fft/numpy")     # path-like groups nest further
class RealFftOp: ...
```

`group` sets `__confluid_group__`, indexes in the registry (`get_registry().list_classes(group="numpy")`, `list_groups()`), and is otherwise inert — an absent group simply leaves the node directly under `<Package>/<Category>`. It is NOT part of the discovery contract.

**Behavioral marks** — stamp-only flags (no registry index; consumers read the class attribute):

```python
@configurable(category="op", random=True)     # __confluid_random__: non-deterministic output
class AWGNOp: ...                             # editors re-execute its node on every run

@configurable(category="op", constant=True)   # __confluid_constant__: outputs are a PURE
class ImpairmentsTxConfig: ...                # function of the constructor config

@configurable(eager=True)                     # __confluid_eager__: __init__ does real work
class Resampler: ...                          # from its params (a plain Python class)
```

`constant=True` promises that instances (and their declared `@output` properties) depend only on constructor parameters — no I/O, no sample input, no hidden state. Exporters use it to fold a value-producer node into a static config: a graph exporter can hoist the node as a top-level `!class:` entry and rewire consumers via dotted `!ref:<name>.<output>` instead of dropping the wired values. Declaring `constant=True` together with `random=True` raises a `ConfigurableDefinitionError` (a `ValueError`).

`eager=True` declares a plain-constructor class — see [Eager Classes](eager-classes.md). Its runtime effect: `configure()` warns when a constructor-param attribute is set post-construction (the `__init__` work will not re-run). Orthogonal to `random`/`constant`.

## Schema & help extraction — one docstring, every GUI

Two introspection helpers make the same declaration serve every downstream surface:

- **`parse_param_docs(cls_or_fn)`** parses a Google/NumPy-style `Args:` docstring
  block into a `{param: help}` mapping; `to_pydantic(cls)` feeds the same text into
  pydantic `Field(description=...)`. Document a constructor argument once, at the
  source — form-spec builders and node tooltips both read it.
- **`sanitize_schema(json_schema)`** downgrades a pydantic-derived JSON Schema
  (`$ref`/`$defs`, nullable `anyOf`, `allOf`, `const`, …) to the OpenAPI-3.0 subset
  that strict LLM function-calling APIs accept — Google Gemini rejects `$ref`, so a
  typed `config:` MCP tool is otherwise uncallable there. Run every advertised MCP
  tool schema through it. Pure (stdlib
  only, no input mutation); rewrites only the advertised schema, never validation.

## Runnable example

[`examples/discovery.py`](../examples/discovery.py) tags classes with
`category` / `group` / behavioral marks, queries them back through
`get_registry().list_classes(...)`, and extracts parameter help with
`parse_param_docs`.
