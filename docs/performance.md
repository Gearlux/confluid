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
parse         2500 markers   best    100.4 ms   mean    101.1 ms      24894 markers/s
materialize   2500 markers   best    273.6 ms   mean    290.8 ms       9138 markers/s
resolve       2500 markers   best    245.3 ms   mean    248.2 ms      10191 markers/s
configure     2500 markers   best    188.4 ms   mean    190.5 ms      13268 markers/s
```

Recorded 2026-08-17, after the runtime started consuming the settled document
([Architecture Decisions](architecture.md) record 19, phase 3). Two things moved
and both are the SAME cause: the benchmark's 2,500 markers sit under plain
mappings (`groups.g0.s0.m0`), and construction used to descend one level only,
so `materialize` built **none** of them and `configure` then walked a tree of
unbuilt markers (`12.7 ms`). Now every marker is built (`instantiate` recurses
through plain containers), so `materialize` includes 2,500 constructions at the
same wall time as before — the second broadcast into each marker's own kwargs at
construction is gone, which is what paid for it — and `configure` walks 2,500
live objects (`162 ms`) — the number it always claimed to measure.

Since `configure()` runs through the document (record 19, phase 4) it costs
`188 ms` on the same tree: the objects become a marker document, the FULL
resolution pass runs over it, and the settled values are written back — the
price of not having a second implementation of the rule. `materialize` /
`resolve` are unchanged (the slot key is threaded through the pass rather than
searched for, which is what kept the ordering fixes free).


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
