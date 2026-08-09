# Confluid architecture

The *why* behind confluid's mechanisms. The user-facing documentation
([README](../README.md), the per-topic `docs/*.md`) shows **how to use** each surface; this
document records **why the surface is shaped the way it is** — so a reader who asks "why does
this behave like that?" finds the answer here instead of reverse-engineering it from git
history.

Maintenance rules:

- Every record keeps the five elements **Context → Decision → Consequences → Example → What you
  may change**, dated (see the workspace `AGENTS.md` → "Architecture Decisions Are Documented").
- A change that alters a mechanism updates its record **in the same change**.
- A superseded decision is **deleted**, not archived: whatever it still binds is folded into its
  successor record. History lives in git, not here.

This file is **backfilled lazily**: it starts with the mechanisms whose rationale has actually
been asked for, and a record is added whenever a "why does X behave like that?" question is
answered or the mechanism is touched. A mechanism with no record here is not undocumented —
its rules live in `AGENTS.md` and its usage in the topic guides; it simply has not yet needed
its reasoning written down.

---

## 1. An activation that matches nothing is an error, not the default

*2026-08-01*

**Context.** A keyed scope selects a variant: `--scope task=classification` splices the
`!scope:task=classification` block. Resolution was purely *positive* — `_is_active` compared
each block against the activation map, active blocks spliced, everything else dropped. A value
matching **no** block was therefore indistinguishable from passing no scope at all: the
document's unscoped keys applied and the load succeeded.

That silence is expensive in exactly the situation scopes exist for. A consuming CLI that maps
a subcommand or flag onto a scope value has two independent spellings of the same name — the
one the user types and the one the config declares — and nothing compares them. A typo, a
backend the config had no block for, a value renamed on one side only: each ran the *default*
configuration and reported success. The failure surfaces much later, as artifacts that look
right until someone checks which variant produced them.

The check cannot live in the consumer. A CLI knows what the user typed but would have to parse
the YAML to learn what the document offers — which is confluid's job, and confluid already
walks every block during resolution.

**Decision.** `resolve_scopes` opens with `_check_active_values_are_declared`. An active keyed
scope whose **dimension is declared** by positive blocks, with a **value none of them carries**,
raises `ScopeError` listing the values that do exist.

The rule is narrow, and three neighbouring cases stay silent by design:

| Situation | Outcome | Why |
|---|---|---|
| dimension not declared at all | inert no-op | lets a CLI pass a dimension unconditionally while a config grows into it |
| dimension not activated | the unscoped keys apply | that is what "default" means |
| dimension has any `!notscope:` block | every value accepted | a negation is activated by every value *except* the one it names, and deactivated by that one — both outcomes are meaningful, so nothing can be rejected |

That last row is not a concession; omitting it breaks the negation semantics outright. The
first implementation did omit it, and `task=classification` activating a
`!notscope:task=segmentation` block — the documented core of the unset-⇒-active convention —
started raising.

**Consequences.**

- **`discover_dimension_values` reports POSITIVE values only.** A dimension declared solely by
  negated blocks maps to an *empty set*: the key survives (it is a real dimension a CLI must
  bind) but there is nothing to *select*. Reporting the negated value would name the one value
  that does not select the block.
- **The walker is single-sourced.** `_walk_dimensions` returns
  `(positive values per key, keys carrying a negation)`; `discover_dimensions`,
  `discover_dimension_values` and the check all read it. This module has a history of walkers
  drifting apart — `discover_dimensions` once traversed `Fluid.kwargs` while `_resolve_value`
  did not, so a CLI advertised a dimension flag whose block resolution then ignored it.
- **Discovery reads the RAW document.** By the time `load()` returns, the blocks have been
  spliced away and there is nothing left to discover. Callers pass `load_config(...)` or
  `yaml.load(..., Loader=ConfluidLoader)`.
- **Breaking for a config relying on fall-through.** A "default" variant a consumer names
  unconditionally must now be *declared* — typically a block that restates what the unscoped
  keys already say. That cost is the point: the document states which variants it supports
  instead of leaving it to be inferred from what does not crash.

**Example.**

```python
from confluid import ScopeError, discover_dimension_values, load, load_config

raw = load_config("experiment.yaml")
discover_dimension_values(raw)          # {"task": {"classification", "segmentation"}}

load(raw, scopes=["task=classifcation"])
# ScopeError: No scope block matches task='classifcation'. This document declares task
# with: classification, segmentation. Either use one of those values, or add a
# `!scope:task=classifcation` block.
```

