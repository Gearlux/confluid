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

A merge key is resolved by the YAML parser *before* confluid sees the document,
so it copies **keys** — which is also the way to get two independent instances of
one recipe: write the marker again (`<<:` an anchored base into each).

## References: two spellings

`_ref_` points at another node in the same document and yields the **same**
object:

```yaml
proto: {_target_: Box, size: 3}

a: ${ref:proto}      # scalar shorthand — a is b (one shared instance)
b: {_ref_: proto}    # the mapping form, same thing
```

There is no "copy" marker. A second instance is a second marker — the `${clone:}`
escape hatch was removed in 0.3.0 with no users; see the CHANGELOG.

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
model._target_: Box                # a reserved key inside a DOTTED key
x: {_target_: 42}                  # _target_ must be a non-empty string
x: {_target_: Box, _partial_: yes_please}   # _partial_ must be true or false
```

Each message carries the file, line and column of the offending mapping.

**A duplicate key is refused.** The YAML spec restricts a mapping's keys to be
unique and lists non-unique keys among its loading failure points, but leaves the
processor's response open — PyYAML keeps the last value silently. Confluid raises
instead, because the discarded value is invisible:

```yaml
include: a.yaml
x: 1
include: b.yaml      # duplicate key 'include' at main.yaml:3:1 (first written at line 1)
```

`a.yaml` would never have been read, and the survivor would splice at line 1's
position (a collapsed duplicate keeps first-insertion order), so `x: 1` would
beat a value from `b.yaml` the author wrote *below* it. To pull in several files,
write one key with a list:

```yaml
x: 1
include: [a.yaml, b.yaml]      # both read, both override x
```

The same refusal applies to any repeated key — `lr: 0.1` … `lr: 0.5` in one
mapping is a differently-trained run, not a preference. A `<<:` merge key
overridden by a local key of the same name is *not* a duplicate; that is ordinary
override semantics.

**A reserved key cannot be written as part of a dotted key.** Reserved keys are
read while the document is parsed; dotted keys are expanded afterwards. So
`model._target_: Box` could never become a marker — it used to leave a literal
`_target_` key in the loaded config as ordinary data. Write it nested:

```yaml
model._target_: Box       # refused
model:                    # write this
  _target_: Box
```

Every *other* key works dotted, including one that merges into a marker written
above it — which is what made the old failure easy to miss:

```yaml
model:
  _target_: Box
model.size: 3             # fine — builds Box(size=3)
```

**`_partial_` pairs with `_target_` and nothing else.** It is a modifier on
*construction*, and `_target_` is the only key that constructs — so it has no
meaning beside `_ref_` or a `_scope_` block, and those are refused
rather than ignored. To defer what a reference points at, put the modifier on
the node being constructed:

```yaml
proto: {_target_: SGD, _partial_: true, lr: 0.5}
opt: {_ref_: proto}                # `opt` is the deferred marker
```

## From either spelling to this one — `hydraide`

There is nothing to migrate. Both spellings are first-class input; the tag form
is the concise way to *write* a config, and this reserved-key form is what the
[`hydraide`](hydraide.md) preprocessor *emits* after resolving includes, scopes,
dotted keys and broadcasting:

```bash
hydraide experiment.yaml --scope framework=torch -o resolved.yaml
hydraide resolved.yaml --check     # a committed artefact that drifts fails CI
```

A fully annotated reference config in this spelling ships at the repository root
as [`confluid.example.yaml`](https://github.com/Gearlux/confluid/blob/main/confluid.example.yaml).

## Runnable example

[`examples/plain_format.py`](../examples/plain_format.py) loads one document in
both spellings and asserts they produce the same graph — including the
`yaml.safe_load` check that is the whole point of the format.
