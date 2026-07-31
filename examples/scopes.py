"""Scopes — companion to ``docs/scopes.md``.

One document, three activations: no scopes, ``debug``, and ``task=classification``.
``!notscope:`` demonstrates the *unset => active* convention.
"""

from typing import Any, Dict

from confluid import load

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


if __name__ == "__main__":
    main()
