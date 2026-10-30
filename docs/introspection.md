# Introspection: `cast`, `load(until="settled")` & `solidify=False`

## Typed materialization for static checkers (`cast`)

`flow(node)` returns `Any` — fine at runtime, opaque to mypy and your IDE.
`cast(node, Cls)` is `flow` with a type assertion: it materializes the node
(a no-op if it is already live) **and** tells the type checker the result is
`Cls`, so attribute access is checked and completed downstream:

```python
from confluid import cast

model = cast(config["model"], Model)   # flows if deferred; typed as Model
model.layers                            # <- type-checked, autocompleted
```

Use it at the boundary where a config-loaded object enters typed domain code.

## Introspecting a config without paying for it

When a tool needs a config's **structure** (its nodes + wiring) but not its expensive side effects
— e.g. a visual editor importing a training YAML without downloading datasets or building model
backbones — use one of:

```python
from confluid import load

# (a) load(until="settled"): broadcast-resolved Fluid MARKERS, nothing instantiated. ${ref:} targets are shared
#     by identity (a fan-out is one object reached twice); an unresolved reference stays a Reference.
markers = load("config.yaml", until="settled")        # {key: Target / Partial / Reference marker, ...}

# (b) solidify=False: live-but-inert objects — constructed (cheap, per zero-arg / lazy-init) but the
#     expensive post-flow solidify() (e.g. building a model backbone) is suppressed for the subtree.
graph = load(data, solidify=False)   # also load(..., solidify=False) / flow(obj, solidify=False)
```

Both leave `PartialClass` (`_partial_: true`) slots deferred and default behaviour unchanged (`solidify=True`).

**`load(until="settled")` constructs NOTHING — and no reference can make it.** A structural dotted
reference (`${ref:cfg.lr}`) stays a `Reference` under `load(until="settled")`, exactly as a plain
whole-object `${ref:split}` does. An *attribute* reference (`${ref:split.train}` — reading a
property of the object built at `split`) is refused on this path exactly as it is under
`load()`; the spelling was removed (see [Targets](targets.md#ref--shared-instance)) because
resolving it meant *building* `split`, and this API promises introspection without
construction. (Before 2026-08-11 that construction ran here too, so "introspection without
cost" could walk a dataset — 3.9 s on a real config whose split scans 37 archives;
steady-state cost is now ~10 ms.)

## What a config says about its own settings

A config's comments are its documentation. `document_notes` reads them back — for a command's help or its tab
completion — without building, importing or resolving anything:

```yaml
# run.yaml
# The folders read, each one as it is.
folders: []
per_folder: 0    # 0 = every setup
size: 3

# A quick run.
quick: !scope:quick
  per_folder: 20

runnable: !class:shapes.Builder
  source_dirs: !ref:folders
  count: !ref:size
```

```python
from confluid import document_notes, source_param_docs, source_params

notes = document_notes("run.yaml")
[(key.name, key.comment) for key in notes.keys]
# [('folders', 'The folders read, each one as it is.'), ('per_folder', '0 = every setup'), ('size', ''),
#  ('runnable', '')]
notes.keys[2].feeds                     # (('shapes.Builder', 'count'),) — what !ref:size feeds
[(scope.activation, scope.comment) for scope in notes.scopes]   # [('quick', 'A quick run.')]

source_param_docs("shapes.Builder")["count"]   # its Args: help, read from shapes.py — shapes is not imported
source_params("shapes.Builder")
# (SourceParam(name='source_dirs', annotation='Optional[List[str]]', default=None, switch=False),
#  SourceParam(name='count', annotation='int', default='0', switch=False), …) — every constructor parameter
```

- A key's comment is the run of comment lines **directly above** it (a blank line ends the run), else the comment
  at the end of its own line.
- Only the file named is read — each included file has notes of its own (`load(path, until="raw",
  return_paths=True)` lists them). `include:`, `import:` and `default_scopes:` are not settings, and a key holding
  a scope block is a scope.
- `source_params` lists the constructor's parameters as the source writes them — name, annotation, a literal
  default as text, and whether it is a yes/no parameter — so a front end can offer the ones a config does not write.
- `source_param_docs` finds the module's file without importing the module (the packages above it are imported)
  and follows a package's `from .module import Class`. It does not follow base classes, and a registry name
  without its module (`!class:Builder`) has none.

## Dump and reconstruct

The counterpart of introspection is full-fidelity **round-tripping** — export a
live object graph and rebuild it identically in another process:

```python
from confluid import dump, load

state_yaml = dump(trainer)     # export current state
new_trainer = load(state_yaml) # recreate the exact same hierarchy
```

## Runnable example

[`examples/introspection.py`](../examples/introspection.py) contrasts
`load(until="settled")` markers with `solidify=False` inert objects (using a class whose
`solidify()` is expensive), narrows a node with `cast`, and closes with a
`dump` → `load` round-trip; it then reads a config's notes back with `document_notes` and a class's help with
`source_param_docs`.
