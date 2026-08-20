# hydraide — the preprocessor

> New here? [The Lifecycle](lifecycle.md) maps the nine passes; hydraide is the
> first seven, written to a file.

`hydraide` resolves a config to **one plain-YAML document**: includes spliced,
scopes applied, dotted keys expanded, interpolation burned in, broadcasting
settled, shared markers anchored. Every marker in the output carries its *final*
kwargs, so "what did my config resolve to?" is a file you can read, diff, commit,
or hand to `yq` — not an experiment against the engine.

```python
from confluid.hydraide import emit, check

text = emit("experiment.yaml", scopes=["framework=torch"])   # the resolved document
diff = check("resolved.yaml")                                 # None, or a unified diff
```

## The command line

The same two operations as a command — `pip install 'confluid[cli]'` (Click):

```bash
hydraide emit experiment.yaml --scope framework=torch        # the resolved document, on stdout
hydraide emit experiment.yaml -o resolved.yaml               # … or written to a file
hydraide check resolved.yaml                                 # exit 1 + unified diff unless the file
                                                             # is its own resolution — a CI gate
```

With no `--scope` at all, a dimension the document defaults (a top-level
`default_scopes: [framework=lightning]`, see [Scopes](scopes.md)) resolves to that
default — `emit` runs `load()`, so it applies whatever `load()` applies.

A relative path resolves through the same search tiers `load()` uses (CWD →
`./config/` → XDG), so `hydraide emit experiment.yaml` finds `./config/experiment.yaml`.
A malformed document is a located `ConfluidError` — one line on stderr
(`file:line:col`), exit 1, no traceback; a usage error exits 2.

### Shell completion

`hydraide completion SHELL` prints the activation script for `bash`, `zsh` or
`fish`. Once it is loaded, verbs and options complete, `CONFIG` completes file
names, and `--scope` completes **from the document you named**:

```
$ hydraide emit experiment.yaml --scope fram<TAB>
framework=keras   framework=torch                            # what experiment.yaml declares
```

(the values come from `discover_dimension_values(load(path, until="raw"))` — a
boolean dimension is offered bare).

**Bash, in a custom rc file.** Put these lines in the file your shell sources at
start-up — `~/.bashrc`, or a project rc you `source` yourself — *after* the line
that puts `hydraide` on `PATH` (activating the virtualenv, for instance): the
completion function calls the `hydraide` executable, so it must be findable when
the rc runs.

```bash
# in your rc file — AFTER the venv is activated / PATH is set
source /path/to/.venv/bin/activate
eval "$(hydraide completion bash)"
```

That runs `hydraide` once per shell start (~0.1 s). If you would rather not pay
that on every prompt, generate the script once into a file and source the file —
regenerate it when confluid is upgraded:

```bash
hydraide completion bash > ~/.hydraide-complete.bash          # once (and after an upgrade)
```

```bash
# in your rc file
source /path/to/.venv/bin/activate
source ~/.hydraide-complete.bash
```

Reload the shell (`exec bash`, or open a new terminal) and check:

```bash
$ type _hydraide_completion | head -1
_hydraide_completion is a function
$ hydraide em<TAB>                          # → emit
$ hydraide emit experiment.yaml --scope <TAB>
```

Two things to know:

- **Bash 4.4 or newer.** The script uses `complete -o nosort`; macOS's stock
  `/bin/bash` is 3.2 and prints `complete: nosort: invalid option name`. Use the
  Homebrew bash (`/opt/homebrew/bin/bash`, 5.x) or Linux's default.
- **`--scope` completion reads the file you typed** — it opens the config through
  the same search tiers as `load()`, so a relative name resolves against the CWD,
  `./config/`, then XDG. No file typed yet, or an unreadable one → no candidates
  (never an error in the shell).

**Zsh / fish** are the same one-liner with the shell name:

```bash
eval "$(hydraide completion zsh)"       # ~/.zshrc  (or: hydraide completion zsh > ~/.zfunc/_hydraide, with ~/.zfunc on fpath)
hydraide completion fish | source       # ~/.config/fish/config.fish
```

## Two spellings in, one spelling out

Confluid reads two spellings of the same document. The **tag form** is the
preferred way to *write* a config — concise, one line per node:

```yaml
# base.yaml
model: !class:Model(hidden=32)
lr: 0.1                                  # a bare key — broadcasts to every accepting node
train_set: !class:Stream
  ops: !ref:preprocess
preprocess:
  - !class:Resize(size=224)
optimizer: !partial:Adam                 # deferred: built later, with runtime `params`
```
```yaml
# experiment.yaml
include: base.yaml
model.hidden: 64
torch_only:
  _scope_: {framework: torch}
  lr: 0.3
```

The **reserved-key form** (`_target_` / `_partial_`) is what hydraide *emits* —
ordinary YAML that any tool reads, and that confluid reloads to the same graph:

