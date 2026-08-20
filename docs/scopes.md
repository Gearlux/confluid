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
if_debug: !scope:debug
  log_level: DEBUG

# Keyed — flips on with `--scope task=classification` (or `--task classification`)
if_classification: !scope:task=classification
  model: !class:ClassifierModel

# Several dimensions at once — active only when BOTH are selected
if_keras_classification:
  _scope_: {task: classification, framework: keras}
  model: !class:KerasClassifier

# Negation. `_notscope_` is also active when the user passes no `--KEY ...`
# at all (the *unset ⇒ active* convention).
unless_debug: !notscope:debug
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
runnable: !class:Trainer
  model: !partial:TimmModel
    model_name: efficientvit_b0
  # Same slot, different value — `--model convnet` swaps it in.
  alt: !scope:model=convnet
    model: !partial:TorchConvNet
```

A marker's kwargs are a mapping like any other, so the same rule applies: the
active block's contents are spliced at the wrapper's position and later writes
win over earlier ones. Here `alt:` sits *after* `model:`, so an active
`model=convnet` overrides the default; put it before and the default would win.
An inactive wrapper is dropped entirely — the key never reaches the constructor.

Which slot a block replaces is decided by its **contents**, not by its key — the
wrapper key (`alt`, `if_debug`, …) is inert scaffolding.

**"Spliced at the wrapper's slot" is the include-paste rule**, key for key — the
same merge an included file gets (see [Interpolation & config files](interpolation.md)
→ "`include:` and document order"):

- a mapping the block writes at a slot holding a **marker** tunes that marker
  (the marker is kept; the mapping lands on its kwargs):

  ```yaml
  runnable: !class:Trainer
    model: !class:Model {name: a, depth: 3}
    alt: !scope:big
      model: {name: b}          # --scope big -> Model(name=b, depth=3), not {'name': 'b'}
  ```
- a **nested block** deep-merges: `if_x: !scope:x {Trainer: {lr: 0.9}}` over
  `Trainer: {lr: 0.1, epochs: 5}` yields `{lr: 0.9, epochs: 5}`;
- a **scalar** is replaced — last value wins;
- a key the block re-states moves to the **wrapper's position**, and a plain key
  written *after* the block lands at its own, later, position — so whether a later
  bare key wins never depends on whether the block is active;
- two active blocks that each carry an `include:` combine them into a list
  (`include:` accepts one) — both files are read, in document order.

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
  - !scope:extra=yes   # extends: two entries, not one nested list
    - extra_a
    - extra_b
  - !scope:verbose      # a one-item body: one conditional entry
    - trace_it
  - always_last
```

The tag sits on the list item and the nested list under it is the body. In the
reserved-key spelling the same thing is a LIST whose FIRST item is the `_scope_`
mapping (`- - _scope_: {extra: "yes"}`, then the body items) — the only way to
write a conditional list *item* there, because a YAML node is a mapping or a
sequence and never both. A one-item body is how a scalar body is spelled, and it
is type-coerced the same way: `- 42` splices the integer `42`.

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
# a `!scope:task=classifcation` block.
```

Without the check a typo resolved to the document's *unscoped* keys — the run
proceeded on the default and reported nothing, which reads exactly like success
until the outputs are inspected.

The rule is narrow, and three neighbouring cases stay silent on purpose:

| Situation | Outcome |
|-----------|---------|
| the dimension is **not declared** at all | inert no-op — a CLI may pass a dimension a config has not grown into yet |
| the dimension is **not activated** | its `default_scopes:` value if the document declares one, else the unscoped keys |
| the dimension carries **any** `_notscope_` block | every value is accepted — see below |
| a **bare** activation of a KEYED dimension (`--scope framework` with only `framework=…` blocks) | a `ScopeError` naming the declared values — a bare name can select nothing, and it would silently suppress the `default_scopes:` value on top |

That last row follows from what a negation means: `_notscope_: {task: segmentation}`
is activated by *every* value except `segmentation`, and deactivated by that one.
Both outcomes are meaningful, so no value can be rejected.

To ask a document which values it offers, use `discover_dimension_values` — the
same walk the check runs, useful for building a picker or validating a form:

```python
from confluid import discover_dimension_values, load

discover_dimension_values(load("experiment.yaml", until="raw"))
# {"task": {"classification", "segmentation"}, "model": {"convnet"}}
```

Note it takes the **raw** document (`load(path, until="raw")`), not a full `load()`
result — by the time pass 4 has run, the blocks have already been spliced away. A dimension
declared only by negated blocks maps to an empty set: it is a real dimension a
CLI must bind, but it offers nothing to *select*.

## Default scopes — the value a dimension takes when the caller names none

Without a default, "no `--framework`" means "the document's unscoped keys", so
the default backend has to *be* the top level — and still needs a block of its
own, or `--framework lightning` is an undeclared value. A top-level
`default_scopes:` list removes that asymmetry: every variant is a block, and the
document says which one a bare `load()` picks.

```yaml
default_scopes: [framework=lightning]      # used only when the caller names no framework=

model: shared                              # keys every backend shares stay at the top level
lightning: !scope:framework=lightning
  runnable: LightningX
keras: !scope:framework=keras
  runnable: KerasX
```

```python
load(doc)                                  # {"model": "shared", "runnable": "LightningX"}
load(doc, scopes=["framework=keras"])      # {"model": "shared", "runnable": "KerasX"}
```

The rules, each the same one an activation follows:

- **A caller value wins, per dimension.** `default_scopes: [framework=lightning,
  model=convnet]` with `scopes=["framework=keras"]` gives keras and convnet — the
  default fills only the dimension the caller left unset.
- **The value must be declared.** `default_scopes: [framework=kears]` raises the
  same `ScopeError` a typo'd `--framework kears` does, naming the values the
  document offers.
- **Keyed only.** `default_scopes: [smoke]` is refused: nothing the caller passes
  could switch a boolean default *off*, so it would be always-on, not a default.
  For a block that is active while a dimension is unset, write `!notscope:smoke`.
  An alias is refused for the same reason (aliases expand boolean names only).
- **Static.** It is read beside `scope_aliases:` before pass 4, so it is never
  a `${...}` value — see the [lifecycle](lifecycle.md) for why activation
  settles before interpolation. It is stripped from the loaded result.

`hydraide emit cfg.yaml` with no `--scope` therefore emits the defaulted variant.

To show the default beside the offered values — a CLI's `--help`, a picker —
read both from the RAW document:

```python
from confluid import default_scopes, discover_dimension_values, load

raw = load("experiment.yaml", until="raw")
discover_dimension_values(raw)   # {"framework": {"lightning", "keras"}}
default_scopes(raw)              # {"framework": "lightning"}
```

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
- **The key is load metadata.** `scope_aliases` and `default_scopes` — and a
  top-level `scopes:` key, reserved for documenting a config's available scopes —
  are stripped from the loaded result; they configure the load, not the program.

## Runnable example

[`examples/scopes.py`](../examples/scopes.py) loads one document under three
different scope activations and shows which blocks were spliced in.

For scope-driven **config groups** in a realistic setting (pick a model or
optimizer per run without editing files, Hydra-style), see
[`examples/ml_experiments/`](../examples/ml_experiments/README.md).
