# Configuration Reports

Both configuration paths can tell you exactly what a config document did:
which override keys **applied** (and to which objects, via which mechanism),
which **failed**, and which matched **nothing at all**. The answer is a
`ConfigurationReport` — an accumulator with three buckets:

| Bucket | Contents |
|---|---|
| `report.applied` | one record per attribute per object — the **final** (last-write-wins) assignment, with the receiver label (`"Trainer 'encoder'"`) and the origin that delivered it (`bare`, `block 'Trainer'`, `glob '**'`, `glob '*'`, `addressed`, `nested-class`) |
| `report.failed` | keys that could not (fully) apply — an unknown attribute inside an object's own named block (`unknown-attribute`), or a per-field validation failure (`validation`, with the error text) |
| `report.unused` | top-level document keys that matched **nothing** across the whole pass, in document order |

`report.summary()` renders the counts (`"12 applied, 1 failed, 2 unused"`).

## `configure()` returns the report

The post-construction path builds and returns one report spanning **all**
instances of the call:

```python
from confluid import configure

report = configure(model, trainer, config={"lr": 0.01, "Model": {"layers": 8}, "ghost": 1})

report.applied   # [AppliedKey(key='lr', target='Trainer', origin='bare'), ...]
report.failed    # []
report.unused    # ['ghost']  — matched neither instance
```

`configure_from_file()` returns the same report. A key consumed by only one
of the instances counts as used — *unused* means "no object anywhere wanted
this".

## `collect_report()` — the YAML materialization path

`load()` / `materialize()` / `flow()` return the constructed objects, so
their report is exposed by a context manager instead. It installs an ambient
report; everything inside the block — including any nested `configure()`,
which adopts and returns the same report — aggregates into it:

```python
from confluid import collect_report, configure, load

with collect_report() as report:
    model = load("config.yaml")               # broadcast tracking
    configure(model, config=overrides)        # same report, post-construction

print(report.summary())
```

On the engine path, a top-level key whose value is (or contains) a `!class:`
/ `!ref:` marker — or is a list — is a **definition** (the node tree being
built), not an override candidate, and is excluded from unused-tracking.
Glob blocks track per leaf: a partially consumed `'**': {lr: 1, nope: 2}`
reports `**.nope` unused while `**.lr` counts as applied.

Nesting is safe: an inner `collect_report()` reuses the outer block's report.

## Semantics worth knowing

* **`unused` is diagnostic, not an error.** A bare key legitimately matches
  only some nodes, and an override file may target objects configured in a
  later pass. That is why the aggregate unused summary logs at **DEBUG**
  (one line per pass, visible with `LOGGAIR_CONSOLE_LEVEL=DEBUG`) — the
  report object is the actionable surface.
* **A named block is "used" once it matches an object** — a typo *inside* a
  matched block surfaces under `failed` (with the existing warning), not
  under `unused`.
* **Validation failures**: in `warn` mode the value is still applied (the
  key appears under both `failed` and `applied`); in `strict` mode the
  failure is recorded and the exception propagates — inspect the report via
  an enclosing `collect_report()` block. Engine-side (constructor-time)
  validation failures are *not* recorded: strict mode already raises a
  located `ConstructionError`, and warn mode logs.
* **Reconfiguring an `eager=True` class's constructor param** records the
  assignment as applied with a staleness `note` (the `__init__` work does
  not re-run) — see [Eager Classes](eager-classes.md).
* **Zero cost when off.** Without an active `collect_report()` block the
  engine path skips every recording site (a single `None` check); the
  benchmark in [Performance](performance.md) watches this path.

The report is the structured counterpart of the per-key TRACE stream
described in [Broadcasting & Ordered Matching](broadcasting.md) — use TRACE
to watch matching live, the report to assert on the outcome.
