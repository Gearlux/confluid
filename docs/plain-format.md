# The Plain-YAML Format (`_target_`, `_partial_`, `_ref_`, …)

> New here? [The Lifecycle](lifecycle.md) maps the passes this page sits in.

Confluid reads two spellings of the same document. The **reserved-key format** on
this page is ordinary YAML — no custom tags — so `yaml.safe_load`, `yq`, editor
schemas and linters all read it:

```yaml
model:
  _target_: MLP
  hidden: 32
```

The [tag format](targets.md) (`!class:MLP`) is the original spelling and still
loads. Both produce the **same** markers, so everything downstream —
[broadcasting](broadcasting.md), [scopes](scopes.md),
[interpolation](interpolation.md), `dump()` — behaves identically whichever you
write. The two may be mixed in one file, which is what makes a file-by-file
migration safe.

> **Why a second format?** A tagged document is not YAML that anything else can
> read: `yaml.safe_load` on `!class:MLP` raises `ConstructorError: could not
> determine a constructor for the tag`. Tooling that never heard of confluid —
> a diff viewer, a schema-aware editor, a `yq` pipeline in CI — cannot process
> it. The reserved keys buy that back. The vocabulary deliberately mirrors the
> one the wider ecosystem already uses (`_target_` / `_partial_`), so a reader
> coming from another config system is not learning a private dialect.

## The keys

| Key | Means | Tag equivalent |
|---|---|---|
| `_target_: Name` | build this callable | `!class:Name()` |
| `_partial_: true` | **don't** build it — the receiver flows it later | `!lazy:Name` |
| `_ref_: path` | a shared reference to another node | `!ref:path` |
| `_clone_: path` | an independent deep copy | `!clone:path` |
| `_scope_: KEY[=VAL]` | conditional block | `!scope:KEY[=VAL]` |
| `_notscope_: KEY[=VAL]` | negated conditional block | `!notscope:…` |
| `_content_: …` | a scope block's sequence / scalar body | *(the tag's body)* |

Every **other** key in the mapping becomes the marker's kwargs. A mapping
carrying none of these keys is an ordinary dict and is left completely alone.

## Construction: one key, one boolean

There are exactly **two** construction modes, and nothing about the surrounding
document changes which one you get:

```yaml
# built during load()
model:
  _target_: MLP
  hidden: 32

# never auto-built; the receiver calls flow(slot, params=…) when it can
optimizer:
  _target_: torch.optim.SGD
  _partial_: true
  lr: 0.001
```

`_partial_: false` is the default, so writing it is optional.

**A `_partial_` slot is still configured.** Deferral withholds *construction*
only — broadcast keys, addressed blocks and `configure()` all reach a partial
marker and merge into its kwargs, exactly as they do for a built node. See
[Broadcasting](broadcasting.md#deferred-lazy-slots-are-configured-not-skipped).

**Broadcasting reaches a built node too.** Keys are merged in pass 7 and
constructors run in pass 8, so a `_target_` node receives every cascading key
*before* it is constructed:

```yaml
seed: 7                      # reaches both, with no parameter threading
trainer:
  _target_: Trainer
  model:
    _target_: MLP
```

## References: two spellings

`_ref_` points at another node in the same document and yields the **same**
object; `_clone_` yields an independent deep copy:

```yaml
proto: {_target_: Box, size: 3}

a: ${ref:proto}      # scalar shorthand — a is b (one shared instance)
b: ${ref:proto}
c: ${clone:proto}    # an independent copy

d:                   # the mapping form, which can override kwargs on the copy
  _clone_: proto
  size: 9
```

Use the scalar `${ref:…}` for the common case and the mapping form when you need
to carry kwargs. A reference resolves to an *object*, so it must be the **whole**
value — `"pre-${ref:x}-post"` raises rather than quietly stringifying it.

## Environment variables: `${env:…}`

```yaml
data_dir: ${env:DATA_ROOT}/datasets
port: ${env:PORT,8080}            # comma default
```

`${oc.env:NAME}` is accepted as an alias. An unset variable with no default
leaves the literal `${env:NAME}` in place, like every other unresolved
placeholder. Config-key interpolation (`${train.dataset}`) is unchanged — see
[Interpolation](interpolation.md).

## Scopes

The block's key is inert scaffolding, as with the tag form; `_scope_` decides
activation and every sibling key is the body:

```yaml
default_model:
  _notscope_: model              # active while no model=… is selected
  model: {_target_: MLP}

cnn_model:
  _scope_: model=cnn             # load(doc, scopes=["model=cnn"])
  model: {_target_: CNN}
```

A body that is a **sequence or a scalar** cannot be expressed as sibling keys, so
it moves under `_content_` — the only way to write a conditional list *item*:

```yaml
ops:
  - always_first
  - _scope_: extra=yes
    _content_: [extra_a, extra_b]   # extends the list
  - always_last
```

`_content_` and sibling keys are mutually exclusive.

## Malformed markers raise

A marker that does not make sense is a located `ConfigurationError` at load,
never a silently degraded value:

```yaml
x: {_target_: Box, _ref_: y}       # Conflicting reserved keys
x: {_partial_: true, lr: 1}        # needs a discriminator key
x: {_target_: 42}                  # _target_ must be a non-empty string
x: {_target_: Box, _partial_: yes_please}   # _partial_ must be true or false
```

Each message carries the file, line and column of the offending mapping.

## Migrating an existing config — `confluid-migrate`

```bash
confluid-migrate config/                  # rewrite every YAML under config/
confluid-migrate config/ --check          # exit 1 if anything would change; write nothing
confluid-migrate config/ --verify         # + prove the conversion is equivalent
confluid-migrate config/ --report out.csv # a row per converted site
```

It **edits lines rather than reparsing the document**, so everything it does not
convert stays byte-identical — comments, key order, spacing and quoting are
untouched. That matters because a config's comments are usually its
documentation, and a migration diff has to be reviewable.

`--verify` is the check worth running. It compares the *resolved marker trees* of
the old and new documents, **once per scope activation the document declares** —
a wrong conversion inside a variant block never shows up in a plain resolve,
because the block is dropped before any marker is built. A file whose conversion
is not provably equivalent is left untouched and reported.

Anything the grammar cannot convert is reported with its line, never guessed at:

```
config/odd.yaml:128: tag survived conversion — convert by hand — - !scope:verbose 42
```

Three forms need a hand edit, all rare: a `!scope:` block with a **sequence or
scalar body** (it needs `_content_`), the `@axis=` target selector (use a
fully-qualified `_target_`, or `_target_: pkg.${engine}.Loss` for a dynamic
choice), and the quoted-string marker spelling (`"!class:Adam(lr=!ref:base)"`).

The tool also makes every environment read explicit — `${DATA_ROOT}` and
`$DATA_ROOT` both become `${env:DATA_ROOT}`, and `${PORT:8080}` becomes
`${env:PORT,8080}`. That is meaning-preserving today (a plain-named `${...}` is
already an environment variable) and it is what lets a bare `${name}` later be
read as a config key without any config being ambiguous.

## Runnable example

[`examples/plain_format.py`](../examples/plain_format.py) loads one document in
both spellings and asserts they produce the same graph — including the
`yaml.safe_load` check that is the whole point of the format.
