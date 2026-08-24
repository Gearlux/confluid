# The lifecycle: how a document becomes objects

Every other guide describes one hop. This page is the map: the order the passes
run in, what each one consumes, and what it decides **permanently**. Most
"why did my value do that?" questions are answered by the order alone.

```
   ┌─ load(path | text | data, until=..., scopes=[...]) ─────────────────────┐
   │                                                                          │
   │  1  PARSE        yaml.load(ConfluidLoader)   tags -> Fluid markers       │
   │  2  IMPORT       import:                     modules imported            │
   │  3  INCLUDE      include:                    files merged (deep_merge)   │
   │                                                                          │
   │     ── until="raw" stops here: the raw document, scope blocks intact ──  │
   │                                                                          │
   │  4  SCOPE        _scope_ / _notscope_        blocks spliced or dropped   │
   │  5  EXPAND       a.b.c: v                    nested at the written slot  │
   │  6  INTERPOLATE  ${...} and $VAR             substituted — BURNS IN      │
   │                                                                          │
   │     ── until="document" stops here: the Fluid IR ──                      │
   │                                                                          │
   │  7  BROADCAST    document order, last wins   keys merged into kwargs     │
   │                  ${ref:} resolution          shared by identity          │
   │                                                                          │
   │     ── until="settled" stops here: markers, no objects ──                │
   │                                                                          │
   │  8  FLOW         target(**kwargs)            live objects; validation    │
   │  9  SOLIDIFY     obj.solidify()              post-order finalize         │
   │                                                                          │
   │     ── until="objects" (the default): live objects ──                    │
   └──────────────────────────────────────────────────────────────────────────┘

   configure(obj, config=...) re-enters at 5 → 6 → 7 → 9 over LIVE objects.
```

## The passes

| # | Pass | Consumes | Decides — permanently | Leaves alone |
|---|---|---|---|---|
| 1 | **Parse** | the YAML text | `_target_` / `_partial_` / `_ref_` / `_scope_` mappings become typed markers, each stamped with its source location | everything else stays raw |
| 2 | **Import** | `import: pkg.mod` — honoured in every mapping position, like `include:` | the module is imported, so its `@configurable` classes are registered | a failed import warns (whatever the failure — a missing module, a syntax error), it does not raise |
| 3 | **Include** | `include: other.yaml` | files merge into ONE document; the included document is **pasted at the `include:` line** | nothing is resolved yet |
| 4 | **Scope** | `scopes=[...]` from the caller, filled in per dimension from the document's static `default_scopes:` | active blocks splice their contents at the wrapper's slot, inactive ones vanish | the activation map itself — nothing downstream can see it |
| 5 | **Expand** | `trainer.lr: 0.1` | dotted keys nest, anchored where the dotted spelling was written; kwargs written on a reference are folded into the referent marker's own kwargs; a dotted write through an unresolved `${...}` string is refused (`${ref:...}` values are already markers by now — they hoist just before this pass), as is a dotted write INTO a list (`a.0: 99` — the read grammar indexes a list, a write cannot) and a key with an empty segment (`a..b`) | `'*'` / `'**'` — ordinary path segments here |
| 6 | **Interpolate** | `${env:VAR}`, `${a.b}`, `$VAR` | the substituted text **burns in**, marker kwargs included — and a `${a.b}` reads the EXPANDED tree, the same answer the returned document gives; a `$$` is left as written (it is the literal-`$` escape, collapsed in pass 8) | `${ref:}` targets — pass 7 settles those, so an override merged into the document before then is what they see |
| 7 | **Broadcast** | the whole document as a flat view | which value each node's kwargs end up with (document order, last spec wins); every `${ref:}` is settled — a marker is shared by identity, a plain value is inlined; an unresolvable reference OR an unresolvable `_target_:` class is a located error | `PartialClass` markers — settled but not built |
| 8 | **Instantiate** | the settled tree — what `hydraide` emits | every marker at any depth is constructed from its settled kwargs (no second look at the document), kwargs validated under `policy.yaml`, ctor kwargs captured for `dump()`; every `$$` in a key or value becomes `$` here, once (`configure()` does the same when it applies the settled document) | `PartialClass` markers — construction is the one thing deferral withholds |
| 9 | **Solidify** | the built graph | `solidify()` fires post-order — children final, then the parent | objects flowed with `solidify=False` |

## Where you can stop

