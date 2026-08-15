# Performance Baseline

Runnable companion: [`examples/performance.py`](../examples/performance.py)

The scoped-broadcasting engine threads per-key scope tags through the config
tree with lightweight `dict`-subclass context views. Every marker pays two view
constructions per flow (one assembling its ordered kwargs, one splicing the
child context at its slot) and every plain mapping node pays one, so a very
large tree pays interpreter-level overhead that a bare-dict copy would not.
The overhead is small — but "small" is only verifiable against a baseline, so
this harness exists to make it visible across engine changes.

## What it measures

`examples/performance.py` generates a synthetic document of ~2,500 markers
(10 groups × 10 subgroups × 20 markers, every 4th carrying a nested child)
under root keys that exercise every scoping path: two bare broadcast keys
(tree-wide cascade), a `'**'` glob block, an addressed class block, and a
dotted glob key (dotted expansion + strict one-level routing).

Four phases are timed independently (best/mean of 3 runs, plus throughput):

| Phase | What it isolates |
|---|---|
| `parse` | PyYAML + tag construction only — the floor to subtract from the rest |
| `materialize` | The full engine pass: broadcasting, context splicing, construction |
| `resolve` | Broadcasting/reference resolution **without** constructing objects |
| `configure` | The post-construction mirror walking the live object graph |

Each phase re-parses the document because `flow()` memoizes `Target`
markers — re-using one parse would measure the memo hit, not the engine.

## Running it

```bash
python examples/performance.py
```

Typical output (Apple M-series, Python 3.12):

```
tree: 10x10 groups, 2000 top markers + 500 nested = 2500 markers
parse         2500 markers   best    142.5 ms   mean    144.8 ms      17545 markers/s
materialize   2500 markers   best    288.0 ms   mean    288.6 ms       8681 markers/s
resolve       2500 markers   best    287.2 ms   mean    288.5 ms       8703 markers/s
configure     2500 markers   best     12.7 ms   mean     12.7 ms     197413 markers/s
```

`configure` was `38.0 ms / 65710 markers/s` until 2026-08-15. It is 3x faster
because the walk no longer flows every marker-valued attribute into a temporary
object that it configures and then discards — the marker is tuned in place
instead. The construction that disappeared was never useful work: its result was
thrown away (see [Post-construction configuration](configure.md)).


## Profiling mode

Set `CONFLUID_BENCH_PROFILE=1` to additionally run one `materialize` pass
under `cProfile` and print the top 25 functions by cumulative time — the
engine's context-splicing functions should appear near the top, which is how
you confirm a profile run is watching the right code:

```bash
CONFLUID_BENCH_PROFILE=1 python examples/performance.py
```

## Print-only policy

The script prints timings and always exits 0 — **no timing assertions**. It
runs in CI like every other example, and shared runners are far too noisy for
threshold checks; regressions are caught by eyeballing the numbers (or a
profile) against a previous run on the same machine, not by the test suite.
