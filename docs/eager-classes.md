# Eager Classes (Plain Constructors)

Confluid does **not** require the [lazy-init / zero-arg convention](class-design.md). A plain
Python class — required parameters, real work in `__init__`, params not stored verbatim — is fully
supported for loading, flowing, and dumping. This page explains what works out of the box, how the
dump round-trip is achieved, and where the lazy convention still buys you something.

> Not to be confused with the *tag-level* eager-vs-deferred distinction (`!class:Foo()` vs
> `!class:Foo` — see [Tags & Deferred Initialization](tags.md)). "Eager class" here means the
> **class design**: a constructor that does work from its params.

## Loading just works

The materialization engine passes every YAML kwarg that matches the constructor signature directly
to `__init__` — construction is a real `Cls(**kwargs)` call, not a build-then-setattr dance:

```python
from confluid import configurable, load

@configurable(eager=True)
class Resampler:
    def __init__(self, rate: int) -> None:          # required param — no default needed
        self._window = self._design_filter(rate)    # real work, param not stored verbatim
```

```yaml
resampler: !class:Resampler()
  rate: 48000
```

A missing required parameter fails with a clear, YAML-located error
(`Failed to construct Resampler at config.yaml:1:12: ... missing 1 required positional
argument: 'rate'`).

Only kwargs **not** in the signature are assigned post-construction (the body-slot mechanism —
attributes like `self.mode = "fast"` set in the `__init__` body remain freely configurable, which
is exactly normal Python behavior).

## Dump round-trip: captured constructor kwargs

`dump()` normally reconstructs a class's kwargs from same-named instance attributes. An eager class
that transforms a param (`self._window = design(rate)`) has no `rate` attribute — so confluid
**captures the bound constructor kwargs at construction time** and stamps them on the instance as
`__confluid_kwargs__`:

- on the YAML path, the engine stamps the resolved constructor kwargs after building the instance;
- on direct Python construction (`Resampler(48000)`), the `@configurable` validation wrap captures
  the explicitly-passed named arguments (positionals normalized to names, defaults excluded) —
  even when validation is set to `off`.

`dump()` then works per parameter: the **live same-named attribute wins** when it exists (so
post-construction changes to stored params survive the dump), and the **captured kwarg is the
fallback** for transformed params. `dump()` → `load()` reconstructs an equivalent object.

Related fidelity rule: an explicit `None` on a parameter whose default is *not* `None` dumps as
`param: null` (omitting it would silently reload the default). A `None` on a `None`-defaulted
parameter is still omitted — that omission is lossless.

### Known degradations

- **`@configurable(validate=False)` + direct Python construction**: no validation wrap means no
  capture; `dump()` falls back to the live-attribute heuristic (params stored verbatim still dump,
  transformed ones don't). YAML-loaded instances of such classes are unaffected — the engine stamp
  covers them.
- **`__slots__` / frozen instances**: the stamp is silently skipped (the instance rejects arbitrary
  attributes); slot-stored params still dump via the live attribute.
- **Body-slot attributes** (`__init__`-body assignments) holding an explicit `None` are omitted
  from dumps — body slots have no introspectable signature default to compare against.
- Captured kwargs are held **by reference** for the instance lifetime — the same lifetime a
  param-storing class would give them.

## The `eager=True` mark and the staleness warning

Declaring `@configurable(eager=True)` stamps the class as "my constructor does real work from its
params". Its runtime effect: when `configure()` sets a **constructor-param** attribute on such an
instance post-construction, confluid logs a warning —

```
configure(): setting constructor param 'rate' on eager class Resampler — __init__ work will NOT re-run; derived state may be stale
```

The value is still applied (warned, not blocked). Body attributes stay silent — they are freely
reconfigurable by design. The mark is optional and does **not** gate loading or dumping (kwargs are
captured universally); it exists to document the class's behavior and to surface the one genuine
footgun of eager designs.

## When the lazy convention still matters

The [lazy-init / zero-arg convention](class-design.md) remains the better design when your objects
are **reconfigured after construction** or **built incrementally by tools**:

- `configure()` / `configure_from_file()` — post-construction reconfiguration can only recompute
  derived state that lives behind a read-only `@property`; work done once in `__init__` goes stale.
- Cheap structural introspection — `resolve()` and `flow(solidify=False)` assume construction is
  side-effect-free, so a tool can build a config graph without paying for it.
- Interactive builders — a visual editor or discovery service that instantiates classes with
  partial (or zero) arguments to preview them needs every parameter defaulted.

If your usage is "load a YAML, get working objects, maybe dump them back" — plain eager classes are
the simpler choice, and confluid supports them first-class.