**What you may change.** Widen the *message*, not the rule. Each of the three exemptions
prevents a real regression, and narrowing any of them turns a working pattern into an error at
load time for every consumer at once. A new discovery view belongs on `_walk_dimensions`, never
in a fourth traversal.

---

## 2. `flow()` finishes the object, whichever way it arrived

*2026-08-02*

**Context.** Two promises about `flow()` were narrower in the code than in the docstring, and
both gaps had the same shape: they were invisible at the call site and surfaced somewhere else.

*Auto-solidification.* The documented contract is "domain code does not need to manually trigger
solidification — `flow(model)` handles it transparently". It held only on the marker path:
`flow()` opens with an idempotency check that returns any already-live object, and
`_maybe_solidify` ran after *construction*, below that return. So an object that reached the
slot already built — `!class:Model()` (eager, parens), or one handed in from Python — was
returned with its lazy state never finalized. Nothing raised. The consequence landed one step
later and read as an unrelated bug: an optimizer flowed with `params=model.parameters()` got an
empty parameter list, because the backbone `solidify()` would have built did not exist yet.
Consumers papered over it with `build = getattr(model, "solidify", None); build and build()` at
the call site — hand-rolling the engine's own hook because the engine declined to run it.

*Runtime injection.* A marker carries **kwargs** only, because a YAML tag can express nothing
else, and `flow(node, **runtime_kwargs)` matched that shape. But the constructor being deferred
is somebody else's, and a target may take its inputs **positionally** — a variadic
`DataLoaders(*loaders, path=…, device=…)` has no keyword for them at all. Such a slot simply
could not be deferred: `LazyClass(DataLoaders)` had no way to receive the loaders, so callers
constructed it inline and every knob beside the inputs (`device`) became unreachable from
config. The deferral mechanism failed on a signature shape, not on a semantic distinction.

**Decision.** `flow()` covers both.

1. The idempotency return calls `_maybe_solidify(obj)` before handing the object back, so the
   hook fires whichever way the object arrived. It still honours the `suppress_solidify` flag,
   so `flow(obj, solidify=False)` / `materialize(..., solidify=False)` leave a live object inert
   exactly as they leave a constructed one.
2. `flow(obj, *runtime_args, solidify=True, **runtime_kwargs)` threads positional args to the
   call: `target(*runtime_args, **merged_kwargs)`. They are runtime-only — never stored on a
   marker, never round-tripped by `dump()` — which is the same status the `params=` / `dataset=`
   kwargs already had.

**Consequences.**

- **`solidify()` must be idempotent.** It always could be called twice (two `flow()` calls on
  one marker), but a live object now makes that the *normal* path. The convention the lazy-init
  rules already require — build once, cache, return the cache — is what makes the re-fire free.
  A hook with side effects per call is now wrong in a way it was only latently wrong before.
- **`_ctor_params` drops `VAR_POSITIONAL` names.** `inspect.signature` lists `*loaders` under the
  name `loaders`, so a config key of that name passed the kwarg filter and reached the call as a
  keyword — where Python rejects it. The name can never be passed by keyword, so it is not a
  keyword slot.
- **Positional args suppress `Instance` memoization**, for the reason runtime kwargs already did:
  they override the stored spec, so the result is not the shared object the marker names.
- **One path cannot take them.** A registry-*configurable* bare type (`flow(MyClass, …)`)
  materializes through a synthesized marker so broadcasting applies, and a marker is kwargs-only.
  That raises `ConstructionError` naming the two ways out rather than silently dropping the args.
- **Live objects drop them**, matching the existing convention for runtime kwargs. This is what
  keeps `flow(slot, train, valid)` safe when a config wired a live object into that slot.

**Example.**

```python
class Loaders:                                  # somebody else's variadic signature
    def __init__(self, *loaders, device=None): ...

@configurable
class Model:
    def __init__(self, width: int = 8):
        self.width, self.backbone = width, None  # ctor does no functional work

    def solidify(self):                          # build once, cache, return the cache
        if self.backbone is None:
            self.backbone = build_backbone(self.width)
        return self.backbone

slot: Lazy[Loaders] = LazyClass(Loaders, device="cuda")   # config owns the knobs
flow(slot, train_dl, valid_dl)                            # the run owns the inputs

model = Model(width=16)                          # never went through a marker
flow(model)                                      # ...still built: parameters() is ready
```

