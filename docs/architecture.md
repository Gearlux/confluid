# Confluid architecture

The *why* behind confluid's mechanisms. The user-facing documentation
([README](../README.md), the per-topic `docs/*.md`) shows **how to use** each surface; this
document records **why the surface is shaped the way it is** — so a reader who asks "why does
this behave like that?" finds the answer here instead of reverse-engineering it from git
history.

Maintenance rules:

- Every record keeps the five elements **Context → Decision → Consequences → Example → What you
  may change**, dated (see the workspace `AGENTS.md` → "Architecture Decisions Are Documented").
- A change that alters a mechanism updates its record **in the same change**.
- A superseded decision is **deleted**, not archived: whatever it still binds is folded into its
  successor record. History lives in git, not here.

This file is **backfilled lazily**: it starts with the mechanisms whose rationale has actually
been asked for, and a record is added whenever a "why does X behave like that?" question is
answered or the mechanism is touched. A mechanism with no record here is not undocumented —
its rules live in `AGENTS.md` and its usage in the topic guides; it simply has not yet needed
its reasoning written down.

---

## 1. An activation that matches nothing is an error, not the default

*2026-08-01*

**Context.** A keyed scope selects a variant: `--scope task=classification` splices the
`!scope:task=classification` block. Resolution was purely *positive* — `_is_active` compared
each block against the activation map, active blocks spliced, everything else dropped. A value
matching **no** block was therefore indistinguishable from passing no scope at all: the
document's unscoped keys applied and the load succeeded.

That silence is expensive in exactly the situation scopes exist for. A consuming CLI that maps
a subcommand or flag onto a scope value has two independent spellings of the same name — the
one the user types and the one the config declares — and nothing compares them. A typo, a
backend the config had no block for, a value renamed on one side only: each ran the *default*
configuration and reported success. The failure surfaces much later, as artifacts that look
right until someone checks which variant produced them.

The check cannot live in the consumer. A CLI knows what the user typed but would have to parse
the YAML to learn what the document offers — which is confluid's job, and confluid already
walks every block during resolution.

**Decision.** `resolve_scopes` opens with `_check_active_values_are_declared`. An active keyed
scope whose **dimension is declared** by positive blocks, with a **value none of them carries**,
raises `ScopeError` listing the values that do exist.

The rule is narrow, and three neighbouring cases stay silent by design:

| Situation | Outcome | Why |
|---|---|---|
| dimension not declared at all | inert no-op | lets a CLI pass a dimension unconditionally while a config grows into it |
| dimension not activated | the unscoped keys apply | that is what "default" means |
| dimension has any `!notscope:` block | every value accepted | a negation is activated by every value *except* the one it names, and deactivated by that one — both outcomes are meaningful, so nothing can be rejected |

That last row is not a concession; omitting it breaks the negation semantics outright. The
first implementation did omit it, and `task=classification` activating a
`!notscope:task=segmentation` block — the documented core of the unset-⇒-active convention —
started raising.

**Consequences.**

- **`discover_dimension_values` reports POSITIVE values only.** A dimension declared solely by
  negated blocks maps to an *empty set*: the key survives (it is a real dimension a CLI must
  bind) but there is nothing to *select*. Reporting the negated value would name the one value
  that does not select the block.
- **The walker is single-sourced.** `_walk_dimensions` returns
  `(positive values per key, keys carrying a negation)`; `discover_dimensions`,
  `discover_dimension_values` and the check all read it. This module has a history of walkers
  drifting apart — `discover_dimensions` once traversed `Fluid.kwargs` while `_resolve_value`
  did not, so a CLI advertised a dimension flag whose block resolution then ignored it.
- **Discovery reads the RAW document.** By the time `load()` returns, the blocks have been
  spliced away and there is nothing left to discover. Callers pass `load_config(...)` or
  `yaml.load(..., Loader=ConfluidLoader)`.
- **Breaking for a config relying on fall-through.** A "default" variant a consumer names
  unconditionally must now be *declared* — typically a block that restates what the unscoped
  keys already say. That cost is the point: the document states which variants it supports
  instead of leaving it to be inferred from what does not crash.

**Example.**

```python
from confluid import ScopeError, discover_dimension_values, load, load_config

raw = load_config("experiment.yaml")
discover_dimension_values(raw)          # {"task": {"classification", "segmentation"}}

load(raw, scopes=["task=classifcation"])
# ScopeError: No scope block matches task='classifcation'. This document declares task
# with: classification, segmentation. Either use one of those values, or add a
# `!scope:task=classifcation` block.
```

