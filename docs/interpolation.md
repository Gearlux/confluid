# `${...}` Interpolation & Config Files

## `${...}` interpolation — env vars AND config keys

A `${...}` placeholder in a string value is substituted at load time. The name decides the source:

- **Plain name → environment variable** (the historical behaviour): `${HOME}`, `${PORT:8080}` (with an optional `:default`).
- **Dotted / bracketed name → another config key**, resolved against the config tree with the same path machinery `!ref:` uses: `${train.dataset}`, `${items[0]}`, `${db.port:5432}`.

```yaml
train:
  dataset: RFUAV
  version: v3
# Mix env + config keys in one string:
data_dir: "${DATA_ROOT}/${train.dataset}/${train.version}/data"   # -> /store/RFUAV/v3/data
epochs:   "${train.epochs}"                                        # whole match keeps the native int type
```

A whole-string match (`"${train.epochs}"`) returns the value with its real type; an embedded match substitutes `str(value)` (scalars only). Local (sibling) keys win over global, mirroring `!ref:`. On a miss the `:default` applies, else the literal `${...}` is left in place. Interpolation is a single pass, so a referenced key must already be a literal/scalar — for wiring a live object into another config slot, use `!ref:` instead.

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
- **Marker strings are exempt.** A string starting with `!` (`"!class:..."`,
  `"!lazy:..."`, `"!ref:..."`) keeps its `$` text for flow-time parsing — the
  `@axis=$key` document-selector grammar also spells `$` in tag targets.
- **Burn-in.** Like every interpolation, the substitution is a single load-time
  pass: a marker kwarg `"$DATA_ROOT/x"` carries the expanded value from then on
  (`dump()` emits it; a deferred slot flowed later sees it).

```yaml
root: $DATA_ROOT/RFUAV/v3        # -> /store/RFUAV/v3 (same as ${DATA_ROOT}/RFUAV/v3)
```

> **Marker kwargs interpolate too — and burn in.** A `${...}` written inside a
> `!class:` / `!lazy:` tag's mapping body (or its quoted-string form) is
> substituted in the same load-time pass as any plain key. The substituted
> value is what the marker then carries — `dump()` emits it, and a later
> `flow()` of a deferred `!lazy:` slot sees it, even if the environment changed
> in between. A slot that must stay **late-bound** uses `!ref:` to a plain key
> instead of `${...}`.

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