```yaml
# emit("experiment.yaml", scopes=["framework=torch"])
model:
  _target_: Model
  hidden: 64                # the dotted override, applied
  lr: 0.3                   # the scoped bare key, broadcast — visible, not implicit
lr: 0.3
train_set:
  _target_: Stream
  lr: 0.3
  ops:
  - &preprocess_0           # a shared marker is a NAMED anchor
    _target_: Resize
    size: 224
preprocess:
- *preprocess_0
optimizer:
  _target_: Adam
  _partial_: true
  lr: 0.3
```

Both spellings are first-class *input* — a file may mix them, and neither warns.
Whichever you write, hydraide's output is **byte-identical**: that is the
invariant its tests pin.

## What the output guarantees

- **Every contest is settled.** A bare key, an addressed block, a dotted key, an
  include, a scope — whatever competed for a value, the winner is written on the
  node. Nothing is left for the engine to arbitrate: construction (pass 8) reads
  the settled tree and never looks at the document again.
- **Every reference is settled — the document is closed.** A `!ref:` to a marker
  is an anchor; a `!ref:` to a plain value is the value, inlined; an import-path
  `!ref:` is `${ref:module.qualname}` (what `dump()` writes for a function). No
  `_ref_` survives in the output, and a reference that resolves to nothing is a
  located error, not an emitted marker.
- **It is idempotent.** `emit` on its own output produces the same text,
  which is what `check` rests on: a committed resolved artefact that drifts
  from its source fails CI with the lines that changed.
- **Shared markers are anchors, named after where they live.** A marker reached
  twice (`!ref:`, or a YAML alias) is emitted once as `&<shortest path>` and
  aliased elsewhere — `&preprocess_0` for the first element of a top-level list.
  On reload the alias sites share one instance.
- **Deferral survives.** A `!partial:` / `_partial_: true` node is emitted
  deferred and stays deferred on reload.
- **The artefact is self-contained.** Every `import:` the load consumed — the
  entry file's and the included files' — is re-emitted as the document's first
  line, so a fresh process can `load()` the artefact without the source tree's
  side effects.
- **A malformed document is refused, located.** `emit` raises exactly what
  `load()` would — a `ConfigurationError` naming `file:line:col`; the command
  renders it as one line, no traceback. That includes a class name the document
  cannot resolve: settle (pass 7) refuses it where construction would have, so
  `check` cannot bless a typo'd `_target_:` with exit 0. A YAML syntax error
  (PyYAML's own, with its file:line mark) is also one line from the command.
- **Anchor names are unique.** Two shared values whose shortest paths fold to
  one spelling (`m[0]` and a key literally named `m_0`) get deterministic
  suffixes (`&m_0`, `&m_0-2`) — the output always parses back.

## Two things to know

**Identity is per marker, not per container.** A list reached through `!ref:` is
emitted at both sites with its *elements* anchored: on reload you get two lists
holding the same instances. If the container itself must be one object, make it
a marker.

**An anchor does not follow an include overlay.** If `base.yaml` writes
`model: &m !class:Model(hidden=32)` and aliases it elsewhere, and
`experiment.yaml` overlays `model: {hidden: 99}`, the overlay tunes a *copy* at
the key — the alias sites keep the original. `!ref:model` is late-bound and does
see the tune. Prefer `!ref:` for a cross-file reference; use anchors within one
file.

## The other direction: `confluid.spelling.to_tags`

hydraide turns the tag form into the plain form. A file that came the other way —
an emitted artefact you now want to *edit*, a config written before the tag form
was preferred — is converted back **line by line** by `confluid.spelling`, so its
comments and layout survive:

```yaml
# before — the reserved keys
runnable:                # the run
  _target_: Trainer
  model:
    _target_: Model
    _partial_: true
    hidden: 32           # a kwarg keeps its line
```
```yaml
# after — to_tags(text): the tag on the key line, the reserved-key lines gone, nothing else moved
runnable: !class:Trainer                # the run
  model: !partial:Model
    hidden: 32           # a kwarg keeps its line
```

Whatever the grammar cannot convert is *reported*, never guessed: a `_scope_`
with several dimensions (the tag suffix cannot carry two), a `<<:` merged into a
marker. And the conversion of a *file* is gated by hydraide itself —
`convert_file` writes only when `emit(before) == emit(after)` under every scope
activation the document declares:

```python
from confluid.spelling import to_tags, convert_file

text, findings = to_tags(open("cfg.yaml").read())   # findings: (path, line, text, reason)
result = convert_file("cfg.yaml")                    # .written, .findings, .activations_checked
```

## Python API

```python
from confluid.hydraide import emit, check

text = emit("experiment.yaml", scopes=["framework=torch"])   # the resolved document
diff = check("resolved.yaml")                                 # None, or a unified diff
```

`emit` accepts a path or YAML text. Both raise the same located
`ConfigurationError` a `load()` would.

## Runnable example

[`examples/hydraide.py`](../examples/hydraide.py) writes the two spellings above
to a temporary directory, resolves both, asserts the outputs are byte-identical
and idempotent, and reloads the result. The rationale — why one preprocessor
rather than a rule the engine re-derives on every path — is
[Architecture Decisions](architecture.md) record 19.
