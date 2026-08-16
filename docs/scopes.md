# Scopes

> New here? [The Lifecycle](lifecycle.md) maps the passes this page sits in.

Conditional config blocks live at an arbitrary key whose mapping carries a
`_scope_` / `_notscope_` entry. The wrapper key is inert — pick a descriptive
label (`if_debug`, `if_classification`, …); on activation it disappears and the
block's other keys are spliced in at that slot.

The value is a **mapping of dimension → required value**, which is what lets one
block depend on several dimensions at once (all of them must match — it is an
AND). Use `{name: }` with no value for a boolean dimension.

```yaml
# Boolean — flips on with `--scope debug`
if_debug:
  _scope_: {debug: }
  log_level: DEBUG

# Keyed — flips on with `--scope task=classification` (or `--task classification`)
if_classification:
  _scope_: {task: classification}
  model: {_target_: ClassifierModel}

# Several dimensions at once — active only when BOTH are selected
if_keras_classification:
  _scope_: {task: classification, framework: keras}
  model: {_target_: KerasClassifier}

# Negation. `_notscope_` is also active when the user passes no `--KEY ...`
# at all (the *unset ⇒ active* convention).
unless_debug:
  _notscope_: {debug: }
  log_level: WARNING
```

> **The tag spelling** (`!scope:debug`, `!scope:task=classification`, the
> equivalent `!scope:task(classification)` call form, `!notscope:…`) produces the
> same markers and is the concise way to write a block — with one limit: a tag
> suffix is a string, so it carries ONE dimension. A block conditional on several
> dimensions is written with the `_scope_:` mapping.

## Where a scope block may live

Anywhere a mapping key does — the document root, a nested dict, a list item,
**and inside a marker's own block**. The last one is how you offer an
alternative for a single slot without lifting it out to the root:

```yaml
runnable:
  _target_: Trainer
  model:
    _target_: TimmModel
    _partial_: true
    model_name: efficientvit_b0
  # Same slot, different value — `--model convnet` swaps it in.
  alt:
    _scope_: {model: convnet}
    model:
      _target_: TorchConvNet
      _partial_: true
```

A marker's kwargs are a mapping like any other, so the same rule applies: the
active block's contents are spliced at the wrapper's position and later writes
win over earlier ones. Here `alt:` sits *after* `model:`, so an active
`model=convnet` overrides the default; put it before and the default would win.
An inactive wrapper is dropped entirely — the key never reaches the constructor.

Which slot a block replaces is decided by its **contents**, not by its key — the
wrapper key (`alt`, `if_debug`, …) is inert scaffolding.

## What a block's body may be

Three shapes plus the empty placeholder, and the shape decides what "splice" means:

| Body | In a mapping | In a list |
|------|--------------|-----------|
| **mapping** | its keys are spliced at the wrapper's slot | appended as one entry |
| **sequence** | *error* — no keys to splice | the list is **extended** with the entries |
| **scalar** | *error* — no keys to splice | substituted as one entry |
| *empty* | nothing (an inert placeholder) | nothing |

A sequence body is the only way to write a conditional list *item*, since a
mapping body cannot express "add these entries here":

```yaml
ops:
  - always_first
  - - _scope_: {extra: "yes"}   # extends: two entries, not one nested list
    - extra_a
    - extra_b
  - - _scope_: {verbose: }      # a one-item body: one conditional entry
    - trace_it
  - always_last
```

A LIST whose FIRST item is a `_scope_` mapping IS a scope block, and the
remaining items are its body — the only way to write a conditional list *item*,
because a YAML node is a mapping or a sequence and never both. A one-item body
is how the tag form's scalar body is spelled here, and it is type-coerced the
same way: `- 42` splices the integer `42`.

Quote a value YAML would read as a boolean. `{extra: yes}` becomes `True`, which
then never matches the `extra=yes` string an activation carries — confluid
rejects it with a quote-it message rather than silently never firing.

A sequence or scalar body at a *mapping* slot raises `ScopeError` naming the file
and line — the wrapper key cannot stand in for the keys the body doesn't have.
The check fires only when the block is **active**, matching the rule that an
inactive block is dropped without its contents being examined at all.

Resolve them by passing `scopes=` to `load()`:

```python
from confluid import load
trainer = load("experiment.yaml", scopes=["debug", "task=classification"])
```

A CLI framework can forward scope activations straight from command-line
flags (e.g. `--scope debug` / `--task classification`) into `load(scopes=...)`.

## Asking for a variant that does not exist

A keyed activation must name a value the document actually carries. Asking for
one it does not is a `ScopeError` that lists the real values:

```python
load("experiment.yaml", scopes=["task=classifcation"])   # note the typo
# ScopeError: No scope block matches task='classifcation'. This document declares
# task with: classification, segmentation. Either use one of those values, or add
# a `_scope_: {task: classifcation}` block.
```

Without the check a typo resolved to the document's *unscoped* keys — the run
proceeded on the default and reported nothing, which reads exactly like success
until the outputs are inspected.

The rule is narrow, and three neighbouring cases stay silent on purpose:

| Situation | Outcome |
|-----------|---------|
| the dimension is **not declared** at all | inert no-op — a CLI may pass a dimension a config has not grown into yet |
| the dimension is **not activated** | the document's unscoped keys apply (the default) |
| the dimension carries **any** `_notscope_` block | every value is accepted — see below |

That last row follows from what a negation means: `_notscope_: {task: segmentation}`
is activated by *every* value except `segmentation`, and deactivated by that one.
Both outcomes are meaningful, so no value can be rejected.

To ask a document which values it offers, use `discover_dimension_values` — the
same walk the check runs, useful for building a picker or validating a form:

```python
from confluid import discover_dimension_values, load_config

discover_dimension_values(load_config("experiment.yaml"))
# {"task": {"classification", "segmentation"}, "model": {"convnet"}}
```

Note it takes the **raw** document (`load_config`), not a `load()` result — by
the time `load()` returns, the blocks have already been spliced away. A dimension
declared only by negated blocks maps to an empty set: it is a real dimension a
CLI must bind, but it offers nothing to *select*.

## Scope aliases — one name for a bundle of scopes

A document may declare a top-level `scope_aliases:` mapping, so a caller can
activate several boolean scopes under one name:

```yaml
scope_aliases:
  ci: [quick, verbose]      # one alias -> a list of scope names
  smoke: ci                 # ... or another alias — chains expand recursively

quick_mode:
  _scope_: {quick: }
  max_epochs: 1
logging:
  _scope_: {verbose: }
  log_level: DEBUG
```

`load(doc, scopes=["ci"])` (or `scopes=["smoke"]`) activates both `quick` and
`verbose`. A circular alias chain raises `ScopeError` naming the cycle.

Three rules bound the mechanism:

- **Boolean scopes only.** A keyed activation (`task=classification`) never
  alias-expands — its value is meaningful exactly as spelled.
- **Hierarchy still applies afterwards.** An expanded name like `prod.gpu`
  activates its ancestors (`prod`) exactly as it would when passed directly.
- **The key is load metadata.** `scope_aliases` — and a top-level `scopes:`
  key, reserved for documenting a config's available scopes — are stripped
  from the loaded result; they configure the load, not the program.

## Runnable example

[`examples/scopes.py`](../examples/scopes.py) loads one document under three
different scope activations and shows which blocks were spliced in.

For scope-driven **config groups** in a realistic setting (pick a model or
optimizer per run without editing files, Hydra-style), see
[`examples/ml_experiments/`](../examples/ml_experiments/README.md).
