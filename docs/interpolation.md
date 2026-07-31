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
