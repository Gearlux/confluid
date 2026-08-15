# `${...}` Interpolation & Config Files

> New here? [The Lifecycle](lifecycle.md) maps the passes this page sits in.

## `${...}` interpolation — env vars AND config keys

A `${...}` placeholder in a string value is substituted at load time. The name decides the source:

- **Plain name → environment variable** (the historical behaviour): `${HOME}`, `${PORT:8080}` (with an optional `:default`).
- **Dotted / bracketed name → another config key**, resolved against the config tree with the same path machinery `${ref:}` uses: `${train.dataset}`, `${items[0]}`, `${db.port:5432}`.

```yaml
train:
  dataset: RFUAV
  version: v3
# Mix env + config keys in one string:
data_dir: "${DATA_ROOT}/${train.dataset}/${train.version}/data"   # -> /store/RFUAV/v3/data
epochs:   "${train.epochs}"                                        # whole match keeps the native int type
```

A whole-string match (`"${train.epochs}"`) returns the value with its real type; an embedded match substitutes `str(value)` (scalars only). Local (sibling) keys win over global, mirroring `${ref:}`. On a miss the `:default` applies, else the literal `${...}` is left in place. Interpolation is a single pass, so a referenced key must already be a literal/scalar — for wiring a live object into another config slot, use `${ref:}` instead.

Because the dispatch is on the name shape, every pre-existing `${VAR}` keeps meaning an environment variable — only names containing a `.` or `[` hit the config tree.

> **When it runs:** interpolation is applied at **materialization** — `load()`,
> `materialize()`, or `resolve()`. The raw parse returned by `load_config` /
> `load_config_with_paths` still carries the literal `${...}` placeholders.

## Bare `$VAR` — environment variables only

After the `${...}` pass, bare `$IDENTIFIER` occurrences in string values expand as
**environment variables** (`os.getenv`), so `root: $DATA_ROOT/subdir` behaves exactly
like `root: ${DATA_ROOT}/subdir` on every entry path — the spelling no longer depends
on a front-end re-implementing `os.path.expandvars` before handing the file to the loader.

- **Env-only.** There is no dotted config-path form and no `:default` — those
  spellings remain `${...}`-exclusive.
- **Unset stays literal.** An unset variable leaves the `$name` text in place,
  mirroring `os.path.expandvars`. (An unresolved `${...}` literal is likewise
  untouched — the bare pattern cannot match a `${`.)
- **Marker strings are exempt.** A string starting with `!` (the legacy
  quoted-string spelling) keeps its `$` text for flow-time parsing — the
  `@axis=$key` document-selector grammar also spells `$` in tag targets.
- **Burn-in.** Like every interpolation, the substitution is a single load-time
  pass: a marker kwarg `"$DATA_ROOT/x"` carries the expanded value from then on
  (`dump()` emits it; a deferred slot flowed later sees it).

```yaml
root: $DATA_ROOT/RFUAV/v3        # -> /store/RFUAV/v3 (same as ${DATA_ROOT}/RFUAV/v3)
```

> **Marker kwargs interpolate too — and burn in.** A `${...}` written inside a
> `_target_` marker's mapping body is
> substituted in the same load-time pass as any plain key. The substituted
> value is what the marker then carries — `dump()` emits it, and a later
> `flow()` of a deferred (`_partial_`) slot sees it, even if the environment changed
> in between. A slot that must stay **late-bound** uses `${ref:}` to a plain key
> instead of `${...}`.

## `include:` and document order

**An `include:` behaves as if the included document were pasted into your file at
that line.** That is the whole rule. Confluid has one precedence rule — document
order, last spec wins — and the include obeys it like everything else.

```yaml
# base.yaml                    # main.yaml
lr: 0.1                        include: base.yaml
Stage:                         lr: 0.3
  lr: 0.2                      s: {_target_: Stage}
```

Read the composed document top to bottom:

```yaml
Stage:
  lr: 0.2         # from the paste
lr: 0.3           # your line, where you wrote it
s: {_target_: Stage}
```

