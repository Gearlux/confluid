# Scopes

> New here? [The Lifecycle](lifecycle.md) maps the passes this page sits in.

Conditional config blocks live at an arbitrary key whose value carries a
`!scope:` / `!notscope:` tag. The key is inert — pick a descriptive label
(`if_debug`, `if_classification`, …); on activation the wrapper disappears
and the block's contents are spliced in at that slot. Three activation
forms are supported, all equivalent at the IR level:

> **Two spellings.** This page is written in the tag form; the plain-YAML form
> uses a `_scope_:` key taking a MAPPING of dimension → value
> (`_scope_: {task: classification}`), which additionally lets one block depend on
> several dimensions at once. See [the plain-YAML format](plain-format.md#scopes).

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

## Where a scope block may live

Anywhere a mapping key does — the document root, a nested dict, a list item,
**and inside a `!class:` / `!lazy:` marker's own block**. The last one is how you
offer an alternative for a single slot without lifting it out to the root:

```yaml
runnable: !class:Trainer
  model: !lazy:TimmModel
    model_name: efficientvit_b0
  # Same slot, different value — `--model convnet` swaps it in.
  alt: !scope:model=convnet
    model: !lazy:TorchConvNet
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
  - !scope:extra=yes          # extends: two entries, not one nested list
    - extra_a
    - extra_b
  - !scope:verbose trace_it    # a scalar body: one conditional entry
  - always_last
```

Scalar bodies are type-coerced the way inline `!class:Foo(n=7)` kwargs are, so
`!scope:x=y 42` splices the integer `42`.

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
# a `!scope:task=classifcation` block.
```

Without the check a typo resolved to the document's *unscoped* keys — the run
proceeded on the default and reported nothing, which reads exactly like success
until the outputs are inspected.

The rule is narrow, and three neighbouring cases stay silent on purpose:

| Situation | Outcome |
|-----------|---------|
| the dimension is **not declared** at all | inert no-op — a CLI may pass a dimension a config has not grown into yet |
| the dimension is **not activated** | the document's unscoped keys apply (the default) |
| the dimension carries **any** `!notscope:` block | every value is accepted — see below |

That last row follows from what a negation means: `!notscope:task=segmentation`
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

quick_mode: !scope:quick
  max_epochs: 1
logging: !scope:verbose
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
