"""``dump_python``: an object graph written as the Python script that builds it again.

Builds a small graph (one ``Tone`` shared by two slots), writes it with ``dump()`` and with ``dump_python()``, runs the
script, and asserts the script built the same graph — ``dump()`` of it equals ``dump()`` of the original — and kept the
shared tone one object. Then shows the refusal for a value no script can build. Guide: ``README.md`` beside this file.
"""

from typing import Any, Dict

from shapes import Mixture, Tone  # type: ignore[import-not-found]  # run.py's own folder, on the path when it runs

from confluid import PythonDumpError, dump, dump_python


def main() -> None:
    carrier = Tone(frequency_hz=2.4e9, level_db=-30.0)
    mixture = Mixture(tones=[carrier, Tone(frequency_hz=2.41e9)], duration_s=0.004, reference=carrier)

    script = dump_python(mixture, name="mixture", header="The mixture of the python_script example.")
    print(script)

    namespace: Dict[str, Any] = {}
    exec(compile(script, "mixture.py", "exec"), namespace)
    rebuilt = namespace["mixture"]
    assert dump(rebuilt) == dump(mixture), "the script builds the same graph"
    assert rebuilt.reference is rebuilt.tones[0], "a shared object stays one object"
    assert script.count("Tone(frequency_hz=2400000000.0") == 1, "written once, as a variable"

    mixture.reference = object()  # type: ignore[assignment]  # not configurable: nothing could rebuild it
    try:
        dump_python(mixture, name="mixture")
    except PythonDumpError as refused:
        print(f"refused: {refused}")
        assert "mixture.reference" in str(refused)
    else:
        raise AssertionError("an object no script can build must be refused")


if __name__ == "__main__":
    main()
