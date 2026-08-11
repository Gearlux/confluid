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

**Framework** — which engine's API a class belongs to, and therefore what it can be *wired to*:

```python
@configurable(task="classification", role="loss", framework="torch")
class FocalLoss: ...

register(keras.losses.CategoricalCrossentropy,
         task="classification", role="loss", framework="keras")
```

`task` / `role` say what a class is **for**; neither says a torch loss cannot be
handed to a Keras trainer. Without the axis, a picker asked for "a classification
loss" offers both and the mismatch surfaces as a type error far from its cause.
`framework` closes that:

```python
get_registry().list_classes(task="classification", role="loss", framework="keras")
```

The unit is the **API, not the tensor runtime** — a `keras.losses.Loss` is
`"keras"` whether Keras runs on TensorFlow, JAX or PyTorch, because the API is
what decides whether a trainer can consume it. Values in use: `torch`, `keras`,
`tensorflow`, `jax`, `mlx`, `sklearn`.

It is indexed (`list_classes(framework=…)`, `list_frameworks()`) and deliberately
**not** folded into `category`, which stays `f"{task}_{role}"`. Classes left
untagged are absent from the index, so a `framework` filter returns only what
explicitly claims that engine — probe without the filter to see everything.

## Registering a class you don't own

`register()` is the entry point for off-the-shelf classes and builder functions —
it stamps the discovery tags without wrapping validation, so nothing about the
class's own behaviour changes.

It also carries the two **accept-list controls** the decorator has, and that is
not a symmetry nicety. A class you own can be shielded from broadcast keys two
ways: declare its parameters, or add a decorator argument. A class you *don't*
own can be shielded by neither — and a third-party constructor taking `**kwargs`
is exactly the case with no accept-list at all, so confluid errs permissive and
every bare top-level key in the document reaches it (see
[Broadcasting](broadcasting.md) → "Classes with `**kwargs` constructors"). Its
author never chose that; they never saw confluid. `register()` is where you can:

```python
from confluid import register

# No bare or glob key ever cascades in. Addressed blocks still work:
# `Sink: {path: out}` and `sink.path: out` configure it normally.
register(SomeLibraryClass, name="Sink", role="sink", broadcast=False)

# Declare `__init__`-body slots the AST scan cannot see. Body slots are found by
# reading `__init__` SOURCE, which is absent in compiled / frozen / zip
# deployments — and you cannot add a decorator to a class you do not own.
register(VendorTrainer, name="VendorTrainer", broadcast_attrs=["optimizer", "scheduler"])
```

`broadcast_attrs` **unions** with whatever the scan finds, so declaring can never
lose a scanned name — it only adds.

The parameter-level opt-out `NoBroadcast[T]` needs no registration support: it is
an annotation, so it works wherever the annotation can be written.

## Bootstrapping the registry from entry points

Registration is an import side effect, so a process only sees the classes whose
modules it has imported. Packages advertise those modules under the
`confluid.configurables` entry-point group, and one explicit call imports them all:

```toml
[project.entry-points."confluid.configurables"]
mypkg-ops = "mypkg.ops"        # key arbitrary; value = the module to import
```

```python
import confluid

loaded = confluid.load_configurables()   # {entry_name: module}
```

Errors are collected per entry: a package that fails to import is skipped with one
warning and reported in the returned dict as the exception instance, so a broken
install never blanks every other package's registrations. The call is explicit —
confluid never runs it at its own import — and repeating it is cheap, since
Python's module cache makes re-imports no-ops. See
[Extending the Discovery Surface](extending-discovery.md) for the full plugin
contract.

## When two classes share a name

A registered name is not required to be unique. Two classes legitimately share one when a
discovery tag tells them apart — the same op implemented per framework, or a library that
publishes one name as both a loss and a metric:

```python
@configurable(category="op", group="fft/numpy")
class FourierOp: ...

@configurable(category="op", group="fft/torch")     # same name, different group
class FourierOp: ...
```

Both are registered, both are enumerable, and both are reachable. What changes is how you
address them: an unambiguous name is published under itself, while a shared name is
published under each class's canonical dotted key, so the usual enumerate-then-look-up
pass reaches both.

