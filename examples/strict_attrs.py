"""Closing the config surface — ``@configurable(strict_attrs=True)``.

Runnable companion of ``docs/strict-attrs.md``. Confluid is PERMISSIVE by
default: an addressed key the class declares nowhere still lands as a post-init
attribute (warning as it does, since 2026-08-12). ``strict_attrs=True`` is the
opt-in that closes the surface — the same key becomes a located
``ConfigurationError`` instead.

Asserts the four behaviours the mark pins (a print-only script would exit 0
while demonstrating the wrong thing):

1. the permissive default APPLIES an undeclared addressed key;
2. a strict class REFUSES it, naming what it does declare;
3. a BARE key never reaches the gate — a sweep document keeps loading;
4. declared params/body slots are untouched by the mark.
"""

from confluid import ConfigurationError, configurable, load, marks


@configurable
class OpenCache:
    """The permissive default — an undeclared key lands (with a warning)."""

    def __init__(self, path: str = "/tmp/cache") -> None:
        self.path = path


@configurable(strict_attrs=True)
class ClosedCache:
    """The closed surface — an undeclared key is refused."""

    def __init__(self, path: str = "/tmp/cache", size_mb: int = 64) -> None:
        self.path = path
        self.size_mb = size_mb


def main() -> None:
    # 1. The permissive default: the typo'd key is APPLIED as a post-init
    #    attribute (and warned about — that is the B1 behaviour, not silence).
    open_cache = load("cache: {_target_: OpenCache, pathh: /x}")["cache"]
    assert open_cache.pathh == "/x"
    assert open_cache.path == "/tmp/cache"  # the real param kept its default
    print("permissive default: 'pathh' applied as an attribute (with a warning)")

    # 2. The strict class refuses the same spelling, naming its surface.
    try:
        load("cache: {_target_: ClosedCache, pathh: /x}")
    except ConfigurationError as exc:
        assert "pathh" in str(exc) and "path, size_mb" in str(exc)
        print(f"strict_attrs=True: refused — {exc}")
    else:
        raise AssertionError("an undeclared key on a strict class must raise")

    # 3. A BARE key never reaches the gate: it cascades tree-wide, matches
    #    nothing on ClosedCache, and the document still loads.
    result = load("unrelated_sweep_knob: 7\ncache: {_target_: ClosedCache}")
    assert result["cache"].size_mb == 64
    print("bare keys: a sweep document still loads around a strict class")

    # 4. Declared slots are untouched by the mark.
    cache = load("cache: {_target_: ClosedCache, size_mb: 128}")["cache"]
    assert cache.size_mb == 128
    assert marks(ClosedCache).strict_attrs is True
    print("declared params still configure; marks(ClosedCache).strict_attrs is True")


if __name__ == "__main__":
    main()
