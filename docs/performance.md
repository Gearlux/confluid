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
parse         2500 markers   best    141.3 ms   mean    144.2 ms      17687 markers/s
materialize   2500 markers   best    284.0 ms   mean    286.2 ms       8804 markers/s
resolve       2500 markers   best    281.6 ms   mean    282.9 ms       8879 markers/s
configure     2500 markers   best     38.0 ms   mean     54.8 ms      65710 markers/s
```

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