`load` is the ONE door. It takes a path, YAML text or already-parsed data —
parsed data is copied structurally on entry, so the caller's tree is never
mutated (load a raw document twice under different activations and each call
answers for itself) — runs
the passes the input still needs, and stops where `until=` says (a closed
Literal — a typo raises rather than defaulting). Passes already applied to the
data are idempotent, so `load(load(x, until="document"))` is `load(x)`.

| Call | Runs | Gives you | Use when |
|---|---|---|---|
| `load(x, until="raw")` | 1–3 | the raw merged document, scope blocks intact, markers unresolved | scope discovery (`discover_dimensions` needs the blocks), or "the document, not the objects" |
| `load(x, until="document")` | 1–6 | the Fluid IR, scopes applied | you want to inspect or re-merge before anything is built (a CLI merges its overrides here) |
| `load(x, until="settled")` | 1–7 | markers with their final kwargs, no objects | structural introspection (a YAML→graph import) |
| `hydraide.emit(x)` | 1–7 | the same, written as ONE plain-YAML file | "what did my config resolve to?" — see [hydraide](hydraide.md) |
| `load(x)` (`until="objects"`) | 1–9 | live objects | the normal path |
| `load(x, solidify=False)` | 1–8 | live but unfinalized objects | you want objects without paying for the expensive finalize |
| `load(x, …, return_paths=True)` | as above | `(result, [every file read])` | logging the include tree as a run artifact |
| `configure(obj, config=…)` | 5–7, 9 | the same document applied to objects that already exist | post-construction configuration |

## What the order answers

**"Why does `${train.lr}` agree with the tree `load()` returns?"** — Expansion (5)
runs *before* interpolation (6), so a dotted spelling and its nested twin have
already merged (document order, last wins) when the placeholder reads the key.

**"Why is my `${...}` frozen?"** — Interpolation (6) runs *before* anything is
built (8), and it is a single pass. The value it produced is what the marker
carries from then on; `dump()` emits it and a deferred slot flowed an hour later
still sees it. For a value that must follow a LATER override, use `${ref:}` — it is
settled in pass 7, so an override merged into the document before pass 7 (what a
CLI does between `load(until="document")` and `load(document)`) is the value it takes. After
pass 7 nothing is late-bound: a reference to a plain value is the value, a
reference to a marker is that marker, and one that resolves to nothing is an error
naming its line.

**"Why can't a `_scope_` block choose a `${...}` value?"** — Scopes (4) resolve
before interpolation (6), and the order is forced: a `${a.b}` may read a key that
only an active block provides, so which blocks are active must be settled first.
Activation therefore comes from the caller's `scopes=[...]` — expanded through the
document's static `scope_aliases:`, and filled in per dimension from its static
`default_scopes:` — never from a value the document computes. To
pick a *class* by a document key, use the `@axis=$key` target selector instead; it
is resolved at construction (8), after interpolation.

**"Why did the included file win / lose?"** — Include merging (3) fixes the key
order, and pass 7 reads that order as precedence. The included document is pasted
at the `include:` line, so the directive's position decides: lines below it
override the paste, lines above it are overridden by it. See
[Interpolation & config files](interpolation.md) → "`include:` and document order".

**"Why is my `_partial_` slot still a marker?"** — Deferral withholds construction
(8) only. Pass 7 still merges broadcast keys into it, which is why `lr: 0.001`
tunes a deferred optimizer; you call `flow(slot, params=…)` when the runtime
argument exists. See [Tags & deferred initialization](targets.md).

**"Why did `solidify()` see the old value?"** — It shouldn't: 9 runs after 7 and
8, post-order, on both the load path and `configure()`. If derived state looks
stale, the hook is probably not idempotent — it must be build-once-and-cache,
because a live object can be flowed more than once.

**"Why didn't my key reach anything?"** — Passes 7 and 8 gate on the receiving
class's accept-list. `collect_report()` (or `configure()`'s return value) reports
every key that matched nothing; see [Configuration reports](report.md).

## What each pass does NOT carry forward

- The **scope activation map** is a local of `load()`. Nothing after pass 4 can
  ask which scopes were active — a scope block that declares its own dimension as
  a plain key (`framework: keras`) is how a later pass sees the choice.
- The **include tree** is flattened by pass 3. Use `load(x, return_paths=True)` if
  you need the list of files that contributed.
- **`_yaml_loc`** (the source location stamped in pass 1) is carried on markers for
  error messages only. It is never a precedence input.

## Runnable example

[`examples/lifecycle.py`](../examples/lifecycle.py) walks one document through
every stage and prints what changed at each — the merged document, the scoped
document, the burned-in interpolation, the broadcast markers, and finally the
live objects.