```python
get_registry().list_classes(category="op")
# {'myops.numpy.FourierOp', 'myops.torch.FourierOp'}

get_registry().get_class("FourierOp")                    # AmbiguousClassError
get_registry().get_class("FourierOp", group="fft/torch") # the torch one
get_registry().get_class("myops.torch.FourierOp")        # ...or address it directly
```

A **bare** lookup of a shared name raises [`AmbiguousClassError`](errors.md) listing the
candidates — binding one of them would otherwise depend on which module imported last.
The tag filters mirror `list_classes`, so a consumer that already narrowed an enumeration
carries the same filter into the lookup.

In a config, three spellings choose one:

```yaml
op: !class:myops.torch.FourierOp()      # the dotted path — always unambiguous
op: !class:FourierOp@group=fft/torch()  # a tag selector
op: !class:FourierOp@framework=$engine() # ...whose value may come from the document
engine: torch
```

The `@axis=value` selector accepts any of the five discovery axes (`category`, `group`,
`task`, `role`, `framework`), comma-separated for more than one, and an unknown axis is
rejected rather than silently ignored. A value written `$key` is read from the loaded
configuration — so a document says which engine it is targeting once, and every ambiguous
target follows it. Dotted paths work there too (`$run.engine`), and because the value is
resolved at construction time it also works for a target nested inside another marker's
kwargs.

> **`$key` reads the document being materialized.** A marker that is deliberately kept
> deferred and flowed LATER by your own code — `flow(self.loss)` inside a trainer's
> `run()`, long after `load()` returned — is outside that window, and the selector fails
> naming the key it could not find. Write the value literally there
> (`@framework=keras`), or flow inside `confluid.active_context(document)`.

Pair it with a [scope block](targets.md) that declares the key, and one config drives
either engine:

```yaml
torch_run: !scope:engine=torch
  engine: torch
keras_run: !scope:engine=keras
  engine: keras
loss: !class:CrossEntropy@framework=$engine()
```

**When it is a mistake instead.** If a second class claims a name and NO tag distinguishes
it, there is nothing a lookup could select on — so that case keeps its last-write-wins
behaviour but logs a warning naming both classes. Give one a distinguishing tag, or an
explicit `name=`.

**Behavioral marks** — stamp-only flags (no registry index; consumers read the class attribute):

```python
@configurable(category="op", random=True)     # __confluid_random__: non-deterministic output
class AWGNOp: ...                             # editors re-execute its node on every run

@configurable(category="op", constant=True)   # __confluid_constant__: outputs are a PURE
class ImpairmentsTxConfig: ...                # function of the constructor config

@configurable(eager=True)                     # __confluid_eager__: __init__ does real work
class Resampler: ...                          # from its params (a plain Python class)

@configurable(capture=False)                  # __confluid_no_capture__: ctor kwargs are NOT
class Embedder: ...                           # captured — heavy args aren't kept alive
```

`constant=True` promises that instances (and their declared `@output` properties) depend only on constructor parameters — no I/O, no record input, no hidden state. Exporters use it to fold a value-producer node into a static config: a graph exporter can hoist the node as a top-level `!class:` entry and rewire consumers via dotted `!ref:<name>.<output>` instead of dropping the wired values. Declaring `constant=True` together with `random=True` raises a `ConfigurableDefinitionError` (a `ValueError`).

`eager=True` declares a plain-constructor class — see [Eager Classes](eager-classes.md). Its runtime effect: `configure()` warns when a constructor-param attribute is set post-construction (the `__init__` work will not re-run). Orthogonal to `random`/`constant`.

`capture=False` disables the constructor-kwargs capture (`__confluid_kwargs__`) on both the direct-construction and YAML paths, so heavy, disposable constructor arguments are not held by reference for the instance lifetime — at the cost of dump fidelity for transformed params. See [Eager Classes → Opting out](eager-classes.md#opting-out-capturefalse).

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

## Reading a class's marks

`marks(target)` returns a frozen record of every mark a class (or decorated
callable, or instance) carries — the discovery axes (`task` / `role` /
`framework` / `category` / `group`), the display name, and the behavioural
flags (`lazy`, `random`, `constant`, `eager`, …):

```python
from confluid import marks

m = marks(SomeModel)
m.role, m.task      # ("model", "classification")
m.lazy              # True — compose a deferred value for this target
```

This accessor is the public contract; the underlying ``__confluid_*__``
attribute names are internal and may change.
