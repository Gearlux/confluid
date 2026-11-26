# Python scripts — `dump_python()`

The runnable companion to the [serialization guide](../../docs/serialization.md#a-graph-as-a-python-script).
`run.py` writes a small object graph as the Python script that builds it again, runs the script and checks it built
the same graph.

```bash
python examples/python_script/run.py
```

| File | Role |
|---|---|
| `shapes.py` | the `@configurable` classes — in a module of their own, so a script can import them |
| `run.py` | builds the graph, writes and runs its script, asserts the rebuilt graph, shows a refusal |

## What it prints

```python
# The mixture of the python_script example.

from shapes import Mixture, Tone

tone_1 = Tone(frequency_hz=2400000000.0, level_db=-30.0)

mixture = Mixture(
    tones=[tone_1, Tone(frequency_hz=2410000000.0, level_db=0.0)],
    duration_s=0.004,
    reference=tone_1,
)
```

The tone both `tones[0]` and `reference` hold is written once, as `tone_1`, so the script's graph shares it too. A
call that fits in 100 characters stays on one line; a longer one takes one argument per line.

## What it refuses

An object no script can build is refused, naming where it sits:

```text
mixture.reference: a builtins.object is not a configurable object — make it @configurable, or register its spelling with register_dump_spelling
```