**What you may change.** The positional channel is deliberately runtime-only — do not add a way
to *store* positional args on a marker. Round-tripping is built on `dump()` emitting keyword
kwargs a tag can carry; positional args have no names, so a stored one could not be dumped,
diffed, or overridden by key. If a target's positional inputs need to come from config, give the
target a keyword.

---

## 3. One precedence rule, in one module

*2026-08-03*

**Context.** Confluid has a single precedence rule — values apply in document order and the
last spec wins — and it was implemented twice. Once in the engine, merging a document onto
markers before construction; once in the post-construction configurator, applying the same
document to live objects. Two implementations of one rule is a standing invitation to drift,
and they drifted.

Four divergences were measured in a single day, all in the same shape and three of the four
silent:

| | engine | configurator |
|---|---|---|
| a mapping at a *declared* key | value (own kwargs) / routing (named block) — inconsistent with **itself** | recursed *into* the marker |
| a `Lazy` in the eager-flow branch | excluded | **included** — deferred slots built eagerly |
| a bare key → a deferred slot | merged into the marker's kwargs | applied to a throwaway, discarded |
| ordering | positional | none — the bare key won either way |

Each produced a plausible-looking run. A trainer's `optimizer: {lr: 0.5}` left the code default
and trained at the wrong rate; a `!lazy:` slot was constructed without the runtime argument it
exists to wait for. Nothing raised, and nothing appeared in the log.

A fifth divergence was a matter of time. The fault is not carelessness — it is that two files
had to agree on a rule neither of them owned.

**Decision.** The rule and its machinery live in **one module**, `confluid/broadcast.py`, which
both callers import: the scope tags, the tagged view, the ordered merge, the child-view splice,
the accept-lists, and the settability predicates. The layering is

    fluid → state → broadcast → engine → loader

`broadcast` **materializes nothing** — no `flow`, no `_flow_recursive`. That prohibition is not
tidiness; it is what makes the dependency one-directional and therefore what makes the module
importable by both the marker path and the live-object path. Code that needs to *build* an
object belongs in `engine`.

`state` exists for one reason: the ordered merge reads the ambient `ConfigurationReport` off the
engine's `ContextVar`, so leaving that state in `engine` would have made the two modules import
each other. Lifting it is the smallest cut that breaks the cycle.

**Consequences.**

- `engine.py` went from ~2,300 lines to ~1,200. That is an effect, not the goal — a 2,300-line
  module with one owner would have been fine.
- Every moved name is re-exported from `engine`, so no existing import breaks. New code should
  import from the real home.
- Diagnostics from the merge now originate in `confluid.broadcast`. A test that captures them by
  monkeypatching a logger must target that module; four did and were updated.
- The divergences above are now unrepresentable rather than merely fixed. That is the whole
  return on the change.

**Example.**

```python
# both paths, one implementation
from confluid.broadcast import _prepare_kwargs        # engine: markers, pre-construction
from confluid.broadcast import _get_acceptable_keys   # configurator: live objects
```

```yaml
lr: 0.9                              # a document-wide default ...
runnable: !class:Trainer()
  optimizer: !lazy:AdamW(lr=0.5)     # ... overridden per-slot below it   -> 0.5
```

Move the bare `lr:` below the block and it wins instead. Four spellings reach that slot —
a marker, a mapping, a dotted key, a class-name block — and all four order identically,
because one module decides for all of them.

**What you may change.** Not the no-materialization rule: an import of `flow` into `broadcast`
re-creates the cycle and, with it, the pressure to keep a second copy of the rule somewhere
convenient.

A reasonable review of this module recommends splitting it further — scope types, predicates,
and merge engine as three files — on the strength of its size (~1,100 lines). **Do not, without
a stronger reason than size.** The three parts are not three concerns; they are one rule and its
vocabulary, and the failure this record exists to prevent came from that rule living in more
than one place. Split it when a *seam* appears — a part with its own callers and its own
invariants — not when a line count crosses a threshold. If the module is hard to read, the
first move is better ordering and narrative comments within it.

