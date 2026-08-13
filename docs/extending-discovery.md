# Extending the Discovery Surface

> **Audience:** developers adding an op / source / model / loss / trainer that should appear
> automatically as an **MCP tool** and as a **node in a visual editor**, with no glue code in either
> consumer. **The golden rule:** never edit the consumer — tag the class once in its home package.
> A missing tag makes it vanish silently (discovery degrades at `logger.debug`).

Companions: [Discovery](discovery.md) (tag mechanics + `examples/discovery.py`),
[Class Design](class-design.md), [Validation](validation.md).

## 0. One registry, many consumers

`@configurable(task=…, role=…, framework=…, category=…, group=…)` populates `ConfluidRegistry`
(indices `_by_category` / `_by_task` / `_by_role` / `_by_framework` / `_by_group`) as a side effect of the
decorated module being **imported** — and confluid ships the loader for that convention: each
package lists its configurable modules under `[project.entry-points."confluid.configurables"]`,
and a consuming service calls `confluid.load_configurables()` once at its own startup — the
blessed bootstrap. It imports every declared module with per-entry error tolerance (a broken
package is skipped with one warning and reported in the returned dict as the exception instance,
so it never blanks the other packages' registrations), and it is explicit-only — never invoked
at confluid import. A consumer may still iterate the group itself, and any import mechanism
fills the registry equally.
An **MCP discovery service** offers classes and validates *values*; a **visual node editor** offers
nodes and enforces *socket types*.

| Concern | File |
|---|---|
| `@configurable` / `register` | `confluid/decorators.py` |
| `list_classes(category=, task=, role=, framework=, group=)` | `confluid/registry.py` |
| Validation policy | `confluid/validation.py` |
| Class → pydantic schema | `confluid/pydantic_export.py` (`to_pydantic`) |
| Docstring `Args:` → field help | `confluid/schema.py` (`parse_param_docs`) |
| JSON Schema → LLM-safe subset | `confluid/llm_schema.py` (`sanitize_schema`) |

## 1. MUST — the minimum to be discovered

1. **Decorate** with `@configurable`, or `confluid.register(cls, …)` for third-party
   classes/functions. Untagged ⇒ not in the registry.
2. **Entry-point the module** so importing it runs the decorators:
   ```toml
   [project.entry-points."confluid.configurables"]
   mypkg-ops-numpy = "mypkg.ops.numpy"                 # key arbitrary; value = module path
   ```
3. **Reinstall the editable after touching entry points** — they freeze into `*.dist-info` at
   install time; a stale install shows an empty palette / missing class with no error. **Never**
   `--reinstall` / `--force-reinstall` (re-resolves internal deps from PyPI → fails). Confirm:
   `python -c "import importlib.metadata as m; print(len(list(m.entry_points(group='confluid.configurables'))))"`
4. **Obey the zero-arg / lazy-init contract** (§4) — else materialization and the generated schema
   both break.
5. **Be in `__all__`** if surfaced from a *package-root* entry point (the editor's second pass gates
   on it).
6. **Public name** — a leading `_` is skipped.

## 2. The taxonomy: `task` × `role`, plus task-agnostic categories

A task-scoped category is **derived**, never typed by hand. Declare `task` + `role`; confluid stamps
`__confluid_task__` / `__confluid_role__` **and** derives `category = f"{task}_{role}"`
(`decorators.py`: `effective_category = category or (f"{task}_{role}" if task and role else None)`),
so task/role queries and the category-driven form-spec both work from one declaration.

```python
@configurable(task="classification", role="model")
class VisionModel: ...      # → __confluid_category__ == "classification_model"
```

Tasks: `classification` / `segmentation` / `detection`. Roles: `model` / `loss` / `dataset` /
`metric` / `trainer` / `evaluator`, plus the task-agnostic `logger`. An unpopulated cell is simply
unregistered — add a class with that `task`/`role` and it appears.

**`role` is declared, never inferred:** model and loss are *both* `nn.Module`; a `register`-ed
builder **function** has no MRO; a framework-native runnable inherits no marker base. Type inference
is only a fallback for the unambiguous roles (`metric`←`torchmetrics.Metric`,
`optimizer`←`torch.optim.Optimizer`, `loader`←`DataLoader`, `logger`←`pl.Logger`); the tag wins.

**Task-agnostic categories** (bare `category=`): `source` (record producers — `HuggingFaceSource`,
`DatasetSplit`; also unioned into dataset slot pickers) · `engine` (`Stream`, `JointStream`) · `op`
(`Threshold`, `ConvertToImage`, `Pipeline`, `Parallel`, `Enable`) · `sink` (whole-Stream
`write(record)` writers — `HDF5Sink`, `YoloSink`) · `optimizer` / `loader` / `logger` (infra,
`register(..., lazy=True)`, **deliberately without a task category** so they never leak into task
slot pickers).

**`group` is palette nesting ONLY** — a path-like sub-folder *within* a category, presentation-only,
never gating what a consumer is offered. In use across the workspace: a dataset engine groups its ops `numpy` / `torch` / `image` /
`structure` / `compose` / `sink` / `debug`; a signal-domain package nests `spectrogram` /
`regions` / `detection` / `visualize` / `fourier/{numpy,torch}`.

## 3. Your signature is the contract

Both consumers introspect `cls.__init__` — or, for a `register`-ed builder function, the function's
own signature. Annotations, defaults and docstring are the whole contract.

**Prefer closed `Literal`s.** `colormap: Literal["viridis", "magma", "gray"] = "viridis"` → a combo
dropdown in the editor, a JSON-Schema `enum` in the MCP schema. Keep ONE source of truth: derive any
runtime tuple with `typing.get_args(...)`, never restate the values. Bare `str` only for open-ended
values.

**Range-mark numeric params (PEP 593).**
`noise_power_db: Annotated[float, Interval(ge=-200.0, le=50.0)] = -30.0` (`annotated_types`) →
JSON-Schema `minimum`/`maximum` plus the editor's widget bounds. Unmarked numerics get *full-range*
widgets — necessary, because a frontend defaulting to `[0, 2048]` makes every negative dB value
unenterable. A mark on a `(min, max)` tuple bounds both endpoints (`to_pydantic` relocates it
element-wise). Marks are UX/schema metadata: keep bounds generous (physical sanity, not policy) —
the constructor stays the validation authority.

**Docstrings are the tooltip / description source.** Document each param once in a Google-style
`Args:` block; both consumers read it via `confluid.parse_param_docs` (→ widget tooltip; → pydantic
`Field(description=...)`). An undocumented param gets no help — fix it upstream, not in the bridge.

**Type → widget**, in this order (the editor's bridge classifies STRUCTURALLY — via
`get_origin`/`get_args`/identity, never substring-matching `str(annotation)`):

| Annotation / condition | Rendered as |
|---|---|
| closed `Literal[...]` (incl. `Optional[...]`) | combo dropdown (first) |
| the dataset library's `Record` alias (== `Dict[str, Any]`; also `Optional`/`Iterator` of it) | `DATASET_RECORD` wire — alias **equality**, before the container branch |
| `datasets.Dataset` | `HF_DATASET` wire |
| `pathlib.Path`, or a name containing `path`/`dir`/`root`/`folder` | directory picker (the one deliberate name-based rule) |
| a `(min, max)` numeric tuple | a `__lo` / `__hi` widget pair |
| any other container (`List`/`Dict`/`Sequence`) | `STRING`, parsed back by the coercion step |
| a NESTED container in any union arm | a wired `DATASET_VALUE` socket |
| `int`/`float`/`str`/`bool` (incl. `Optional`/`Union` arms) | `INT`/`FLOAT`/`STRING`/`BOOLEAN` |
| anything else | `STRING` fallback |

Add a type by extending `TYPE_MAP` / `_map_type` — never by special-casing a node.

**Type → schema** (`to_pydantic`): a nested `@configurable` param `X` → `Union[X, <model for X>]`
(instance *or* config dict); opaque leaves and parameterized opaque generics (`torch.Tensor`,
`Dataset[Any]`) → `Any`; iterables → `Any`; `Literal` → `enum`; `Annotated[..., Field(...)]`
constraints preserved; `__init__`-body slots become optional fields (default `None`) via the AST
scan; the model is `extra="forbid"`.

## 4. The construction contract (zero-arg + lazy init)

Full text in [Class Design](class-design.md); the four rules discovery depends on:

1. `__init__` only *stores* values — no I/O, network, file reads, materialization, heavy compute.
2. **`Cls()` must succeed** ⇒ every param defaulted; a value required to *run* is validated
   **lazily** where it is needed (`HuggingFaceSource()` builds with no `path`; `.dataset` raises
   only when data is needed), never in `__init__`.
3. **Derived state → read-only `@property`, recomputed.** Cache into a private `_backing` field only
   for expensive external materialization whose inputs are stable by first use.
4. **Params stay introspectable** — in the signature, or as **annotated** `__init__`-body attributes
   (`to_pydantic` surfaces them as optional fields). A runtime-injected body slot (`params=`,
   `dataset=`) MUST hold a `PartialClass(...)` (`_partial_: true`), not a plain `_target_`.

This is *why* validation is on by default: fully-defaulted params validate cleanly, and
required-at-use values are checked by your code, not by pydantic at construction time.

## 5. Schema enforcement — three validation points

Every `@configurable` class's `__init__` is wrapped to validate kwargs against `to_pydantic(cls)`,
whose model is `extra="forbid"` — a wrong-shape config fails loudly instead of silently
mis-constructing.

| Point | Fires on | Policy field | Env var |
|---|---|---|---|
| `__init__` | every direct Python instantiation | `ValidationPolicy.init` | `CONFLUID_VALIDATE_INIT` |
| YAML materialization | `flow()` swapping mode around `target(**ctor)` | `ValidationPolicy.yaml` | `CONFLUID_VALIDATE_YAML` |
| MCP tool entry | the tool validating its typed config before spawning the run subprocess | `ValidationPolicy.tool` | `CONFLUID_VALIDATE_TOOL` |

Modes: **`strict`** (default, re-raises) · **`warn`** (logs, continues) · **`off`**. Set via
`confluid.set_policy(init=…, yaml=…, tool=…)` or the env vars; a deliberately-untyped constructor
opts out with `@configurable(validate=False)`.

## 6. Consumer: the MCP discovery service

It enumerates classes (`list_configurable_classes`), generates schemas (`get_node_pydantic_schema`,
`get_node_form_spec`, `config_to_yaml`), and composes/executes task configs — every execution tool
spawning the dataset runner (`run <config.yaml>`) as a subprocess. `sanitize_schema` downgrades each advertised tool schema to
the OpenAPI-3.0 subset that strict LLM function-calling APIs accept.

**Slot options are TYPE-compatible, with `task`/`role` ranked FIRST.** A config-valued slot becomes
a discriminated `Union[...]` built by ROLE (the type-kind), not locked to one task:

```python
preferred = set(registry.list_classes(task=task, role=role))
if role == "dataset":
    preferred |= set(registry.list_classes(category="source"))       # task-agnostic sources
type_compatible = set(registry.list_classes(role=role)) - preferred  # same role, ANY task
# options = sorted(preferred) + sorted(type_compatible)
```

So a classification trainer's `model` slot **prefers** classification models but also **accepts**
any other `role="model"` config — task/role are guidance, the role keeps it type-safe (a loss cannot
fill a model slot). Slots are located by param name (`model`, `loss_fn`,
`train_set`/`val_set`/`test_set`/`dataset`, `*_metrics`); infra slots (`optimizer` / `*_loader` /
`lightning`) get curated configs.

**Naming discipline:** `Download*` / `*To*` / `*Trainer` / `*Evaluator` are auto-wrappable as MCP
tools. There is ONE runner — `<runner> run <config.yaml>` — and what used to be a CLI verb is now
encoded in the config (the runnable's class + its `task:`). Adding a CLI subcommand? Sketch its MCP
tool in the same change; if the tool would be awkward, the CLI is too.

## 7. Consumer: the visual node editor

**Admission is a positive category allowlist** (currently
`{op, model, source, engine, sink, value}`) plus interface/role admissions: a no-arg `run(self)` →
Runnable; `role="dataset"` → Source; `role ∈ {model, loss, metric, logger}` or `lazy=True` → Object.
Everything else is excluded **by construction** — the raw-callable op wrappers (`FilterOp` /
`WrappedOp`, uncategorised: their ctor takes a raw Python callable nothing a widget supplies), task
`*_dataset`s (a trainer-config concept, not a canvas one), uncategorised helpers, untagged sinks,
`_`-private names. There is no negative special-casing to maintain.

| Signal | Node kind | Output socket(s) |
|---|---|---|
| no-arg `run(self)` | **Runnable** — itself the terminal `OUTPUT_NODE` (there is no separate Run node) | `DATASET_RUNNABLE` + `@output` sockets |
| `@configurable(category="op")` with a carrier-taking `__call__` | **Op** (dual-mode) | `(DATASET_RECORD, DATASET_OP)` |
| `__getitem__`/`__iter__` yielding records, name ends `Source`, or `role="dataset"` | **Source** | `DATASET_SOURCE` |
| `role ∈ {model, loss, metric, logger}`, `lazy=True`, or `category="sink"` | **Object member** | `(instance, class)` on `DATASET_OBJECT[:role]` |
| no-arg `__call__(self)` | **Value producer** | `@output` sockets |
| anything else | **Generic** | `STRING` (or `HF_DATASET`) |

Op classification is the **positive `category="op"` tag**, not an annotation sniff: the record
carrier is a plain `dict` (no marker class to identity-match) and a kernel-only `Transform` subclass
*inherits* its `__call__`, so a `vars(cls)` scan would misclassify it as generic; the signature is
resolved through the MRO. Engines (`Stream`/`JointStream`) and source-views (`DatasetSplit`) render
source-typed ctor params as **wired sockets** — `source` → one `DATASET_SOURCE`;
`streams`/`sources` → dynamic `source_N`; `ops`/`transforms` → dynamic `op_N`; `op`/`target` → one
`DATASET_OP`. Detection is by **param name**, never per class.

**Object sockets are ROLE-qualified and TASK-AGNOSTIC** — `DATASET_OBJECT:<role>` for
`role ∈ {model, loss, metric, sink}`, since the canvas engine connects sockets on string equality. A
classification model and a segmentation model both produce `DATASET_OBJECT:model` and dock into
*any* trainer's `model` slot; the role still guards *kind* (a loss is refused by a model slot).
Role-less infra (optimizer / loader / lightning / logger) stays bare `DATASET_OBJECT`. Output
side and slot side gate on the same role set so they always agree — a one-sided qualifier silently
refuses a valid wire. Task survives only as guidance (palette folder + MCP slot ranking); the
hand-written **Retag** node is the per-instance escape hatch.

**Record wires are NOT type-checked at connection time.** A `DATASET_RECORD` wire keeps one
socket string, so any op *can* be wired; the record is self-describing (a plain `dict` of typed
items each carrying its own metadata), so an op given an incompatible record raises naturally when
applied. An op's `handles` / `consumes` / `optional` / `produces` attributes are **declarative graph
metadata** — nothing validates them against behaviour, and the base `Transform` dispatches on the
kernel registry, not on `handles`. Declare them truthfully or not at all.

**Palette, label, key.** The palette path reuses the taxonomy, NOT the package: a role-bearing class
→ `Acme/<Role>[/<Task>]` (`Acme/Model/Classification`, `Acme/Logger`); an op/source/engine/sink
→ `Acme/<Category>[/<group>]` (`Acme/Op/numpy`). The label is an uppercased `[PKG]` distribution
tag + the verbatim callable name with `_`→space (never `str.title()`, which mangles
`HuggingFaceSource` → `Huggingfacesource`). The key
`Acme_<package>_<category>[_<group>]_<callable>` is globally unique, so two same-named classes in
different modules can't clobber each other. Widget coercion casts widget `STRING`s back to
constructor types (numeric strings → `int`/`float`, comma-lists → `List[str]`, empty → `None`), so
the canvas never feeds the constructor a value the schema (§5) would reject.

**Hand-written adapters are canvas *execution-model* bridges only** — Walk Dataset, Extract from
Record / Extract Metadata, Compose Record, Mix Records, Retag, Value, Math, Yaml Dump, Subgraph Op,
and the viewers. A missing op or visualizer is added to its home package, never here.

## 8. Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Class missing, **no error** | stale `*.dist-info` after an entry-point change | reinstall the editable; confirm the entry-point count |
| Op/source tagged but absent from the canvas | category outside the editor's allowlist, or no `@configurable` | tag `category ∈ {op, source, engine, sink, …}` upstream |
| Submodule class missing (package-root entry point) | not in `__all__` | add it to `__all__` |
| A bare library transform (albumentations, torchvision v2) has no node | it is not `@configurable` — libraries run AS-IS via the engine's op-family dispatch | drop it into an `ops:` list (`{_target_: albumentations.HorizontalFlip, p: 0.5}`); the engine's op-family dispatch is the consumer's extension point for a new library |
| Model/loss not offered in a trainer slot | wrong/missing `task`/`role` | tag `@configurable(task=…, role=…)` to match the slot (§6) |
| Canvas wire between two object nodes refused | role-qualified socket mismatch (correct) | wire compatible roles, or re-tag the value with the editor's retag facility |
| Op raises mid-run on an incompatible record | there is no connection-time type filter, by design (§7) | fix the pipeline order / key names |
| MCP tool rejects a config with `ValidationError` | `extra="forbid"` / type / enum mismatch (correct) | match the schema; `CONFLUID_VALIDATE_TOOL=warn` for debugging only |
| Numeric field renders as `STRING`, ctor chokes | bad annotation | annotate `int`/`float`/`Optional[int]`/`Literal[...]` |
| Fixed-choice param is free text | typed as `str` | type it `Literal[...]` |
| Numeric widget clamps to `[0, 2048]` | no `Interval` mark | annotate `Annotated[float, Interval(...)]` (§3) |
| Body slot becomes an OBJECT socket | un-annotated `self.x = …` | write `self.x: bool = …` |
| Runtime-injected body slot crashes on materialize | plain `_target_` instead of `_partial_: true` | hold a `PartialClass(...)` |
| Body slots vanish in a frozen/zipped deployment | `inspect.getsource` fails, the AST scan is empty | run `confluid-bake <package>`, or declare `@configurable(broadcast_attrs=[...])` |
| Discovery log shows a skipped module | optional dependency missing | expected; degrades at `debug` |

## 9. Worked examples

A per-record op — node, composable, YAML-wireable:

```python
@configurable(category="op", group="numpy")
class Threshold(Transform):
    """An array-bearing field → a boolean ``Mask`` item.

    Args:
        low_level: Lower bound; None disables the lower bound.
        output: Record key the boolean Mask item is written to.
    """

    handles = (NDArrayItem,)      # declarative type interface (§7)
    consumes = (NDArrayItem,)
    produces = (Mask,)

    def __init__(self, low_level: Optional[float] = None, output: str = "mask") -> None:
        super().__init__()
        self.low_level, self.output = low_level, output
```

A task-scoped model — ranked first in classification trainer `model` slots (still offered to any
other `role="model"` slot), canvas socket `DATASET_OBJECT:model`, and `lazy=True` so a config
wires it `_partial_: true` and the trainer injects the dataset-derived `num_classes` at flow time:

```python
@configurable(task="classification", role="model", lazy=True)   # → "classification_model"
class VisionModel(nn.Module):
    def __init__(self, model_name: str = "", num_classes: int = 0):
        """A timm-backed classifier.

        Args:
            model_name: Any timm architecture name.
            num_classes: Output classes (derived from the dataset at run time).
        """
    def solidify(self): ...        # builds + caches the backbone post-flow
```

A third-party class or builder function you don't own — `register` stamps the markers but adds **no**
validation wrap, and the target may be a plain **function** (`to_pydantic` introspects the callable's
own signature, so no wrapper class is needed):

```python
register(nn.CrossEntropyLoss, task="classification", role="loss")             # "classification_loss"
register(fasterrcnn_resnet50_fpn, task="detection", role="model", lazy=True)  # a builder FUNCTION
```
```toml
[project.entry-points."confluid.configurables"]
mypkg-classification = "mypkg.classification"   # import the module so register() runs
```

## 10. Quick reference — the decorator

```python
@configurable(
    name=None,             # override the registration name (default cls.__name__)
    category=None,         # bare category; PREFER task+role, which derives f"{task}_{role}"
    task=None,             # classification / segmentation / detection
    role=None,             # model / loss / dataset / metric / trainer / evaluator / logger
    framework=None,        # which engine API the class belongs to (torch / keras / sklearn / …)
    group=None,            # palette sub-folder ONLY (presentation, not a filter)
    lazy=False,            # value stays deferred (PartialClass / runtime-injection slot)
    validate=True,         # wrap __init__ to validate kwargs against to_pydantic(cls)
    random=False,          # non-deterministic → editors re-execute the node every run
    constant=False,        # pure value producer → exporters fold it into the static config
    eager=False,           # __init__ does real work; configure() warns on post-construction setattr
    broadcast=True,        # False = never receives bare/glob broadcasts
    capture=True,          # False = don't capture ctor kwargs (heavy disposable args)
    broadcast_attrs=None,  # declare body-slot names for frozen/zipped deployments
    strict_typing=False,   # render Union[int, str] as two typed sockets instead of one STRING
    display_name=None,     # human-readable UI label
)
# Third-party classes/functions: confluid.register(cls, task=…, role=…, lazy=…, category=…)
```

**MUST:** decorate · entry-point the module · reinstall after entry-point changes · zero-arg
constructable · in `__all__` if surfaced from a package root · public name.

**SHOULD:** prefer `task`+`role` over a hand-built `category` · `Literal` for fixed choice sets ·
`Annotated[..., Interval(...)]` for physical ranges · a Google-style `Args:` entry per param ·
`group` for palette tidiness · `Download*`/`*To*`/`*Trainer`/`*Evaluator` naming · annotate body
slots · declare `handles`/`consumes`/`produces` truthfully or not at all.
