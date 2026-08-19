# Configuration Reports

Both configuration paths can tell you exactly what a config document did:
which override keys **applied** (and to which objects, via which mechanism),
which **failed**, and which matched **nothing at all**. The answer is a
`ConfigurationReport` — an accumulator with three buckets:

| Bucket | Contents |
|---|---|
| `report.applied` | one record per attribute per object — the **final** (last-write-wins) assignment, with the receiver label (`"Trainer 'encoder'"`) and the origin that delivered it (`bare`, `block 'Trainer'`, `glob '**'`, `glob '*'`, `addressed`, `nested-class`, `deferred slot` — `addressed` is also what a config key that NAMES an object records: each key the overlay hands the object, at `"Class 'name'"`; `deferred slot` is a bare key tuned into a deferred marker's kwargs on the `configure()` path) |
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

`load()` / `flow()` return the constructed objects, so
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

On the engine path, a top-level key whose value is (or contains) a `_target_`
/ `${ref:}` marker — or is a list — is a **definition** (the node tree being
built), not an override candidate, and is excluded from unused-tracking.
Glob blocks track per leaf: a partially consumed `'**': {lr: 1, nope: 2}`
reports `**.nope` unused while `**.lr` counts as applied.

Nesting is safe: an inner `collect_report()` reuses the outer block's report.

## `explain(key)` — why this key has this value

Confluid arbitrates by POSITION and nothing else, which makes "why is `lr` 0.9
and not 0.5?" a question about *where* each value was written, not which one is
more specific. `explain` answers it directly — the contest for one key on one
object, in document order, with the winner marked:

```python
from confluid import collect_report, load

with collect_report() as report:
    cfg = load("experiment.yaml")

print(report.explain("lr"))
```

```
lr on Trainer = 0.9
    #0   block 'Trainer'    0.5          beaten — earlier in the document
  ✓ #2   bare               0.9          applied
```

Pass `target=` to narrow to one receiver when several got the same key. Both
paths report identically — a `configure()` report explains the same contest the
`load()` report does, because both run the one scanner.

The candidates live on `AppliedKey.contest` (a tuple of `Candidate(origin,
value, pos)`, in document order, last entry wins), so a front-end can render
them instead of the text form. `value` is a bounded *string*, never the object:
a config value may be a dataset or a model, and holding one for the report's
lifetime would turn a diagnostic into a leak.

Three limits worth knowing:

* **A key that overrode nothing has no line.** A marker's own kwarg and a
  constructor default both produce a value nothing overrode — that is what
  having no override record means, and `explain` says so rather than implying
  the key is unset. An own kwarg still appears as a losing candidate when a
  later key beats it, which is the case worth explaining.
* **An uncontested key stores no candidates.** One source is not a contest: it
  says nothing `origin` does not already say, and rendering it costs a `repr()`
  per applied key — 80 % of what this ledger first added to a `configure()`
  pass. Such a key prints `via <origin> — nothing else competed for it`.
* **Some paths have no ordering to report** and land in the same place. The
  deferred-slot cascade and a direct `flow()` fill kwargs from a pool rather
  than scanning a view, so there is nothing to rank.

## Semantics worth knowing

* **`unused` is diagnostic, not an error.** A bare key legitimately matches
  only some nodes, and an override file may target objects configured in a
  later pass. That is why the aggregate unused summary logs at **DEBUG**
  (one line per pass, visible with `LOGGAIR_CONSOLE_LEVEL=DEBUG`) — the
  report object is the actionable surface.
* **An undeclared key is reported on BOTH paths.** A key naming nothing the target
  declares — not a constructor parameter, not a settable class attribute, not an
  `__init__`-body slot — warns and appears under `failed` as `"unknown-attribute"`,
  whether it arrived through `load()` or `configure()`.

  On the load path the own-kwarg form (`{_target_: Node, pathh: /x}`) **still
  applies** the value — that branch is the post-init attribute mechanism, so the
  warning makes it audible rather than removing it. A class that wants the same
  key REFUSED instead opts in with `@configurable(strict_attrs=True)` — see
  [Closing the Config Surface](strict-attrs.md). Three things are never
  reported: a `**kwargs` class (no accept-list — it accepts everything by design),
  a **bare** key (it legitimately matches nothing, so it is `unused`, not
  `failed`), and a declared body slot.
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

## Runnable example

[`examples/report.py`](../examples/report.py) applies a document with applied,
failed, and unused keys to a small object graph, then prints the report both
paths return — `configure()`'s return value and a `collect_report()` block
around a `load()`.