The AST scan behind post-init slot discovery (`introspect.py`) is a fair target for reduction:
it is cached per class and cleared per pass, so it is not a hot-path cost, but it does read
source at runtime and needs the build-time bake step in frozen deployments. Encouraging explicit
slot declarations in new code shrinks the reliance without removing the fallback that existing
code depends on.

The permissive treatment of a ``**kwargs`` constructor is deliberate and unchanged: such a
class declares no accept-list, so confluid errs permissive and every bare key in the document
reaches it. The dangerous half is already closed — those keys land as post-init *attributes*,
never as constructor arguments, so a strict library can no longer be called with whatever the
document happens to contain. What is left is a policy question, and the answer is that the
person wiring the class up decides: ``register()`` therefore carries the same accept-list
controls the decorator does (``broadcast=False``, ``broadcast_attrs=[...]``). A class you do not
own is precisely the one you cannot fix by declaring parameters or adding a decorator, so
withholding those controls from ``register()`` had it backwards.

---

## 4. A registered name may map to more than one class

*2026-08-03*

**Context.** The registry was a flat `name -> class` dict. A second registration under a name
already taken silently replaced the first: the displaced class vanished from every picker, and
*which* one survived depended on import order — so the same program could resolve a name
differently between runs for reasons nothing in the config could express.

Two real shapes hit this, and they are not exotic:

- **The same operation per engine.** One name, two implementations, distinguished by the
  presentation/engine tag they already carry.
- **A library publishing one name in two roles.** Several well-known loss functions are exported
  under identical names as both a *loss* and a *metric*. Registering both spellings replaced each
  loss with its metric namesake — invisibly, since both are legitimate configurables for the same
  task.

The naive fix — "make the name unique by convention" — pushes the problem onto every author and
fails for classes you do not own, which is exactly where the collisions come from.

**Decision.** A name maps to a **list of entries**, and the discriminator is the **full tag
tuple**, never one privileged axis:

    _entries: Dict[str, List[_ClassEntry]]   # name -> registrations, in order
    _by_key:  Dict[str, _ClassEntry]         # canonical dotted key -> entry (unique by construction)

Three decisions inside that shape carry the design:

1. **The five reverse indices store entry KEYS, not names.** A name becomes ambiguous at the
   *second* registration — an index keyed by name would have to be rewritten across five dicts at
   that moment. An entry key never changes, so the ambiguity is resolved on *read* instead
   (`_public_key`: the bare name while it is unique, the dotted key once it is shared).
2. **`list_classes()` always returns something `get_class()` accepts.** That is the contract the
   enumerate-then-look-up idiom rests on; breaking it does not raise, it silently empties pickers.
   So the returned identifier changes shape when a name becomes shared, rather than becoming
   invalid.
3. **A bare lookup of a shared name raises `AmbiguousClassError`** — a *sibling* of
   `UnknownClassError`, not a subclass. Code that catches "unknown" to fall back to an import
   must not swallow "ambiguous": the first means *try something else*, the second means *say
   which one*. Making it a subclass would have turned a question into a silent wrong answer.

**Consequences.**

- Ambiguity is disambiguated in config without spelling out a module path, via a tag selector
  on the target (`@axis=value`), resolved at flow time so it also works for a target nested
  inside another marker's kwargs.
- `key_for(cls)` is computed live, never stamped. A name becomes ambiguous when the first class
  is already registered, so a stamp taken at registration time would be stale exactly when it
  matters. The dumper asks it, so a round-trip reloads *this* class rather than a namesake.
- **A cost, and it is real:** an identifier's spelling depends on global registry state at the
  moment it is read. A document written while a name was unique holds the bare name; if an
  unrelated package later registers a namesake, that document now names an ambiguous class and
  raises. The trade was made knowingly — always emitting dotted keys would cost readability in
  every document to protect against a collision most never see — but a consumer that persists
  configs for a long time should know it exists.
- Clobbering is now a spectrum rather than a silent overwrite: the same class object re-registers
  silently (a snapshot restore does that on every bootstrap), a genuinely different class with
  *identical* tags warns and last-write-wins, and anything with a differing tag coexists.

**Example.**

```python
@configurable(task="classification", role="loss", framework="torch")
class Hinge: ...

@configurable(name="Hinge", task="classification", role="metric", framework="torch")
class HingeMetric: ...

get_class("Hinge")                 # AmbiguousClassError, listing both with their tags
get_class("Hinge", role="metric")  # -> HingeMetric
```

