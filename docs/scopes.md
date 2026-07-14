# Scopes

Conditional config blocks live at an arbitrary key whose value carries a
`!scope:` / `!notscope:` tag. The key is inert — pick a descriptive label
(`if_debug`, `if_classification`, …); on activation the wrapper disappears
and the block's contents are spliced in at that slot. Three activation
forms are supported, all equivalent at the IR level:

```yaml
# Boolean — flips on with `--scope debug`
if_debug: !scope:debug
  log_level: DEBUG

# Keyed — flips on with `--scope task=classification` (or `--task classification`)
if_classification: !scope:task=classification
  model: !class:ClassifierModel

# Equivalent function-call form
also_classification: !scope:task(classification)
  model: !class:ClassifierModel

# Negation. `!notscope:KEY=VAL` is also active when the user passes no
# `--KEY ...` at all (the *unset ⇒ active* convention).
unless_debug: !notscope:debug
  log_level: WARNING
```

Resolve them by passing `scopes=` to `load()`:

```python
from confluid import load
trainer = load("experiment.yaml", scopes=["debug", "task=classification"])
```

A CLI framework can forward scope activations straight from command-line
flags (e.g. `--scope debug` / `--task classification`) into `load(scopes=...)`.

## Runnable example

[`examples/scopes.py`](../examples/scopes.py) loads one document under three
different scope activations and shows which blocks were spliced in.
