# ML Experiment Suite — a real-world confluid scenario

A research team runs many training experiments against one shared configuration:

- **one base config** ([`base.yaml`](base.yaml)) holds the object wiring and every default,
- **config groups** let a run pick the model/optimizer *without editing any file*,
- **experiment overlays** ([`experiment_quick.yaml`](experiment_quick.yaml),
  [`experiment_full.yaml`](experiment_full.yaml)) layer named variations on top,
- **one dump** archives exactly what ran, and rebuilds it later.

Run it (pure Python, no ML dependencies — the components are stubs; the configuration story is real):

```bash
python examples/ml_experiments/run.py
```

## If you know Hydra

| Hydra concept | confluid feature |
|---|---|
| Config groups (`db: mysql`) | `!scope:model=cnn` dimension blocks; `!notscope:model` holds the default (active while the dimension is unset) |
| Group selection (`db=mysql` on the CLI) | `load(path, scopes=["model=cnn"])` |
| Defaults list / experiment overlays (`+experiment=full`) | `include: base.yaml` + overriding keys (document order, last write wins) |
| `_target_:` / `instantiate()` | `!class:Name()` — the trailing `()` builds eagerly at load |
| `_partial_:` | `!lazy:Name` — deferred until domain code flows it with runtime args |
| Interpolation (`${db.port}`) | `${dotted.key}` / `${ENV_VAR}` strings, and `!ref:key` for live objects |
| `--cfg job` (show the composed config) | `dump(obj)` — and the dump reloads into the identical object graph |

## The pieces

**Global knobs are bare top-level keys.** `seed`, `device`, and `verbose` in
`base.yaml` broadcast tree-wide: every component whose constructor accepts the
name receives it — the dataset, the model, and the trainer all get `seed: 7`
with zero parameter-threading code.

**Config groups are scope blocks.**

```yaml
model_default: !notscope:model      # active while no model=... dimension is set
  model: !class:MLP()
    hidden: 32
model_cnn: !scope:model=cnn
  model: !class:CNN()
    channels: 8
```

An active block splices its body into the document (the wrapper key
disappears), so exactly one `model:` key survives per run:

```python
load("base.yaml")                          # -> MLP (the default)
load("base.yaml", scopes=["model=cnn"])    # -> CNN
```

A CLI framework forwards `--scope model=cnn` (or dimension flags) straight
into `scopes=`.

**The optimizer is deferred.** It needs the model's parameters — which only
exist at run time — so the YAML declares it `!lazy:` and the trainer builds it
inside `fit()`:

```python
opt = flow(self.optimizer, params=self.model.params)
```

**Experiments are include overlays.** `experiment_full.yaml` is four lines:

```yaml
include: base.yaml
base_lr: 0.001
trainer.max_epochs: 25    # addressed key: exact — reaches the trainer only
MLP:
  hidden: 128             # class-name block: every MLP instance
```

**Reproducibility is a round trip.** `dump(trainer)` emits the fully-resolved
wiring — selected groups, applied overlays and broadcasts, the still-deferred
`!lazy:` optimizer — and `load()` of that snapshot rebuilds the identical
experiment.

## Where to read more

Each mechanism has a focused guide with its own runnable example:
[tags](../../docs/tags.md) · [broadcasting](../../docs/broadcasting.md) ·
[scopes](../../docs/scopes.md) · [interpolation & includes](../../docs/interpolation.md).