```yaml
loss:   !class:Hinge@role=loss
metric: !class:Hinge@role=metric
```

**What you may change.** Not the discriminator. It is the full tag tuple because the two real
collision shapes differ on *different* axes — picking either one as "the" disambiguator solves
half the problem and looks finished.

Not the `AmbiguousClassError` / `UnknownClassError` sibling relationship, for the reason above.

The persistence trade-off in the third consequence was revisited and **decided: `dump()` keeps
emitting the public key.** The obvious alternative — always emit the dotted key, so a written
config is immune to a future namesake — was implemented and measured, and it does not buy
durability. It trades one failure for a more common one:

| what changed after the config was written | bare name | dotted key |
|---|---|---|
| an unrelated package registers a namesake, tags differ | `AmbiguousClassError` (loud, at load) | resolves |
| ...namesake with identical tags | resolves to the namesake — but the clobber **warns at registration** | resolves |
| **the class moves module** (an ordinary refactor) | resolves, the class is still registered | `UnknownClassError` |

Both spellings are handles into a live registry, not import paths — neither survives an
environment that has not registered the class. The only axis they differ on is *which* change
breaks them, and a class changing module is far more routine than an unrelated package claiming
its name. The dotted form also loses the one case with no diagnostic anywhere: a class defined in
a script keys as `__main__.Name`, which is meaningless to any other process.

Always-dotted additionally costs readability in every dumped document and breaks the explicit pin
that a unique name round-trips as itself. Reopen this only with evidence that namesake collisions
outnumber refactors in practice — the measurement above, not intuition, is what should move it.

---

## 5. Deferral withholds construction, not configuration

*2026-08-03*

**Context.** Some dependencies cannot be built when the config is read, because an argument they
need does not exist yet. The canonical one is an optimizer: it takes the model's parameters, and
the model is built by the same pass that would build the optimizer. A config engine that
instantiates everything it finds cannot express that object at all — it either constructs it
without the argument (wrong, and the failure lands far away, at first use) or forces the
dependency out of the config entirely, taking every knob beside it (`lr`, `weight_decay`) with it.

So there has to be a way to say "this one is built later, by its owner". That is `!lazy:` in YAML
and `Lazy[T]` / `LazyClass(...)` in Python.

The subtlety, and the thing that was wrong for a long time, is what "later" is allowed to defer.
Deferring construction is the point. Deferring *configuration* looks like the same thing and is
not: a marker's kwargs are a plain mapping, so merging a key into them **builds nothing**. The
two statements are independent, and conflating them made a slot that a consumer declared in
code — `self.optimizer = LazyClass(AdamW, lr=1e-4)` — unreachable from configuration entirely.
Every spelling failed, and the run trained at the hard-coded rate and reported nothing.

**Decision.** Deferral withholds **construction only**. A deferred marker is broadcast into,
addressed, tuned and ordered exactly like an eager one; the single thing withheld is the terminal
build.

Three rules follow, and they are load-bearing together:

1. **Nothing auto-builds a `Lazy`.** Not the recursive descent of a materialization pass, not the
   post-init attribute step, not an external deep-flow walker. A bare `Class` stub in the same
   position *is* built eagerly — that difference is the whole distinction between the two markers,
   and it is why a slot needing a runtime argument must be `!lazy:` and not `!class:`.
2. **An explicit `flow()` builds it, deliberately.** `flow()` means "build this now", so the
   owner calls it when it has the missing piece. The Lazy-ness defers *automatic* construction,
   never a direct request.
3. **It is configured like anything else.** Broadcast keys merge into its kwargs; a mapping
   addressed at its slot tunes it rather than replacing it; a kwarg set in code is a default and
   loses to a later document key, exactly as a constructor default does.

**Consequences.**

- The knobs beside the runtime argument stay in configuration. That is the whole return: `lr`
  is tunable from YAML and from a CLI override even though the object cannot be built yet.
- A slot's declaration is a promise about *timing*, not about reachability. A reader seeing
  `LazyClass(...)` in an `__init__` body knows it is built later, not that it is beyond config.
- `solidify()` must be idempotent, because an owner may flow the same slot more than once.
- The cost: the object does not exist until someone flows it, so an error in its construction
  surfaces at that call rather than at load. That is inherent — the argument genuinely is not
  available earlier — and it is why the deferred target should still be cheap to build once its
  input arrives.

