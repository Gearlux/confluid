# Serialization — `dump()` and the Round Trip

`dump()` writes a live object graph back to YAML; `load()` of that YAML
reconstructs an identical graph. This symmetry is a core contract: **a dumped
configuration is a complete, reloadable record of a run.**

```python
from confluid import dump, load

state = dump(trainer)          # YAML text
clone = load(state)            # an equivalent object graph, any process
```

## What a dump contains

`dump()` emits the **plain format** — reserved keys, no tags — so a dumped
document is ordinary YAML that `yaml.safe_load`, `yq` and an editor schema can
all read. That holds for every value it can emit, including a function-valued
param (`collate_fn: ${ref:mypkg.collate_records}`) and the informational
placeholder it falls back to for an opaque object.

For each `@configurable` (or registered) instance, `dump()` emits a
`_target_:` mapping covering **both kinds of slot** — constructor parameters and
`__init__`-body attributes — reconstructed per slot:

1. the **live attribute of the same name**, when the instance still carries
   it — so post-construction changes (a `configure()` call, a broadcast) are
   what you get back;
2. otherwise the **captured constructor kwargs** — stamped at build time —
   which is what keeps a plain [eager class](eager-classes.md) (one that
   *transforms* its constructor args instead of storing them) reloadable.

Values follow the same rules recursively: a nested configurable dumps as its
own `_target_:` node, a shared instance reached twice dumps once and is
referenced (`${ref:...}`), a deferred slot still holding a partial marker dumps
as `_partial_: true` — deferred in, deferred out.

**Every value is emitted, including one equal to its default.** That is what
makes a dumped document self-contained: if a value were omitted because it
happened to match the default, then changing that default in the source later
would silently change what an existing dump reloads to.

```python
@configurable
class Trainer:
    def __init__(self, lr: float = 0.1):
        self.lr = lr
        self.epochs = 10          # a body slot, no constructor parameter

configure(trainer, config={"epochs": 50})
dump(trainer)                     # _target_: Trainer / lr: 0.1 / epochs: 50
```

## What a dump deliberately omits

- **Derived state** — read-only properties and `_`-prefixed internals are
  never emitted; they are recomputed by the reconstructed object
  ([Class Design](class-design.md)). A constructor **parameter** shadowed by a
  property dumps its CAPTURED ctor value (`device: cpu`, not the getter's
  derived object) — the getter never runs during a dump.

Opaque **values** dump by value wherever a faithful spelling exists: a `Path`
as its string, an `Enum` member as its value, a numpy scalar as the Python
scalar it wraps — each reloads through the constructor that took it. A
`**kwargs` class's captured extras are emitted too (they *are* constructor
arguments). Anything else keeps the bare `{_target_: X}` placeholder, and the
dump WARNS once per type that the placeholder reloads default-constructed —
register the class for a faithful round trip. A literal `$` in any emitted string is
written as `$$` — the loader's escape — so interpolation-active text
(`echo $RUN_USER`, a `${...}` kept verbatim) reloads unchanged instead of being
substituted: the reload leaves `$$` alone through every document stage and turns
it back into `$` when the document becomes objects. This is why the convention asks for a
  recomputing `@property` rather than a stored attribute: a property stays out
  of the document, while a stored one is a body slot and is dumped.
- **Runtime-injected arguments** — the `params=` / positional inputs handed
  to `flow()` at build time are call arguments, not configuration; the slot
  they fed round-trips as its `_partial_` recipe.
- **A `None` that is the default** — omitted only when the parameter's
  default is also `None` (lossless); any other default dumps an explicit
  `param: null`, because omitting it would silently restore the default on
  reload.

## Registering a document spelling

The three built-in value spellings above are the hardcoded cases of a general
extension point: `register_dump_spelling(cls, spell)` declares how a
third-party value type is written into a document. `spell(value)` returns the
value's document form — a `Target` marker (rebuilt by the ordinary load
machinery, so the round trip needs no load-side change) or a plain YAML-clean
value — or `None` to *decline*, keeping the placeholder and its warning (a
recipe declines when the value is faithful only up to a point, e.g. a tensor
above a size cap):

```python
from confluid import Target, register_dump_spelling

def vector_from_values(values):          # the reload target — a plain callable
    return MyVector(values)

register_dump_spelling(
    MyVector,
    lambda v: Target(vector_from_values, values=list(v)),
)
```

The rules, each pinned:

- **A registered spelling is THE document spelling of its type.** It wins over
  the generic slot-walk reconstruction even for a registered/`@configurable`
  instance — a spelling exists precisely where the generic walk gets the type
  wrong (a `**kwargs` base whose body slots the constructor chain refuses
  back).
- **The built-ins win over a registered spelling** (a `Path` always dumps as
  its string), and the lookup walks the value's MRO, exact class first — a
  subclass hits its base's recipe unless it registers its own.
- **A marker's kwargs may carry live objects** (a collection's members): each
  is rendered by the dumper's ordinary rules, registered spellings included.
- **The in-memory document is untouched.** `to_markers()` / `configure()` keep
  live values live — identity is what `configure()` maps settled values back
  onto; only the text emission respells.
- A body slot rebinding one of the object's **own methods**
  (`self.update = wrap(self.update)` — a common library idiom, stored either
  as the bound method or a `functools.wraps` closure over it) is machinery,
  not configuration, and is never dumped.

## Two things to know

- **Class names are registry handles — unless you ask for importable paths.**
  By default a dump names each class by its public registry key — the bare name
  while unique, the module-dotted key once a namesake is registered
  ([Discovery](discovery.md)) — so the reloading process must have the class
  registered or importable. `dump(obj, qualified=True)` names each class by its
  importable dotted path (`module.qualname`) instead, so the document reloads
  in a **cold** process with no registrations at all — the spelling a run
  artifact wants. A class whose path imports to nothing (a `<locals>` factory
  class, `__main__`) keeps the registry key. Trade-offs in
  [Architecture Decisions](architecture.md) §4.
- **Interpolation is burned in.** A `${DATA_ROOT}`-style placeholder is
  substituted at load time, and `dump()` emits the substituted value — so
  reloading the dump in a different environment reproduces *this* run rather
  than resolving afresh ([Architecture Decisions](architecture.md) §7). A
  value that must stay late-bound uses `${ref:}` to a plain key.

An eager class may opt out of kwargs capture (`capture=False`) when a
constructor argument is too heavy to hold; that deliberately relaxes the
round-trip for that class — the trade-off is documented in
[Eager Classes](eager-classes.md).

## Runnable example

[`examples/reproducible_experiment.py`](../examples/reproducible_experiment.py)
builds a two-stage pipeline, exercises its derived (property-held) state,
dumps it, and asserts the round trip: the dump carries only the configured
inputs — the derived state provably stays out of the YAML — and the
reconstructed pipeline rebuilds it to identical values. For the eager-class
side of the round trip (captured kwargs), see
[`examples/eager_classes.py`](../examples/eager_classes.py).
