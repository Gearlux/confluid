"""Scopes — companion to ``docs/scopes.md``.

One document, three activations: no scopes, ``debug``, and ``task=classification``.
``!notscope:`` demonstrates the *unset => active* convention.

The second half shows a scope block nested INSIDE a ``!class:`` marker — the way
to offer an alternative for a single slot without lifting it to the document
root. A marker's kwargs are a mapping like any other, so the wrapper splices at
its own position and the later write wins.

The last part asks a document which values it offers, and shows what happens
when you ask for one it does not have.
"""

from typing import Any, Dict

import yaml

from confluid import ScopeError, configurable, discover_dimension_values, load
from confluid.loader import ConfluidLoader

DOC = """
log_level: INFO
if_debug: !scope:debug
  log_level: DEBUG
unless_debug: !notscope:debug
  log_level: WARNING
if_classification: !scope:task=classification
  head: classifier
"""


ALIAS_DOC = """
scope_aliases:
  ci: [quick, verbose]
max_epochs: 50
log_level: INFO
quick_mode: !scope:quick
  max_epochs: 1
logging: !scope:verbose
  log_level: DEBUG
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
  model: !class:TimmModel
  alt: !scope:model=convnet
    model: !class:ConvNet
"""

#: The three body shapes. A SEQUENCE body extends the surrounding list (the only
#: way to write a conditional list ITEM); a SCALAR body substitutes one entry.
BODY_DOC = """
ops:
  - always_first
  - !scope:extra=yes
    - extra_a
    - extra_b
  - !scope:extra=yes
    - 42
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
    default_slot = load(SLOT_DOC, until="document")["runnable"]
    assert default_slot.kwargs["model"].target == "TimmModel"
    assert "alt" not in default_slot.kwargs  # the inactive wrapper is dropped
    print(f"{'slot: scopes=[]':<28} model={default_slot.kwargs['model'].target}")

    swapped = load(SLOT_DOC, until="document", scopes=["model=convnet"])["runnable"]
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

    # What does this document offer? Ask the RAW parse — `load()` has already
    # spliced the blocks away, so there is nothing left to discover in its result.
    raw = yaml.load(DOC, Loader=ConfluidLoader)
    offered = discover_dimension_values(raw)
    assert offered == {"task": {"classification"}}
    print(f"{'declared dimensions':<28} {offered}")

    # An alias activates a BUNDLE of boolean scopes under one name; chains
    # expand recursively, and the metadata keys never reach the result.
    aliased = load(ALIAS_DOC, scopes=["ci"])
    assert aliased["max_epochs"] == 1 and aliased["log_level"] == "DEBUG"
    assert "scope_aliases" not in aliased
    print(f"{'alias ci -> quick+verbose':<28} max_epochs={aliased['max_epochs']} log_level={aliased['log_level']}")

    # Asking for a value it does NOT offer is an error naming the ones it does,
    # rather than a silent fall-through to the unscoped defaults.
    try:
        load(DOC, scopes=["task=classifcation"])  # typo, on purpose
    except ScopeError as exc:
        print(f"{'typo rejected':<28} {exc}")
    else:  # pragma: no cover - the raise is the point of the example
        raise AssertionError("an undeclared scope value must raise")


if __name__ == "__main__":
    main()
