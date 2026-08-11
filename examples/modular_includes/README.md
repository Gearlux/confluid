# Modular includes — composing a config from several files

The runnable companion to the [interpolation & config-files guide](../../docs/interpolation.md).
`experiment.yaml` pulls in `base.yaml`, overrides part of it, and the merged
document configures a `Model`.

```bash
python examples/modular_includes/run.py
```

| File | Role |
|---|---|
| `base.yaml` | shared defaults — a bare `dropout`, an addressed `Model:` block |
| `experiment.yaml` | `include: base.yaml` plus this run's overrides |
| `run.py` | loads the tree, builds the `Model`, asserts what won |

## What it shows

**Composition.** `include:` merges the included file first, then the including
file on top. `load_config` returns one dict; nothing downstream can tell it came
from two files.

**Order.** Confluid has one precedence rule — document order, last spec wins —
and the rule for includes follows from it directly:

> An `include:` behaves as if the included document were **pasted into your file
> at that line**. A key written on both sides survives once, at the later
> position, with the later value.

`dropout` is set three times here:

| Where | Spec | |
|---|---|---|
| `base.yaml` | `dropout: 0.9` | bare — broadcasts to any node accepting `dropout` |
| `base.yaml` | `Model: {dropout: 0.1}` | addressed at `Model` |
| `experiment.yaml` | `dropout: 0.5` | bare, written after its own `Model:` block |

The run prints `Model(layers=50, dropout=0.5)`: the includer's later bare key
beats the included file's *addressed* block, because there are no priority tiers —
only position. Move `dropout: 0.5` above the `Model:` block in `experiment.yaml`
and the block wins instead.

**Where you write `include:` matters too.** `fallbacks.yaml` is the same idea
inverted — it sets its own values and *then* includes `base.yaml`, so the paste
lands last and the shared file overrides everything above it:

```yaml
Model:
  layers: 99          # a fallback ...
dropout: 0.5          # ... and so is this
include: base.yaml    # ... because the paste lands here, after them
```

`run.py` asserts that this yields `Model(layers=3, dropout=0.1)` — base's values,
not the file's own. Put the directive at the top to get the usual "my file wins",
at the bottom to say "these are defaults, let the shared file decide".
