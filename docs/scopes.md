# Scopes

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

Three shapes, and the shape decides what "splice" means:

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

## Runnable example

[`examples/scopes.py`](../examples/scopes.py) loads one document under three
different scope activations and shows which blocks were spliced in.

For scope-driven **config groups** in a realistic setting (pick a model or
optimizer per run without editing files, Hydra-style), see
[`examples/ml_experiments/`](../examples/ml_experiments/README.md).