**Example.**

```python
@configurable
class Trainer:
    def __init__(self) -> None:
        self.optimizer = LazyClass(Optimizer, lr=1e-4)   # a default, not a decision

trainer = load("lr: 0.5\nt: !class:Trainer()\n")["t"]
type(trainer.optimizer)            # Lazy — the pass did NOT build it
trainer.optimizer.kwargs           # {"lr": 0.5} — but it DID configure it
flow(trainer.optimizer)            # ValueError: Optimizer needs params
flow(trainer.optimizer, params=p)  # built, at lr=0.5, when the owner has the model
```

The third and fourth lines are the record in miniature: the same marker is fully configured and
still unbuildable, and only the caller that holds `params` can finish it.

**What you may change.** Not rule 1, and specifically not by re-adding an early return for
`Lazy` in the kwarg-resolution path. That is the shape the original bug had: it read as "leave
deferred values alone", which is right about building and wrong about configuring, and the
result was that an identical marker behaved differently depending on whether it was written in a
document or created in code. If a future change needs a value left entirely untouched, that is a
*different* marker with a different name — do not overload this one.

Rule 2's asymmetry with rule 1 is deliberate and worth keeping explicit: automatic walkers skip a
`Lazy`, a direct `flow()` does not. Making `flow()` also skip it would leave no way to build the
object at all.

---

## 6. Position is settled once, and the marker remembers

*2026-08-03 (recorded 2026-08-08)*

**Context.** Confluid has one precedence rule — document order, last spec wins — and the ordered
merge that applies it runs once per marker, when `_prepare_kwargs` walks the surrounding context
and unrolls the marker's own kwargs at its slot's position. But a later pass exists: the
nested-marker broadcast in `engine._resolve_kwarg_value`, which fills markers that never met the
document (a constructor default `Class(Engine, power=7)`, a body slot `LazyClass(AdamW, lr=1e-4)`).
Left alone, that pass re-applies a bare key over a contest the ordered merge already settled —
which is a specificity tier by another name, the exact thing the one rule forbids.

Something must therefore say "this marker's kwargs are already ordered". The first discriminator
was `_yaml_loc`: a marker carrying a source location was "authored", one without was "a code
default". That answered wrong for one shape — a document that *tunes* a code-built marker
(`optimizer: {lr: 0.5}`) merges into the code marker and inherits its **empty** location, so the
author's value read as a default and lost to any bare key regardless of where either sat.

**Decision.** An explicit engine flag, `Fluid._order_resolved`, stamped by `_flow_recursive` the
moment the ordered merge has run for that marker; the broadcast pass skips any key the marker
already carries once the flag is set. `_yaml_loc` is demoted to diagnostics — an error-message
pointer, never a precedence input.

Two corollaries carry the same idea to the places the flag cannot reach:

- A **mapping addressed at a deferred slot** is applied post-construction and a plain dict
  carries no position — so the contest is decided *where the ordering is still visible* and only
  its outcome travels (`Fluid._late_bare_keys`: per dict-valued slot, the bare keys positioned
  later than it).
- On the **configure() path** the verdict is call-scoped — a `beaten` parameter computed by the
  owner's scan — never marker state. It was briefly stamped on the marker, and a second
  `configure()` carrying only a bare key found that key still marked "lost" against a document
  it never saw.

**Consequences.**

- Engine bookkeeping lives on the marker (`_order_resolved`, `_late_bare_keys`,
  `_addressed_keys`), because the marker is the only thing that survives between the passes that
  write and read them. Every read goes through the `fluid.is_order_resolved` /
  `late_bare_keys_of` / `addressed_keys_of` accessors, so each default is written down once.
- The guard is load-bearing and measured: disabling it fails 7 tests, including a
  document-authored marker losing to an *earlier* bare key.

**Example.**

```yaml
lr: 0.9                                  # earlier bare key ...
opt: !lazy:torch.optim.AdamW(lr=0.5)     # ... loses to the later own kwarg -> 0.5
```

Swap the two lines and the bare key wins instead. Without the flag, the broadcast pass would
apply `lr: 0.9` in **both** orderings.

**What you may change.** Not the discriminator: `_yaml_loc` must never again gate precedence
(diagnostics drift toward absence — a merged, tuned, or hand-built marker legitimately has no
location, and absence must not mean "default"). And never persist a configure()-path verdict on
the marker — it would outlive the document that produced it.

