# Confluid

**Confluid** is a modern, hierarchical configuration and dependency injection framework for Python, built for researchers and engineers who need modularity and 100% reproducibility in their experiment pipelines.

## Key Features
- **Post-Construction Configuration:** Configure existing objects without requiring re-instantiation.
- **Strict Gated Hierarchy:** Prevents deep-traversal into non-configurable third-party objects.
- **Third-Party Registration:** Easily make third-party classes (like PyTorch Optimizers) — or plain builder **functions** — part of your configurable graph via `@configurable` / `register`.
- **Smart Reference Resolution:** Uses `!ref:` syntax for cross-config references (shared instance), `!clone:` for a deep copy, and `${...}` for string interpolation of environment variables (`${HOME}`) AND config keys (`${train.dataset}`).
- **Deferred Initialization:** A node is built eagerly (`!class:Model()`) or left as a deferred recipe (`!class:Model`); `!lazy:` keeps a node deferred until you `flow()` it with runtime-injected arguments (e.g. an optimizer needing `params=model.parameters()`). See [Tags & Deferred Initialization](#tags--deferred-initialization).
- **Full Hierarchy Dumping:** Export your runtime state to YAML/JSON and reconstruct it later.
- **Docstring-Derived Parameter Help:** `to_pydantic` parses each class's Google/NumPy-style `Args:` block into pydantic `Field(description=...)`; `parse_param_docs(cls_or_fn)` exposes the same `{param: help}` mapping directly, so downstream GUIs (navigaitor's form-spec, FluxStudio's node tooltips) document a constructor argument once, at the source.
- **I/O Contract:** `@output` marks a `@property` as a Runnable's output (a trainer's trained model) and `Mandatory[T]` flags an input as required even when defaulted; `output_specs(cls)` / `input_specs(cls)` expose the contract so FluxStudio sockets, navigaitor's form-spec, and MCP schemas read it from one source. See [I/O Contract](#io-contract-output-properties--mandatoryt-inputs).
- **LLM-Safe Tool Schemas:** `sanitize_schema(json_schema)` downgrades a pydantic-derived JSON Schema (`$ref`/`$defs`, nullable `anyOf`, `allOf`, `const`, …) to the OpenAPI-3.0 subset that strict LLM function-calling APIs accept — Google Gemini (the **Antigravity** CLI) rejects `$ref`, so a typed `config:` MCP tool is otherwise uncallable there. The workspace's MCP servers (navigaitor, sairen) run every advertised tool schema through it. Pure (stdlib only, no input mutation); rewrites only the advertised schema, never validation.
- **Flat-View Ordered Matching:** When a class materializes, the visible context is the document minus the descent path; matching scalars are applied in YAML document order with **last-write-wins** semantics. Explicit kwargs are not privileged — every source (own kwargs, sibling broadcasts, class-name blocks) takes its slot at its document position.

