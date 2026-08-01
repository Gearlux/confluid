"""Scopes — companion to ``docs/scopes.md``.

One document, three activations: no scopes, ``debug``, and ``task=classification``.
``!notscope:`` demonstrates the *unset => active* convention.

The second half shows a scope block nested INSIDE a ``!class:`` marker — the way
to offer an alternative for a single slot without lifting it to the document
root. A marker's kwargs are a mapping like any other, so the wrapper splices at
its own position and the later write wins.
"""

from typing import Any, Dict

from confluid import configurable, load

DOC = """
log_level: INFO
if_debug: !scope:debug
  log_level: DEBUG
unless_debug: !notscope:debug
  log_level: WARNING
if_classification: !scope:task=classification
  head: classifier
"""


def summarize(label: str, cfg: Dict[str, Any]) -> None:
    print(f"{label:<28} log_level={cfg['log_level']:<8} head={cfg.get('head', '-')}")


@configurable
class TimmModel:
    """A zoo-backed model — the default slot value."""

    def __init__(self, model_name: str = "efficientvit_b0") -> None:
        self.model_name = model_name


@configurable
class ConvNet:
    """A from-scratch model — the alternative the `model=convnet` scope selects."""

    def __init__(self, widths: int = 32) -> None:
        self.widths = widths


@configurable
class Trainer:
    def __init__(self, model: Any = None) -> None:
        self.model = model


#: A scope block INSIDE the marker's own block, after the slot it overrides.
SLOT_DOC = """
runnable: !class:Trainer
  model: !class:TimmModel()
  alt: !scope:model=convnet
    model: !class:ConvNet()
"""

#: The three body shapes. A SEQUENCE body extends the surrounding list (the only
#: way to write a conditional list ITEM); a SCALAR body substitutes one entry.
BODY_DOC = """
ops:
  - always_first
  - !scope:extra=yes
    - extra_a
    - extra_b
  - !scope:extra=yes 42
  - always_last
"""


def main() -> None:
    # No scopes: !notscope:debug is ACTIVE (unset => active), so WARNING wins (later in doc).
    plain = load(DOC)
    assert plain["log_level"] == "WARNING" and "head" not in plain
    summarize("scopes=[]", plain)

    # debug active: the !scope:debug block splices in, the !notscope: block disappears.
    debug = load(DOC, scopes=["debug"])
    assert debug["log_level"] == "DEBUG"
    summarize("scopes=['debug']", debug)

    # A keyed scope: task=classification adds the classifier head.
    task = load(DOC, scopes=["task=classification"])
    assert task["head"] == "classifier" and task["log_level"] == "WARNING"
    summarize("scopes=['task=class...']", task)

    # A scope nested in a marker's own kwargs swaps ONE slot. No activation keeps
    # the default; `model=convnet` splices the alternative over it.
    default_slot = load(SLOT_DOC, flow=False)["runnable"]
    assert default_slot.kwargs["model"].target == "TimmModel"
    assert "alt" not in default_slot.kwargs  # the inactive wrapper is dropped
    print(f"{'slot: scopes=[]':<28} model={default_slot.kwargs['model'].target}")

    swapped = load(SLOT_DOC, flow=False, scopes=["model=convnet"])["runnable"]
    assert swapped.kwargs["model"].target == "ConvNet"
    print(f"{'slot: model=convnet':<28} model={swapped.kwargs['model'].target}")

    # Body shapes: the sequence body EXTENDS the list (two entries, not one nested
    # list) and the scalar body substitutes one, type-coerced to int.
    lean = load(BODY_DOC)
    assert lean["ops"] == ["always_first", "always_last"]
    print(f"{'ops: scopes=[]':<28} {lean['ops']}")

    full = load(BODY_DOC, scopes=["extra=yes"])
    assert full["ops"] == ["always_first", "extra_a", "extra_b", 42, "always_last"]
    print(f"{'ops: extra=yes':<28} {full['ops']}")


if __name__ == "__main__":
    main()