---

## 7. Interpolation burns in — at load, for every spelling

*2026-08-08*

**Context.** `${...}` interpolation ran at load time for plain mapping and list values, but the
Resolver returned any Fluid whole — so a placeholder written inside a `!class:`/`!lazy:` tag's
mapping body stayed the literal string on every path, silently, while the quoted-string spelling
of the same target (`"!class:Src(input_dir=${DATA_ROOT})"`) interpolated, because the
string parser resolves per kwarg. Two spellings of one target, two answers — and the losing one
was the spelling the docs recommended.

**Decision.** The load-time Resolver pass walks a marker's kwargs too
(`Resolver._interpolate_fluid_kwargs`) — **text-only** and **in place** — and the substituted
value **burns in**.

Three alternatives were rejected, each for a stated reason:

- *Flow-time (late-bound) substitution* — it would break the single-pass contract, make a value
  depend on *when* a deferred slot is built (an environment change between load and
  `configure_optimizers` changes the run), and duplicate a channel that exists: `!ref:` to a
  plain key is the late-binding mechanism, and stays it.
- *A full `resolve()` walk of the kwargs* — that would also parse `"!ref:"`/`"!class:"` strings
  and eagerly resolve `Reference` fluids, changing when deferred values bind. Only text
  substitution happens; strings keep their prefixes for flow-time parsing, matching the
  top-level order (interpolate first, parse later).
- *Copy-on-write kwargs* — marker identity is load-bearing (the flow memo and `!ref:` sharing
  key on `id()`), and a naive copy of a marker reached twice would split the one-marker-one-
  instance guarantee. In-place follows the precedent `_expand_block_keys` set for marker kwargs.

**Consequences.** `dump()` emits the substituted value, so a reload in a different environment
reproduces this run — reproducibility over freshness, by design. A deferred `!lazy:` slot flowed
later sees the load-time value. A hand-built marker passed through `materialize()` gets its
kwargs rewritten in place; a template reused across environments would keep the first
environment's values (accepted as exotic; see below).

**Example.**

```yaml
run: {name: exp42}
opt: !lazy:Sink()
  out_dir: "${DATA_ROOT}/${run.name}"    # substitutes at load; construction stays deferred
```

`dump()` of the loaded marker emits `/store/exp42` — changing `DATA_ROOT` and reloading the dump
reproduces the original run.

**What you may change.** Not to a full `resolve()` of kwargs (it changes when deferred values
bind), and not to flow-time substitution (it re-opens the two-answers problem as a
*when*-you-flow dependence). A memoized-copy variant — one copy per marker per pass, preserving
aliasing — is an acceptable refinement if in-place mutation of hand-built templates ever bites
in practice.

---

## 8. Two drivers, one scanner

*2026-08-08*

**Context.** Record 3 unified the precedence rule's *vocabulary* — one module both paths import
for the scope tags, the tagged view, and the accept-lists. It deliberately stopped there, and
within a week the audit found what stopping there costs: the two drivers still wrote their own
*sentences*. The block-consumption ladder existed twice with a byte-identical comment on its
third branch, and five NEW cross-path differences had accumulated (a routing-hoist merge
condition, a list value tuning deferred slots on one path only, the Fluid self-broadcast guard
missing on one path, and two grammar differences nobody had ever decided). Nothing but memory
distinguished the deliberate ones from the drift.

**Decision.** The walk itself exists once. ``_scan_view`` owns the main loop, the five-branch
``_consume`` ladder, own-kwargs consumption, and position bookkeeping; it emits every gate
outcome to a sink in document order. What may differ between the paths is confined to two
declared seams:

- a **receiver**, built ONLY by the two factories that sit side by side
  (``_receiver_for_target`` / ``_receiver_for_instance``) — each kept cross-path difference is
  a named predicate with a pin in ``tests/test_cross_path_pins.py``;
- a **sink** (``_MergeSink`` for markers; ``configurator._LiveSink`` for live objects, which
  stays in ``configurator`` because its outputs feed ``_assign``, which flows) — an effect
  writer only. **A sink may not grow branches on keys or scopes**; a new rule belongs in the
  scanner behind a receiver predicate, or nowhere.