> **Scopes:** Conditional overlays use explicit YAML tags — `!scope:debug`, `!scope:task=classification` (or equivalently `!scope:task(classification)`), and the `!notscope:…` negative twins. Activate via `confluid.load(path, scopes=["debug", "task=classification"])` or, in liquifai-built CLIs, via `--scope debug` / `--scope task=classification` / `--task classification`. See the [Scopes](#scopes) section below.

## Design Goals & Requirements

### Configuration Engine
- **Dotted-Key Resolution:** Allow flat overrides to target nested attributes (e.g. `model.layers: 10`).
- **Tag-Based IR:** Use standard YAML tags (`!class:Name` deferred / `!class:Name()` eager, `!lazy:Name`, `!ref:path`, `!clone:path`) instead of proprietary symbols like `@`. See [Tags & Deferred Initialization](#tags--deferred-initialization).
- **Object-Based Internal Representation:** Use typed `Reference` and `ClassReference` objects for internal resolution.

### Dependency Injection
- **Automatic Hydration:** Support `@configurable` decorator for automatic class registration and instantiation.
- **Fluid-Solid Protocol:** Implement a two-stage lifecycle where objects are defined ("Fluid") and then materialized ("Solid").
- **Materialize API:** Provide an explicit `materialize()` function to instantiate objects from already-resolved configuration.

### Robustness
- **IR-Aware Merging:** `deep_merge` and `expand_dotted_keys` must traverse into `ClassReference` arguments.
- **Circular Reference Detection:** Gracefully handle and report circular dependencies in the object graph.
- **Type Coercion:** Integrate `parse_value` to ensure CLI strings (e.g. "100") are cast to correct types (int 100).

## Quick Start

### 1. Define Configurable Classes
```python
from typing import Optional

from confluid import configurable

@configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1):
        self.layers = layers
        self.dropout = dropout

@configurable
class Trainer:
    # Lazy + zero-arg: every parameter is defaulted, so `Trainer()` works and the model is
    # wired afterwards. See "Class Design: Lazy Init & Zero-Arg Construction" below.
    def __init__(self, model: Optional[Model] = None, lr: float = 0.001):
        self.model = model
        self.lr = lr
```

### 2. Configure via YAML
```yaml
# experiment.yaml
n_layers: 10

Trainer:
  lr: 0.0001
  model: "!class:Model(layers=!ref:n_layers)"
```

### 3. Load and Apply
```python
from confluid import load_config, configure

# Instantiate with defaults
model = Model()
trainer = Trainer(model=model)

# Apply configuration
config = load_config("experiment.yaml")
configure(trainer, config)

print(trainer.lr) # 0.0001
print(trainer.model.layers) # 10
```

`configure_from_file` collapses the load + apply into one call — handy when the config lives on disk:

```python
from confluid import configure_from_file

# Equivalent to configure(trainer, config=load_config("experiment.yaml"))
configure_from_file(trainer, path="experiment.yaml")
```

It reads the file via `load_config` (so `include:` / `import:` directives and `!class:` / `!ref:` markers are honoured) and then applies it exactly as `configure` does. A missing path raises `ConfigFileNotFoundError`.

### `${...}` interpolation — env vars AND config keys

A `${...}` placeholder in a string value is substituted at load time. The name decides the source:

- **Plain name → environment variable** (the historical behaviour): `${HOME}`, `${PORT:8080}` (with an optional `:default`).
- **Dotted / bracketed name → another config key**, resolved against the config tree with the same path machinery `!ref:` uses: `${train.dataset}`, `${items[0]}`, `${db.port:5432}`.

```yaml
train:
  dataset: RFUAV
  version: v3
# Mix env + config keys in one string:
data_dir: "${DATA_ROOT}/${train.dataset}/${train.version}/data"   # -> /store/RFUAV/v3/data
epochs:   "${train.epochs}"                                        # whole match keeps the native int type
```

A whole-string match (`"${train.epochs}"`) returns the value with its real type; an embedded match substitutes `str(value)` (scalars only). Local (sibling) keys win over global, mirroring `!ref:`. On a miss the `:default` applies, else the literal `${...}` is left in place. Interpolation is a single pass, so a referenced key must already be a literal/scalar — for wiring a live object into another config slot, use `!ref:` instead.

Because the dispatch is on the name shape, every pre-existing `${VAR}` keeps meaning an environment variable — only names containing a `.` or `[` hit the config tree.

### 4. Dump and Reconstruct
```python
from confluid import dump, load

# Export current state
state_yaml = dump(trainer)

# Recreate exact same hierarchy in a new process
new_trainer = load(state_yaml)
```

### Introspecting a config without paying for it

When a tool needs a config's **structure** (its nodes + wiring) but not its expensive side effects
— e.g. a visual editor importing a training YAML without downloading datasets or building model
backbones — use one of:

```python
from confluid import resolve, materialize, load

# (a) resolve(): broadcast-resolved Fluid MARKERS, nothing instantiated. !ref: targets are shared by
#     identity (a fan-out is one object reached twice); an unresolved !ref:NAME stays a Reference.
markers = resolve("config.yaml")        # {key: Instance/Lazy/Class marker, ...}

# (b) solidify=False: live-but-inert objects — constructed (cheap, per zero-arg / lazy-init) but the
#     expensive post-flow solidify() (e.g. building a model backbone) is suppressed for the subtree.
graph = materialize(data, solidify=False)   # also load(..., solidify=False) / flow(obj, solidify=False)
```

Both leave `Lazy` (`!lazy:`) slots deferred and default behaviour unchanged (`solidify=True`).

### Capturing the YAML include tree

`load_config_with_paths(path)` returns both the loaded dict AND the ordered
list of every YAML file that contributed to it — entrypoint first, then
each transitively `include:`-d file in load order, deduplicated. Used by
liquifai's CLI bootstrap so downstream tools (e.g. `marainer.trainer`)
can log every config file as a reproducible run artifact.

```python
from confluid import load_config_with_paths

data, paths = load_config_with_paths("experiment.yaml")
# data:  the same dict load_config would return
# paths: [PosixPath('.../experiment.yaml'), PosixPath('.../common.yaml'), ...]
```

Plain `load_config(path)` keeps its single-Dict return, so callers that
don't care about the tree are unaffected.

## Class Design: Lazy Init & Zero-Arg Construction

Confluid configures objects **after** they are built, so every `@configurable` class is designed
to be cheap and side-effect-free to construct, then configured. Four rules:

1. **Lazy constructor — no functional work.** `__init__` only *stores* values. No I/O, network,
   file reads, dataset/model materialization, or heavy compute. Real work is deferred to a property
   or method.
2. **Zero-arg construction works.** `Cls()` must succeed — every parameter is defaulted. A value
   genuinely required to *run* is still a defaulted parameter, validated **lazily** (where it is
   used) with a clear error, not in `__init__`.
3. **Derived state → read-only `@property`, recomputed.** State derived from the configurable
   inputs is a read-only property (not a stored attribute), so it never goes stale when the inputs
   change. Read-only properties are invisible to Confluid's config surface — never set by
   `configure`, never `dump`ed, rebuilt after `load()`. Cache (into a private `_field`) **only** an
   expensive external materialization whose inputs are stable by first use.
4. **Params stay in the constructor**, documented in the `Args:` docstring — so they remain visible
   to static introspection (`to_pydantic`, `parse_param_docs`, the navigaitor form-spec / FluxStudio
   widgets).

```python
from typing import Any, Optional
from confluid import configurable

@configurable
class DataSource:
    def __init__(self, path: str = "", split: str = "train") -> None:
        # Lazy: store config only — no load here. `DataSource()` is valid.
        self.path = path
        self.split = split
        self._data: Optional[Any] = None   # private cache for the expensive materialization

    @property
    def data(self) -> Any:
        """Loaded on first access and cached (reset `_data` to reload).

        A required-at-use value (`path`) is validated here, lazily — not in `__init__`.
        """
        if self._data is None:
            if not self.path:
                raise ValueError("DataSource.path is empty — set it before reading `data`.")
            self._data = _load(self.path, self.split)   # the real, expensive work
        return self._data

    @property
    def size(self) -> int:
        """Derived, cheap → a *recomputing* property (never stale, never cached)."""
        return len(self.data)
```

> **Why recompute by default?** `configure()` introspects an instance with `getattr` over
> `dir(obj)`, which *executes* read-only property getters. A *cached* property that derives from a
> *post-configured* attribute would freeze a pre-configuration value. Recompute unless the work is
> expensive and its inputs are construction-time-stable. (`flow()`-built objects are unaffected —
> `flow` uses `vars(obj)` + post-init setattr and never touches properties.)

`dataflux.sources.HuggingFaceSource` is the reference implementation; runnable end-to-end examples
live in [`examples/`](examples/) (`ml_pipeline.py`, `reproducible_experiment.py`,
`basic_registration.py`).

## Tags & Deferred Initialization

A Confluid config is built from six YAML tags. Each parses into a typed
**Fluid** — a deferred *recipe* — that `load()` / `materialize()` resolves into
a live **Solid** object. This two-stage lifecycle is what lets Confluid
broadcast values into a node *before* it is built and inject runtime arguments
*as* it is built.

The tags are parsed only by confluid's own loader (a private `yaml.SafeLoader`
subclass) — a plain `yaml.safe_load` elsewhere in your process does **not**
recognize them and will raise on the unknown tag, exactly as it would without
confluid installed. Always go through `confluid.load` / `load_config` to parse
tagged documents.

| Tag | Purpose | Produces |
|---|---|---|
| `!ref:path` | Late-bound reference to another node (shared instance) | `Reference` |
| `!clone:path` | Like `!ref:` but returns a deep copy | `Clone` |
| `!class:Name` / `!class:Name(...)` | Class node — **deferred or eager depending on `()`** (below) | `Class` / `Instance` |
| `!lazy:Name(...)` | Class node that **always** stays deferred (runtime injection) | `Lazy` |
| `!scope:KEY[=VAL]` / `!notscope:…` | Conditional overlay (see [Scopes](#scopes)) | `ScopeBlock` |

### The lifecycle: Fluid → Solid

| State | What it is | Tag types |
|---|---|---|
| **Fluid** (deferred) | A recipe, not yet built. Still receives broadcast kwargs. | `Class`, `Lazy`, `Reference`, `Clone` |
| **Solid** (live) | The actual Python instance your code uses. | — |

`load(text)` (≡ `load(text, flow=True)`) and `materialize(data)` walk the tree
and turn Fluids Solid — but **not all of them**, by design (see the flow table
further down). `load(text, flow=False)` stops at the Fluid layer so you can
inspect or re-merge the IR before anything is constructed.

### `!class:` — one tag, two behaviours (the bit the docs glossed over)

`!class:` is a single tag, but the **parentheses decide whether it is built
eagerly or left deferred**. `!class:Model` and `!class:Model()` look almost
identical and are *not* the same thing:

| YAML | Parses to | After `load()` | Built? |
|---|---|---|---|
| `m: !class:Model` | `Class` Fluid | a deferred `Class` stub | **No** |
| `m: !class:Model` + indented block | `Class` Fluid (+ block kwargs) | a deferred `Class` carrying those kwargs | **No** |
| `m: !class:Model()` | `Instance` Fluid | a live `Model` | **Yes** |
| `m: !class:Model(layers=10)` | `Instance` Fluid (+ kwargs) | a live `Model(layers=10)` | **Yes** |
| `m: !class:Model()` + indented block | `Instance` Fluid (+ block kwargs) | a live `Model(**block)` | **Yes** |

**Rule of thumb: a trailing `()` means "build it now."** `!class:Model` is a
deferred stub; `!class:Model()` is a live instance. Everything else follows from
that one bit.

**The target may be any callable, not just a class.** A `!class:` / `!lazy:`
target resolves to any callable — a class OR a plain builder/factory **function**
(e.g. `!lazy:torchvision.models.detection.fasterrcnn_resnet50_fpn`,
`!class:timm.create_model`). `flow()` introspects the callable's own signature,
so both its stored kwargs and any runtime-injected kwargs are passed through
(runtime wins) — e.g. `flow(deferred_builder, num_classes=37)` calls
`builder(..., num_classes=37)`. This is what lets a trainer flow a `!lazy:`
model builder with a dataset-derived dimension and no wrapper class. **`to_pydantic`
is callable-aware too** — it generates a config schema from a builder function's
own signature, so a `register`-ed function (e.g. `register(fasterrcnn_resnet50_fpn,
task="detection", role="model")`) surfaces in navigaitor's form-spec / MCP schemas
and FluxStudio's node palette exactly like a class (un-JSON-schemable param types —
e.g. torchvision's `Weights` enums or a `Callable[...]` arg — degrade to `Any`).

**You can also `@configurable` / `register` a FUNCTION directly**, not just a class:

```python
from confluid import configurable

@configurable
def build_model(num_classes: int = 10, backbone: str = "resnet18"):
    return ...   # a plain builder function is now a first-class configurable target
```

A `@configurable` function has its **call** validated against its signature (the
callable analogue of a class's `__init__` validation) — an unknown kwarg or a
type-invalid value raises the same structured pydantic error, under the same
`ValidationPolicy` (`@configurable(validate=False)` opts out). `register(fn, ...)`
registers an off-the-shelf builder for discovery without wrapping validation, just
as it does for a third-party class.

```yaml
# Deferred — the receiving object gets a Class stub and builds it itself,
# after broadcasting has filled in matching scalars.
model: !class:Model

# Eager — Confluid builds the Model during load(). Inline scalars are coerced
# to their declared types (layers → int 10, not "10").
model: !class:Model(layers=10,dropout=0.5)

# Eager with a block body — the empty () flips it to eager; the indented
# block carries the kwargs. Use this form when a kwarg value is itself a
# tag (!ref:, !class:, …) or a nested mapping.
model: !class:Model()
  layers: !ref:n_layers              # nested tags are fine as block values
  head: !class:LinearHead(units=128)
```

Three grammar notes (all pinned by the test suite):

- **Inline scalars are coerced.** In both the unquoted tag (`!class:Model(layers=7)`)
  and the quoted-string (`"!class:Model(layers=7)"`) forms, each inline
  `key=value` is run through `parse_value`, so `7` → `int`, `0.01` → `float`,
  `true` → `bool`, `null` → `None`. Two YAML-level caveats on the **unquoted**
  form: it can't contain **spaces** (YAML ends a tag at whitespace — write
  `(a=1,b=2)`, not `(a=1, b=2)`), and it can't carry a **nested tag**
  (`!ref:` / another `!class:`), because YAML allows only one tag per node.
- **For a nested `!ref:` / `${ENV}`, or for spaces, quote the tag.** The
  quoted-string form `"!class:Adam(lr=!ref:base_lr)"` is resolved through the
  resolver, which both coerces scalars *and* resolves a nested reference. (A
  block body works too: nested tags are valid as block values.) Note `1e-3`
  is *not* a YAML float — write `1.0e-3` or `0.001`.
- **Inline kwargs and a block body now merge.** When both are present the inline
  `(k=v)` kwargs combine with the block; on a key in both, the **block body
  wins** (it's later in document order — last-write-wins). Inline-only keys are
  preserved, not discarded.

> A legacy, colon-free spelling — `!class Model` / `!class Model(lr=1)` — is
> kept for backward compatibility. It mirrors the same eager/deferred `()` rule
> but supports scalars only, not a block body. Prefer the `!class:` form.

### Deferred initialization: `Class` stubs, `!lazy:`, and `flow()`

A deferred node is built later by calling **`flow(node, **runtime_kwargs)`** —
idempotent (live objects pass through unchanged), with runtime kwargs winning
over stored ones. There are two distinct reasons to defer, and a tag for each.

**1. `Class` stub — "broadcast into it, but I'll build it myself."**
A bare `!class:Foo` (or a `Class(Foo)` default in Python) stays a stub so the
*receiving* `@configurable` object can build it on its own terms — typically
after Confluid's broadcasting has merged matching scalars into its kwargs.

```python
from typing import Any
from confluid import Class, configurable, flow

@configurable
class Car:
    def __init__(self, engine: Any = Class(Engine), color: str = "red"):
        self.engine = engine             # stays a Class stub after load()
    def start(self) -> None:
        self.engine = flow(self.engine)  # built here, with broadcasts applied
```

What `materialize()` / `load()` actually build vs. leave deferred:

| Node | Eagerly built? |
|---|---|
| `Instance` (`!class:Foo()`) | **Yes**, always |
| Root-level Fluid (the whole document is `!class:…`) | **Yes** |
| `Class` nested in a **`@configurable`** parent | **No** — the parent receives the stub |
| `Class` nested in a **non-`@configurable`** parent (e.g. `pl.Trainer`) | **Yes** — the third-party ctor won't flow it, so Confluid does |
| `Lazy` (`!lazy:…`) | **No**, anywhere — see below |

**2. `Lazy` — "this genuinely cannot be built until runtime."**
Some objects need an argument that does not exist at config time — the textbook
case is an optimizer that needs `params=model.parameters()`. Declare it with the
**`!lazy:`** tag, which mirrors the `!class:` grammar (`!lazy:Foo`,
`!lazy:Foo(lr=1e-3)`, or `!lazy:Foo` + block) but **always** produces a deferred
`Lazy` — parentheses or not:

```yaml
# Inline kwargs are coerced just like !class: — lr is a float here.
optimizer: !lazy:torch.optim.Adam(lr=0.01)
```

```python
def configure_optimizers(self):
    # runtime injection: params isn't known until the model exists
    return flow(self.optimizer, params=self.parameters())
```

> ⚠️ `!lazy:` must be written as a real (unquoted) YAML tag — the "quote the
> tag" trick does **not** apply (a quoted `"!lazy:…"` stays a plain string,
> never a `Lazy`). Its inline kwargs are coerced and merge with a block body
> exactly as for `!class:`; only the deferral differs.

**`Class` vs `Lazy` — when to reach for which.** Both are deferred, but they
differ in how *external* deep-flow walkers treat them. Liquifai's
`flow_mode="auto"` (and any caller that recursively flows a graph) will
**eagerly build a bare `Class`** — which crashes if the target needs a runtime
argument. A `Lazy` is *never* auto-flowed by anything; only an explicit
`flow(node, …)` builds it. So:

- Use a **`Class` stub** when the target *can* be built from config alone but
  you want to build it yourself (to apply broadcasts, sequence side effects, …).
- Use **`!lazy:` / `Lazy`** when building the target without a runtime-injected
  argument would fail.

**Python-side `Lazy[T]` annotation.** The same deferral can be pinned on a
*constructor parameter*, so an auto-flow walker leaves even a plain
`Class` / `Instance` default in that slot alone:

```python
from typing import Any
from confluid import Class, configurable, flow
from confluid.lazy import Lazy   # Lazy[T] == Annotated[T, <marker>]

@configurable
class Trainer:
    def __init__(self, optimizer: Lazy[Any] = Class(Adam, lr=1e-3)):
        self.optimizer = optimizer           # auto-flow walkers skip this slot
    def configure_optimizers(self):
        return flow(self.optimizer, params=self.parameters())
```

`Lazy[T]` reads as plain `T` to type-checkers; the marker is runtime-only and is
discovered via `lazy_param_names(cls)`. It is the Python-annotation twin of the
YAML `!lazy:` tag: **the tag defers a *value*, the annotation defers a *slot*.**

## I/O Contract: `@output` properties & `Mandatory[T]` inputs

A *Runnable* class (a trainer/evaluator — anything whose product is consumed
downstream) declares an explicit **I/O contract** that GUIs and agents read from
one source: which `@property` getters are its **outputs**, and which inputs are
**mandatory** vs **nullable**. FluxStudio (output sockets + required vs optional
input sockets), navigaitor's `get_node_form_spec`, and MCP schemas all consume it.

```python
import torch.nn as nn
from typing import Any, Optional, Union
from confluid import Mandatory, configurable, output, input_specs, output_specs

@configurable
class Trainer:
    def __init__(
        self,
        model: Mandatory[Union[nn.Module, Any]],   # mandatory input (must be wired)
        num_classes: Optional[int] = None,          # nullable / optional
    ) -> None:
        self.model = model
        self.num_classes = num_classes

    @property
    @output                                         # NOTE: @output UNDER @property
    def trained_model(self) -> nn.Module:
        """The trained model produced by run()."""
        return self.model

output_specs(Trainer)   # [{'name': 'trained_model', 'type': 'Module', 'description': '...'}]
input_specs(Trainer)    # [{'name': 'model', 'required': True, 'nullable': False, ...},
                        #  {'name': 'num_classes', 'required': False, 'nullable': True, ...}]
```

* **`@output`** (mirrors `@ignore_config`) marks a read-only `@property` getter as
  a declared output. Apply it **under** `@property` so it stamps the getter, not
  the `property` object. Because the property is read-only/derived, it is already
  excluded from `to_pydantic` — it never becomes a config knob and round-trips
  cleanly. `output_specs(cls)` enumerates them (MRO-walked; subclass override wins).
* **`Mandatory[T]`** (an `Annotated` marker, mirroring `Lazy[T]`; named to avoid
  `typing.Required` confusion) flags an input mandatory **even when it carries a
  default** for zero-arg construction — the structural signal (no default /
  non-`Optional`) already implies mandatory, but the marker restores the contract
  when the **Zero-Arg Construction** mandate forces a default onto a genuinely
  required class/`Fluid` slot. `input_specs(cls)` reports `{required, nullable}`
  per param (`required = no-default OR Mandatory`). The marker is stripped by
  `to_pydantic`, so it never leaks into the JSON Schema, and composes with
  `Lazy` (`Mandatory[Lazy[T]]`).

### `!ref:` vs `!clone:` — shared instance vs. deep copy

Both point at another node in the same document; the difference is identity.

```yaml
proto: !class:Box(size=3)
a: !ref:proto     # a is the SAME object as proto (and as b)
b: !ref:proto
c: !clone:proto   # c is a deep copy — independent of proto
```

Within one `materialize()` pass, a marker reached directly or through any number
of `!ref:` resolves to **one** live instance (so `a is b`). `!clone:` opts out
with an explicit `deepcopy`, and may carry extra kwargs to override on the copy
(`!clone:proto` + a block). `!ref:` also resolves dotted attribute / method
paths — `!ref:my_split.train`, `!ref:some_obj.build()` — against that single
materialized instance.

## Validation

Every `@configurable` class has its `__init__` wrapped at decoration time to validate kwargs against the auto-generated pydantic schema (`confluid.to_pydantic(cls)`). The validation fires at three points, each with its own strict / warn / off knob:

| Point | When it runs | Policy field | Env var |
|---|---|---|---|
| Constructor | Every direct Python instantiation | `policy.init` | `CONFLUID_VALIDATE_INIT` |
| YAML materialization | `confluid.flow()` / `materialize()` / `load()` instantiating a `!class:` Fluid | `policy.yaml` | `CONFLUID_VALIDATE_YAML` |
| MCP tool entry | Navigaitor subprocess tools, just before spawning marainer | `policy.tool` | `CONFLUID_VALIDATE_TOOL` |

All three default to `"strict"` — pydantic `ValidationError` is raised. `"warn"` logs the error to `confluid.validation` and lets the call proceed. `"off"` skips validation entirely.

**Pydantic is an optional dependency** (`pip install 'confluid[pydantic]'`). The core — loading, references, scopes, `flow()`, `configure()` — never needs it. Without the extra, every validation point degrades to `"off"` (a one-time log line records the downgrade) and the schema-export API (`to_pydantic`, `confluid_class_of`, `lazy_param_names_of`) raises `ImportError` naming the extra. Packages that rely on schema export or want validation enforced (navigaitor, fluxstudio, waivefront-helios) must depend on `confluid[pydantic]`.

```python
from confluid import configurable, get_policy, set_policy

@configurable
class Optimizer:
    def __init__(self, lr: float = 1e-3, weight_decay: float = 0.0) -> None:
        self.lr = lr
        self.weight_decay = weight_decay

# strict (default) — pydantic catches the bad type and raises
Optimizer(lr="not a float")  # → pydantic.ValidationError

# Relax YAML loads but keep direct Python instantiation strict
set_policy(yaml="warn")

# Or via env: CONFLUID_VALIDATE_INIT=warn python train.py
```

**Tightening constraints** lives on the annotation, not the body:

```python
from typing import Annotated
from pydantic import Field

@configurable
class TimmClassifierModel:
    def __init__(
        self,
        num_classes: Annotated[int, Field(ge=1)] = 1000,
        drop_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0,
    ) -> None: ...
```

**Opt out per class** when the constructor is intentionally untyped:

```python
@configurable(validate=False)
class ExperimentalThing:
    def __init__(self, **kwargs): ...  # too dynamic for pydantic
```

**Discovery category** — group classes by taxonomy so navigaitor's `list_configurable_classes(category="loss")` filter can find them:

```python
@configurable(category="loss")
class FocalLoss:
    def __init__(self, gamma: float = 2.0) -> None: ...
```

**Presentation group** — an optional free-form, path-like sub-grouping *within* a category, for visual editors. Unlike `category` / `task` / `role` (which gate *what* a consumer is offered), `group` only organises presentation — FluxStudio nests a node's palette folder as `<Package>/<Category>/<group>`:

```python
@configurable(category="op", group="numpy")        # FluxStudio: Taidal/DataFlux/Op/numpy
class StandardizeOp: ...

@configurable(category="op", group="fft/numpy")     # path-like groups nest further
class RealFftOp: ...
```

`group` sets `__confluid_group__`, indexes in the registry (`get_registry().list_classes(group="numpy")`, `list_groups()`), and is otherwise inert — an absent group simply leaves the node directly under `<Package>/<Category>`. It is NOT part of the discovery contract.

**Behavioral marks** — two mutually exclusive stamp-only flags (no registry index; consumers read the class attribute):

```python
@configurable(category="op", random=True)     # __confluid_random__: non-deterministic output
class AWGNOp: ...                             # FluxStudio re-executes its node on every run

@configurable(category="op", constant=True)   # __confluid_constant__: outputs are a PURE
class ImpairmentsTxConfig: ...                # function of the constructor config
```

`constant=True` promises that instances (and their declared `@output` properties) depend only on constructor parameters — no I/O, no sample input, no hidden state. Exporters use it to fold a value-producer node into a static config: FluxStudio's ops-export hoists the node as a top-level `!class:` entry and rewires consumers via dotted `!ref:<name>.<output>` instead of dropping the wired values. Declaring `constant=True` together with `random=True` raises a `ConfigurableDefinitionError` (a `ValueError`).

## Error Handling

Every error Confluid raises is typed, rooted at `ConfluidError`, so callers can catch configuration failures distinctly:

```python
import confluid

try:
    app = confluid.load("config.yaml")
except confluid.ConfigFileNotFoundError:
    ...  # the config file (or an include) does not exist
except confluid.ConfigurationError:
    ...  # bad content: unknown !class:, unresolvable !ref:, circular include, ...
except confluid.ConfluidError:
    ...  # any other confluid-specific failure
```

Every concrete class **also inherits the builtin it replaces**, so pre-existing `except ValueError:` / `except FileNotFoundError:` code (and `pytest.raises(ValueError)` tests) keep working unchanged.

| Exception | Also a | Raised when |
|---|---|---|
| `ConfigurationError` | `ValueError` | base for config-content errors (all six below) |
| `CircularIncludeError` | `ValueError` | an `include:` chain revisits a file |
| `ReferenceResolutionError` | `ValueError` | a `!ref:` cannot be resolved (unknown or self-referential) |
| `UnknownClassError` | `ValueError` | a `!class:` target is neither registered nor importable |
| `ConfigurableDefinitionError` | `ValueError` | a `@configurable` declaration is contradictory |
| `ValidationModeError` | `ValueError` | a `CONFLUID_VALIDATE_*` env var holds an unknown mode |
| `ScopeError` | `ValueError` | a scope alias chain is circular |
| `ConfigFileNotFoundError` | `FileNotFoundError` | a config or included file is missing |
| `ConstructionError` | `RuntimeError` | a target's constructor failed and the original exception class cannot be rebuilt (original chained via `__cause__`) |
| `WorkspaceEnvError` | `RuntimeError` | no `.env` found / a required key is unset / a path-typed value is missing |
| `IntrospectionError` | `TypeError` | a class or callable cannot be introspected for schema export |

Note: a failing constructor normally re-raises with the **original** exception class (`Failed to construct X: ...`) — `ConstructionError` is only the fallback for exception classes that cannot be rebuilt from a plain message (e.g. pydantic's `ValidationError`).

## Scopes

Conditional config blocks live at an arbitrary key whose value carries a
`!scope:` / `!notscope:` tag. The key is inert — pick a descriptive label
(`if_debug`, `if_classification`, …); on activation the wrapper disappears
and the block's contents are spliced in at that slot. Three activation
forms are supported, all equivalent at the IR level:

```yaml
# Boolean — flips on with `--scope debug`
if_debug: !scope:debug
  log_level: DEBUG

# Keyed — flips on with `--scope task=classification` (or `--task classification`)
if_classification: !scope:task=classification
  model: !class:ClassifierModel

# Equivalent function-call form
also_classification: !scope:task(classification)
  model: !class:ClassifierModel

# Negation. `!notscope:KEY=VAL` is also active when the user passes no
# `--KEY ...` at all (the *unset ⇒ active* convention).
unless_debug: !notscope:debug
  log_level: WARNING
```

Resolve them by passing `scopes=` to `load()`:

```python
from confluid import load
trainer = load("experiment.yaml", scopes=["debug", "task=classification"])
```

Liquifai apps wire `--scope NAME` / `--scope KEY=VAL` and per-dimension
`--KEY VAL` flags automatically — see liquifai's docs.

## Installation
```bash
pip install git+https://github.com/Gearlux/confluid.git@main
```

## License
MIT
