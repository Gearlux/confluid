# Introspection: `cast`, `resolve()` & `solidify=False`

## Typed materialization for static checkers (`cast`)

`flow(node)` returns `Any` — fine at runtime, opaque to mypy and your IDE.
`cast(node, Cls)` is `flow` with a type assertion: it materializes the node
(a no-op if it is already live) **and** tells the type checker the result is
`Cls`, so attribute access is checked and completed downstream:

```python
from confluid import cast

model = cast(config["model"], Model)   # flows if deferred; typed as Model
model.layers                            # <- type-checked, autocompleted
```

Use it at the boundary where a config-loaded object enters typed domain code.

## Introspecting a config without paying for it

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

## Dump and reconstruct

The counterpart of introspection is full-fidelity **round-tripping** — export a
live object graph and rebuild it identically in another process:

```python
from confluid import dump, load

state_yaml = dump(trainer)     # export current state
new_trainer = load(state_yaml) # recreate the exact same hierarchy
```

## Runnable example

[`examples/introspection.py`](../examples/introspection.py) contrasts
`resolve()` markers with `solidify=False` inert objects (using a class whose
`solidify()` is expensive), narrows a node with `cast`, and closes with a
`dump` → `load` round-trip.