**What you may change.** Widen the *message*, not the rule. Each of the three exemptions
prevents a real regression, and narrowing any of them turns a working pattern into an error at
load time for every consumer at once. A new discovery view belongs on `_walk_dimensions`, never
in a fourth traversal.

---

## 2. `flow()` finishes the object, whichever way it arrived

*2026-08-02*

**Context.** Two promises about `flow()` were narrower in the code than in the docstring, and
both gaps had the same shape: they were invisible at the call site and surfaced somewhere else.

*Auto-solidification.* The documented contract is "domain code does not need to manually trigger
solidification — `flow(model)` handles it transparently". It held only on the marker path:
`flow()` opens with an idempotency check that returns any already-live object, and
`_maybe_solidify` ran after *construction*, below that return. So an object that reached the
slot already built — `!class:Model()` (eager, parens), or one handed in from Python — was
returned with its lazy state never finalized. Nothing raised. The consequence landed one step
later and read as an unrelated bug: an optimizer flowed with `params=model.parameters()` got an
empty parameter list, because the backbone `solidify()` would have built did not exist yet.
Consumers papered over it with `build = getattr(model, "solidify", None); build and build()` at
the call site — hand-rolling the engine's own hook because the engine declined to run it.

*Runtime injection.* A marker carries **kwargs** only, because a YAML tag can express nothing
else, and `flow(node, **runtime_kwargs)` matched that shape. But the constructor being deferred
is somebody else's, and a target may take its inputs **positionally** — a variadic
`DataLoaders(*loaders, path=…, device=…)` has no keyword for them at all. Such a slot simply
could not be deferred: `LazyClass(DataLoaders)` had no way to receive the loaders, so callers
constructed it inline and every knob beside the inputs (`device`) became unreachable from
config. The deferral mechanism failed on a signature shape, not on a semantic distinction.

**Decision.** `flow()` covers both.

1. The idempotency return calls `_maybe_solidify(obj)` before handing the object back, so the
   hook fires whichever way the object arrived. It still honours the `suppress_solidify` flag,
   so `flow(obj, solidify=False)` / `materialize(..., solidify=False)` leave a live object inert
   exactly as they leave a constructed one.
2. `flow(obj, *runtime_args, solidify=True, **runtime_kwargs)` threads positional args to the
   call: `target(*runtime_args, **merged_kwargs)`. They are runtime-only — never stored on a
   marker, never round-tripped by `dump()` — which is the same status the `params=` / `dataset=`
   kwargs already had.

**Consequences.**

- **`solidify()` must be idempotent.** It always could be called twice (two `flow()` calls on
  one marker), but a live object now makes that the *normal* path. The convention the lazy-init
  rules already require — build once, cache, return the cache — is what makes the re-fire free.
  A hook with side effects per call is now wrong in a way it was only latently wrong before.
- **`_ctor_params` drops `VAR_POSITIONAL` names.** `inspect.signature` lists `*loaders` under the
  name `loaders`, so a config key of that name passed the kwarg filter and reached the call as a
  keyword — where Python rejects it. The name can never be passed by keyword, so it is not a
  keyword slot.
- **Positional args suppress `Instance` memoization**, for the reason runtime kwargs already did:
  they override the stored spec, so the result is not the shared object the marker names.
- **One path cannot take them.** A registry-*configurable* bare type (`flow(MyClass, …)`)
  materializes through a synthesized marker so broadcasting applies, and a marker is kwargs-only.
  That raises `ConstructionError` naming the two ways out rather than silently dropping the args.
- **Live objects drop them**, matching the existing convention for runtime kwargs. This is what
  keeps `flow(slot, train, valid)` safe when a config wired a live object into that slot.

**Example.**

```python
class Loaders:                                  # somebody else's variadic signature
    def __init__(self, *loaders, device=None): ...

@configurable
class Model:
    def __init__(self, width: int = 8):
        self.width, self.backbone = width, None  # ctor does no functional work

    def solidify(self):                          # build once, cache, return the cache
        if self.backbone is None:
            self.backbone = build_backbone(self.width)
        return self.backbone

slot: Lazy[Loaders] = LazyClass(Loaders, device="cuda")   # config owns the knobs
flow(slot, train_dl, valid_dl)                            # the run owns the inputs

model = Model(width=16)                          # never went through a marker
flow(model)                                      # ...still built: parameters() is ready
```

**What you may change.** The positional channel is deliberately runtime-only — do not add a way
to *store* positional args on a marker. Round-tripping is built on `dump()` emitting keyword
kwargs a tag can carry; positional args have no names, so a stored one could not be dumped,
diffed, or overridden by key. If a target's positional inputs need to come from config, give the
target a keyword.