The rewrite was proven equal, not assumed: a verbatim copy of the old implementation was
replayed against every real invocation captured over an 18-document corpus (values, key order,
scope tags), then DELETED with the phase — a retained reference would be a third copy of the
rule. Dispatch is a visitor, not a decision-object stream: a 2,500-marker pass emits tens of
thousands of decisions, and method calls replace the old closure calls one-for-one (measured
end state: materialize within 1%, configure ~19% faster after receiver caching and lazy
position bookkeeping).

**Consequences.**

- A divergence in the WALK is now unrepresentable — there is one walk. A divergence in a
  *predicate* is two adjacent functions in one diff, reviewed together.
- The remaining cross-path differences are declared, not ambient: D4 (top-level dict is a value
  on the load path, a block on configure) and D5 (a glob-delivered dict reaches a declared slot
  on configure only) each carry twin pins that fail on drift in either direction. D5 is flagged
  for its own adjudication — aligning it is a feature, not drift repair.
- Receivers for marker targets are cached per pass (pure per spelling × target × instance
  name); instance receivers are not (their predicates close over ``vars(obj)``).

**Example.**

```python
# the ONE walk, two compositions
def _prepare_kwargs(cls_name, own_kwargs, parent_context, target=None, self_obj=None):
    receiver = _receiver_for_target(cls_name, own_kwargs, target)
    sink = _MergeSink(receiver.cls_name)
    _scan_view(parent_context, receiver, sink, own_kwargs=own_kwargs, self_obj=self_obj)
    return sink.merged

def _apply(obj, view, context, visited, report):          # configurator
    receiver = _receiver_for_instance(obj)
    sink = _LiveSink(obj, receiver.cls_name, target_label, report)
    _scan_view(view, receiver, sink)
    ...
```

**What you may change.** Not the sink rule: the moment a sink branches on a key name or a
scope, the rule has two homes again and this record's history restarts. A new cross-path
behavior is a new receiver predicate — added to BOTH factories (even if one side is constant),
with a pin per path. The splice pair (``_splice_kwargs_at_slot`` vs ``_spliced_subtree_view``) is the ONE
sanctioned duality, landed as Phase B: both live in ``broadcast`` as thin compositions over the
shared primitives ``_merge_rider`` / ``_merge_routing`` (the D1-adjudicated hoist policy) /
``_spent_at_boundary`` — never one mode-flagged function, because the marker splice's collision
rules (``_parent_wins``, the glob shield) have no live analogue.

---

## 9. `!clone:` stays — the identity model's independence escape hatch

*2026-08-09*

**Context.** A dead-surface audit found `!clone:` used by no config anywhere in sight —
a marker type, a tag constructor, a flow branch, a merge branch and two dump branches
maintained for a feature with no caller. The audit recommended deletion; the ruling was
KEEP, and this record is the reason, so the next audit stops at the "why" instead of
re-litigating the grep.

**Decision.** `!clone:` is retained as a first-class tag because the identity model is
incomplete without it. Sharing is the DEFAULT everywhere: `!ref:` resolves to the one
instance by identity, and the merge layer deliberately preserves Fluid identity when it
copies (`merger._preserve_identity_copy` — its own docstring defers to `!clone:` as the
opt-in for independence). Delete the tag and "a fan-out of INDEPENDENT instances from
one spec" has no spelling in the language at all — the design's sharing-by-default
posture is safe only while the escape hatch exists. Zero *current* users measures
adoption, not load-bearing-ness: the tag is published surface (the tag table in the
user docs), fully pinned, and has produced zero findings in any audit.

**Consequences.**

- Five small branches stay maintained across `fluid` / `loader` / `engine` / `merger` /
  `dumper`. The cost has measured zero: no drift, no defect, no finding.
- `_flow_clone` stays trivially thin: flow the referenced value, `deepcopy`, apply
  overrides. Anything smarter (copy-on-write, partial sharing) is out of scope.

**Example.**

```yaml
counter: !class:Counter()
  count: 5
shared:  !ref:counter      # the SAME live object — identity preserved
copy:    !clone:counter    # an independent deep copy; mutations don't propagate
tuned:   !clone:counter    # a clone may carry overrides
  count: 9
```

**What you may change.** The deepcopy semantics are the contract (pinned in the clone
test suite) — do not weaken them to a shallow copy for performance without a new record.
Deleting the tag requires first answering where independent fan-out goes instead; a
grep showing no users is not that answer.
