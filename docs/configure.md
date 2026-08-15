# Post-Construction Configuration

> New here? [The Lifecycle](lifecycle.md) maps the passes this page sits in.

`configure()` applies a config document to objects that **already exist** — in
place, no re-instantiation. It is the second half of confluid's core promise:
`load()` builds an object graph *from* a document; `configure()` brings a live
graph *up to* one. Both apply the same single matching rule, so a document
means the same thing whichever way it is applied.

```python
from confluid import configurable, configure

@configurable
class Trainer:
    def __init__(self, lr: float = 0.001, layers: int = 3):
        self.lr = lr
        self.layers = layers

trainer = Trainer()                                   # built with defaults
report = configure(trainer, config={"lr": 0.01})      # reconfigured in place
assert trainer.lr == 0.01
```

## The call surface

```python
configure(*instances, config=..., context=None) -> ConfigurationReport
configure_from_file(*instances, path=..., context=None) -> ConfigurationReport
```

- **`*instances`** — one or more already-constructed objects; each is walked
  recursively (its attribute values too, so a nested configurable child is
  reached without naming it).
- **`config`** — a mapping, or YAML **text** (markers like `_target_` work — the
  text is parsed with confluid's own loader). A plain file *name* is not YAML
  text: it applies nothing, warns, and points you at `configure_from_file`,
  which loads the path first (honouring `include:` directives and the
  [config-file search tiers](search-paths.md)) and then behaves identically.
- **`context`** — an optional explicit resolution context for `${ref:}` /
  `${...}` values; defaults to the config itself.
- **Returns** a [`ConfigurationReport`](report.md): every applied override
  with its receiver and origin, failed keys, and the document keys that
  matched nothing.

## Matching: the same ONE rule as loading

A document applied to live objects reads exactly as it does at load time —
flat view, document order, **last write wins**, no priority tiers
(the full grammar: [Broadcasting & Ordered Matching](broadcasting.md)):

```yaml
lr: 0.9              # a bare key broadcasts into every object that accepts it
Trainer:             # a class-name block addresses Trainer instances only
  layers: 10
encoder:             # ... as does a block naming an instance (its `name:` attr)
  dropout: 0.2
Trainer.lr: 0.5      # dotted spelling of the same addressed form
'**':                # a glob rider opts back into cascading below a node
  seed: 7
```

- A **bare key** lands on every walked object whose accept-list carries it —
  constructor params, settable properties, and `__init__`-body slots; the
  `NoBroadcast[T]` / `broadcast=False` opt-outs are honoured exactly as at
  load time.
- An **addressed block** configures its named receiver only, and a dict-valued
  entry addressing a configurable *child* recurses into it at the block's
  position.
- Whichever assignment sits **later in the document wins**, whatever its
  spelling — there is no "addressed beats bare".
- A present `null` **sets** `None`; a typo'd key inside an object's own block
  gets a warning naming the receiver.

Two things the walk never does: it never executes property getters (instance
attributes only), and it never touches private (`_`-prefixed) state.

## What a mapping at a slot means

The dict-at-slot dispatch is ONE rule shared with the load path — decided by what
the slot holds (marker → tune; live `@configurable` child → recurse; plain data →
assign; any other live object → a located refusal). See
[Broadcasting → What a mapping at a slot means](broadcasting.md#what-a-mapping-at-a-slot-means--decided-by-what-the-slot-holds).

## Marker slots are tuned, not built

An attribute holding a marker — `PartialClass(...)` for a slot built *later*
(usually because a constructor argument only exists at runtime,
`params=model.parameters()`), or a plain `Target(...)` recipe — is treated
exactly as loading treats it: keys aimed at it are merged **into the marker's
kwargs**, and nothing is constructed here.

```python
from confluid import PartialClass, configurable, configure, flow

class Optimizer:
    def __init__(self, params=None, lr: float = 1e-4):
        self.params, self.lr = params, lr

@configurable
class Trainer:
    def __init__(self) -> None:
        self.optimizer = PartialClass(Optimizer, lr=1e-4)   # a default, not a decision

trainer = Trainer()
configure(trainer, config={"lr": 0.5})
trainer.optimizer.kwargs["lr"]      # 0.5 — configured ...
opt = flow(trainer.optimizer, params=[...])   # ... and built later, by its owner
```

A plain `Target(...)` slot behaves the same way. It used to be flowed into a
throwaway object which was configured and then discarded — so the marker kept
its defaults while the report said the key had been applied:

```python
@configurable
class Host:
    def __init__(self) -> None:
        self.opt = Target(Opt)          # a recipe, not a deferred slot

host = Host()
configure(host, config={"lr": 0.75})
host.opt.kwargs            # {'lr': 0.75}
flow(host.opt).lr          # 0.75   (was 0.0, built from the defaults)
```

Live objects held **inside** a marker's kwargs (`Target(Stage, dep=widget)`) are
still walked and configured — the marker is not a wall, only a value that is not
built yet. `_ref_` and `_clone_` slots are not markers of this kind and keep
resolving as before.

The rationale — why deferral withholds construction but never configuration —
is recorded in [Architecture Decisions](architecture.md) §5.

## Values first, finalization second

If an object defines a `solidify()` method (the lazy-finalize hook `flow()`
fires — see [Targets & Deferred Initialization](targets.md)), `configure()` re-fires
it **after** the object and its whole subtree carry their new values — never
before. An object that was already finalized keeps its built state: the hook
is idempotent by contract, and fresh derived state after reconfiguration is
what recomputing properties are for ([Class Design](class-design.md)).

## Layering calls

Applying a base config and then an override document is ordinary usage; each
`configure()` call is judged on its own document alone:

```python
configure(trainer, config="lr: 0.9\nTrainer:\n  lr: 0.5\n")   # block is later -> 0.5
configure(trainer, config={"lr": 0.7})                        # nothing competes -> 0.7
```

## When to reach for it

- **Reconfigure without rebuilding** — retune a live object graph (an editor
  panel, an agent adjusting a knob between runs) where reconstruction would
  lose state.
- **Configure objects you did not build** — instances handed to you by a
  framework or test harness.
- **Layer override documents** over a graph a previous pass produced.

If you are starting from YAML and have no live objects yet, you want
`load()` — see [Targets & Deferred Initialization](targets.md).

## Runnable example

[`examples/configure.py`](../examples/configure.py) walks every rule above —
block vs bare ordering, deferred-slot tuning, layered calls, the report, and
the values-before-solidify ordering — with self-checking assertions.
