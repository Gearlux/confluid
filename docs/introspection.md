# Introspection: `cast`, `load(until="settled")` & `solidify=False`

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
from confluid import load

# (a) load(until="settled"): broadcast-resolved Fluid MARKERS, nothing instantiated. ${ref:} targets are shared
#     by identity (a fan-out is one object reached twice); an unresolved reference stays a Reference.
markers = load("config.yaml", until="settled")        # {key: Target / Partial / Reference marker, ...}

# (b) solidify=False: live-but-inert objects — constructed (cheap, per zero-arg / lazy-init) but the
#     expensive post-flow solidify() (e.g. building a model backbone) is suppressed for the subtree.
graph = load(data, solidify=False)   # also load(..., solidify=False) / flow(obj, solidify=False)
```

Both leave `PartialClass` (`_partial_: true`) slots deferred and default behaviour unchanged (`solidify=True`).

**`load(until="settled")` constructs NOTHING — and no reference can make it.** A structural dotted
reference (`${ref:cfg.lr}`) stays a `Reference` under `load(until="settled")`, exactly as a plain
whole-object `${ref:split}` does. An *attribute* reference (`${ref:split.train}` — reading a
property of the object built at `split`) is refused on this path exactly as it is under
`load()`; the spelling was removed (see [Targets](targets.md#ref--shared-instance)) because
resolving it meant *building* `split`, and this API promises introspection without
construction. (Before 2026-08-11 that construction ran here too, so "introspection without
cost" could walk a dataset — 3.9 s on a real config whose split scans 37 archives;
steady-state cost is now ~10 ms.)

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
`load(until="settled")` markers with `solidify=False` inert objects (using a class whose
`solidify()` is expensive), narrows a node with `cast`, and closes with a
`dump` → `load` round-trip.
