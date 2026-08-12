# Serialization — `dump()` and the Round Trip

`dump()` writes a live object graph back to YAML; `load()` of that YAML
reconstructs an identical graph. This symmetry is a core contract: **a dumped
configuration is a complete, reloadable record of a run.**

```python
from confluid import dump, load

state = dump(trainer)          # YAML text
clone = load(state)            # an equivalent object graph, any process
```

## What a dump contains

`dump()` emits the **plain format** — reserved keys, no tags — so a dumped
document is ordinary YAML that `yaml.safe_load`, `yq` and an editor schema can
all read. That holds for every value it can emit, including a function-valued
param (`collate_fn: ${ref:recordstream.collate_records}`) and the informational
placeholder it falls back to for an opaque object.

For each `@configurable` (or registered) instance, `dump()` emits a
`_target_:` mapping whose kwargs are reconstructed **per parameter**:

1. the **live attribute of the same name**, when the instance still carries
   it — so post-construction changes (a `configure()` call, a broadcast) are
   what you get back;
2. otherwise the **captured constructor kwargs** — stamped at build time —
   which is what keeps a plain [eager class](eager-classes.md) (one that
   *transforms* its constructor args instead of storing them) reloadable.

Values follow the same rules recursively: a nested configurable dumps as its
own `_target_:` node, a shared instance reached twice dumps once and is
referenced (`${ref:...}`), a deferred slot still holding a partial marker dumps
as `_partial_: true` — deferred in, deferred out.

## What a dump deliberately omits

- **Derived state** — read-only properties and `_`-prefixed internals are
  never emitted; they are recomputed by the reconstructed object
  ([Class Design](class-design.md)).
- **Runtime-injected arguments** — the `params=` / positional inputs handed
  to `flow()` at build time are call arguments, not configuration; the slot
  they fed round-trips as its `_partial_` recipe.
- **A `None` that is the default** — omitted only when the parameter's
  default is also `None` (lossless); any other default dumps an explicit
  `param: null`, because omitting it would silently restore the default on
  reload.

## Two things to know

- **Class names are registry handles.** A dump names each class by its public
  registry key — the bare name while unique, the module-dotted key once a
  namesake is registered ([Discovery](discovery.md)). Either way the
  reloading process must have the class registered/importable; the trade-offs
  are recorded in [Architecture Decisions](architecture.md) §4.
- **Interpolation is burned in.** A `${DATA_ROOT}`-style placeholder is
  substituted at load time, and `dump()` emits the substituted value — so
  reloading the dump in a different environment reproduces *this* run rather
  than resolving afresh ([Architecture Decisions](architecture.md) §7). A
  value that must stay late-bound uses `${ref:}` to a plain key.

An eager class may opt out of kwargs capture (`capture=False`) when a
constructor argument is too heavy to hold; that deliberately relaxes the
round-trip for that class — the trade-off is documented in
[Eager Classes](eager-classes.md).

## Runnable example

[`examples/reproducible_experiment.py`](../examples/reproducible_experiment.py)
builds a two-stage pipeline, exercises its derived (property-held) state,
dumps it, and asserts the round trip: the dump carries only the configured
inputs — the derived state provably stays out of the YAML — and the
reconstructed pipeline rebuilds it to identical values. For the eager-class
side of the round trip (captured kwargs), see
[`examples/eager_classes.py`](../examples/eager_classes.py).
