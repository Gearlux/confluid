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
| `_scope_: {dim: value}` | conditional block (several dims are ANDed) | `!scope:KEY[=VAL]` |
| `_notscope_: {dim: value}` | negated conditional block | `!notscope:…` |

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
[Broadcasting](broadcasting.md#deferred-_partial_-slots-are-configured-not-skipped).

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

## Anchors and merge keys work on markers

Because the format is ordinary YAML, YAML's own reuse machinery applies to
markers too. `<<:` merges an anchored base into a node, and the result is a
marker just as if you had written the keys out:

```yaml
base: &base
  _target_: Trainer
  epochs: 10

fast:
  <<: *base
  epochs: 1          # the node's own key wins over the merged one

slow:
  <<: *base
  epochs: 100
```

`fast` and `slow` are independent `Trainer` markers. A merge of several anchors
(`<<: [*a, *b]`) and an anchor that is itself built from a merge key both work.

Note the difference from `_clone_`: a merge key is resolved by the YAML parser
*before* confluid sees the document, so it copies **keys**. `_clone_` is resolved
by confluid and copies a **node**, which is what you want when the template is
built elsewhere or you need the copy to track a reference.

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

`_scope_` takes a **mapping of dimension → value**, and every sibling key is the
body. The block's key is inert scaffolding — pick a descriptive label:

```yaml
default_model:
  _notscope_: {model: }          # boolean: no value — active while model is unset
  model: {_target_: MLP}

cnn_model:
  _scope_: {model: cnn}          # load(doc, scopes=["model=cnn"])
  model: {_target_: CNN}
```

The wrapper key **cannot** be the dimension name, because several blocks routinely
share one dimension (`lightning:` / `torch:` / `keras:` all on `framework`) and
YAML forbids duplicate keys in a mapping.

**Several dimensions are ANDed**, which needs no extra spelling:

```yaml
mlx_convnet:
  _scope_: {framework: mlx, model: convnet}   # fires only when BOTH are selected
  model: {_target_: MlxConvNet}
```

**A list body** is a list whose **first item is the marker**; the rest is the
body, spliced into the surrounding list. This is the only way to write a
conditional list *item* — a YAML node is a mapping or a sequence, never both, so
a mapping-bodied block cannot express it:

```yaml
ops:
  - always_first
  - - _scope_: {extra: enabled}    # the marker …
    - extra_a                      # … and the items it guards
    - extra_b
  - always_last
```

Mapping body splices its **keys**; list body splices its **items**. Both start
with `_scope_:` — only the container differs.

> **Quote a value YAML would read as a boolean.** `{extra: yes}` becomes `True`,
> which never matches the `extra=yes` an activation passes, so confluid rejects it
> and tells you to write `{extra: "yes"}`. Use `{debug: }` for a boolean
> *dimension* — one with no value at all.

## Malformed markers raise

A marker that does not make sense is a located `ConfigurationError` at load,
never a silently degraded value:

```yaml
x: {_target_: Box, _ref_: y}       # Conflicting reserved keys
x: {_partial_: true, lr: 1}        # _partial_ needs _target_ in the same mapping
x: {_ref_: proto, _partial_: true} # _partial_ only modifies _target_
x: {_target_: 42}                  # _target_ must be a non-empty string
x: {_target_: Box, _partial_: yes_please}   # _partial_ must be true or false
```

Each message carries the file, line and column of the offending mapping.

**`_partial_` pairs with `_target_` and nothing else.** It is a modifier on
*construction*, and `_target_` is the only key that constructs — so it has no
meaning beside `_ref_`, `_clone_` or a `_scope_` block, and those are refused
rather than ignored. To defer what a reference points at, put the modifier on
the node being constructed:

```yaml
proto: {_target_: SGD, _partial_: true, lr: 0.5}
opt: {_ref_: proto}                # `opt` is the deferred marker
```

## Migrating an existing config — `confluid-migrate`

The legacy tag spelling (`!class:` / `!lazy:` / `!ref:` / `!clone:` / `!scope:`)
still parses in 0.3 — emitting a `FutureWarning` once per document — and is
**removed in 0.4.0**. Convert before then; the tool proves each conversion
equivalent before writing. (A fully annotated reference config in the plain
spelling ships at the repository root as
[`confluid.example.yaml`](https://github.com/Gearlux/confluid/blob/main/confluid.example.yaml).)

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

One form needs a hand edit, and it is rare: the quoted-string marker spelling
(`"!class:Adam(lr=!ref:base)"`). It is a third grammar the codemod deliberately
reports rather than guesses at — it parses only `!class:` and `!ref:`, and since
0.3.0 every other marker written that way raises a `ConfigurationError` naming
the plain-YAML line to write, instead of reaching your config as literal text.
Everything else converts, including sequence- and scalar-bodied scope blocks —
measured over this workspace, 626 sites across 56 files with zero findings.

An `@axis=value` [target selector](discovery.md) needs no migration — it is part
of the target NAME, so it rides along unchanged and stays ordinary YAML:

```yaml
loss:
  _target_: Loss@framework=keras     # picks between classes sharing one name
  from_logits: false
```

The tool also makes every environment read explicit — `${DATA_ROOT}` and
`$DATA_ROOT` both become `${env:DATA_ROOT}`, and `${PORT:8080}` becomes
`${env:PORT,8080}`. That is meaning-preserving today (a plain-named `${...}` is
already an environment variable) and it is what lets a bare `${name}` later be
read as a config key without any config being ambiguous.

## Runnable example

[`examples/plain_format.py`](../examples/plain_format.py) loads one document in
both spellings and asserts they produce the same graph — including the
`yaml.safe_load` check that is the whole point of the format.