`s` gets `lr = 0.3` — your bare key is the last spec. Note that base's `lr: 0.1`
is gone: **a key written on both sides survives once, at the later position, with
the later value.**

### The position of `include:` is meaningful

Move the directive to the bottom and the same two files mean the opposite thing:

```yaml
# main.yaml
lr: 0.3
s: {_target_: Stage}
include: base.yaml     # everything above is a FALLBACK; base overrides it
```

composes to

```yaml
s: {_target_: Stage}
lr: 0.1           # base's value now, at the paste's later position
Stage:
  lr: 0.2         # the last spec addressing Stage
```

so `s` gets `lr = 0.2`. This is the natural way to say *"here are my defaults,
let the shared file win"*, and an include in the middle splits the file cleanly:
keys above it are overridden by the paste, keys below it override the paste.

- **Between two includes, the later one wins** (`include: [first, second]`) — they
  paste in listed order at the same slot.
- **A nested block still deep-merges.** Splicing decides *where* a block lands,
  not whether blocks combine: `Trainer: {lr: 0.9}` over an included
  `Trainer: {lr: 0.1, epochs: 5}` yields `{lr: 0.9, epochs: 5}`.

> **Changed 2026-08-11.** Previously the directive was lifted out and the whole
> including file was merged over the result, so its position made no difference
> and the including file always won — and, because an overridden key kept the
> *included* file's position, an override written after the include could still
> lose to an addressed block inside it (the same document written flat gave the
> opposite answer). If you have a config that relied on the including file
> winning from a bottom-placed `include:`, move the directive to the top.

### `include:` works anywhere a mapping does

The directive is honoured in every position, not only at the top level — inside a
nested block, inside a list item, inside a marker's own kwargs, and inside a scope
block:

```yaml
# frag.yaml
size: 7
label: from-frag
```
```yaml
m:
  _target_: Widget
  include: frag.yaml     # -> Widget(size=7, label='from-frag')
```

Two spellings are refused rather than quietly dropped: `include: {path: a.yaml}`
(the value must be a path or a list of paths) and a non-string entry in the list
(`include: [a.yaml, 42]`). Both used to consume the directive and splice nothing.

### Conditional includes

Because a scope block splices its contents at its own slot, wrapping an include in
one gives you a conditional overlay. The wrapper key is inert, so name it whatever
reads best:

```yaml
lr: 0.1

post_include:
  _notscope_: { default: }     # active unless `default` is activated
  include: tuning.yaml         # its keys land here, overriding lr above
```

**An unactivated block never opens its file.** Scope resolution and include
splicing alternate until they settle, so a block that is dropped takes its
directive with it — a framework-specific overlay need not exist in a checkout that
never activates that framework:

```yaml
torch_only:
  _scope_: { framework: torch }
  include: torch_overrides.yaml    # not opened unless --scope framework=torch
```

The alternation repeats, so an included file may itself contain a scope block
carrying a further include. Two files that include *each other* from inside scope
blocks expose one another's directive on every pass; that is capped and reported
rather than hung on.

## Capturing the YAML include tree

`load_config_with_paths(path)` returns both the loaded dict AND the ordered
list of every YAML file that contributed to it — entrypoint first, then
each transitively `include:`-d file in load order, deduplicated. Useful for
CLI bootstraps and experiment trackers that log every config file as a
reproducible run artifact.

```python
from confluid import load_config_with_paths

data, paths = load_config_with_paths("experiment.yaml")
# data:  the same dict load_config would return
# paths: [PosixPath('.../experiment.yaml'), PosixPath('.../common.yaml'), ...]
```

Plain `load_config(path)` keeps its single-Dict return, so callers that
don't care about the tree are unaffected.

## Runnable example

[`examples/interpolation_includes.py`](../examples/interpolation_includes.py)
writes a small include tree to a temp directory, then demonstrates env-var and
config-key interpolation plus `load_config_with_paths`. The
[`examples/modular_includes/`](../examples/modular_includes/) directory holds a
standalone include-tree demo as well.
