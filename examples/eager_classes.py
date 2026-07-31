"""Eager classes (plain constructors) — the runnable companion to ``docs/eager-classes.md``.

Covers: loading a class with a REQUIRED param that does real work in ``__init__``,
the clear YAML-located error when a required param is missing, the dump round-trip
via captured constructor kwargs (live attribute preferred, capture as fallback),
direct-Python-construction capture, the ``eager=True`` staleness warning fired
by ``configure()``, and the ``capture=False`` opt-out for heavy constructor args.
"""

from typing import Any

from confluid import ConfluidError, configurable, configure, dump, load


@configurable(eager=True)
class Resampler:
    def __init__(self, rate: int) -> None:
        """A plain Python class: required param, real work at construction.

        Args:
            rate: Sample rate in Hz — consumed immediately, not stored verbatim.
        """
        self._taps = [1.0 / rate] * 4  # "designs a filter" from the param


@configurable(eager=True)
class Mixed:
    def __init__(self, kept: int = 1, transformed: int = 2) -> None:
        """One convention-style param (stored verbatim) and one eager param.

        Args:
            kept: Stored under its own name — the live attribute wins in dumps.
            transformed: Consumed at construction — dumps via the captured kwarg.
        """
        self.kept = kept
        self._t = 10 * transformed


@configurable(capture=False)
class Embedder:
    def __init__(self, corpus: Any = None, dim: int = 32) -> None:
        """A class whose heavy ctor arg must NOT be kept alive by the kwargs capture.

        Args:
            corpus: A large, disposable input — consumed at construction.
            dim: Embedding size — stored verbatim, still dumps.
        """
        self.dim = dim
        self._index_size = len(corpus) * dim if corpus else 0  # "builds an index"


def main() -> None:
    # --- Loading just works: required params go straight to __init__ --------------------
    cfg = load("resampler: !class:Resampler()\n  rate: 48000")
    resampler = cfg["resampler"]
    assert resampler._taps == [1.0 / 48000] * 4
    print(f"loaded eager class, filter designed at construction: {resampler._taps[0]:.2e}")

    # --- A missing required param fails with a located, legible error -------------------
    try:
        load("resampler: !class:Resampler()")
    except ConfluidError as exc:
        print(f"missing required param -> {type(exc).__name__}: {exc}")

    # --- Dump round-trip: live attribute preferred, captured kwargs as fallback ---------
    mixed = load("mixed: !class:Mixed()\n  kept: 5\n  transformed: 7")["mixed"]
    mixed.kept = 99  # post-construction change to a stored param survives the dump
    text = dump(mixed)
    assert "kept: 99" in text and "transformed: 7" in text
    print("dump of a transforming constructor:")
    print(text)
    reloaded = load(text)
    assert reloaded.kept == 99 and reloaded._t == 70

    # --- Direct Python construction captures kwargs too (positionals normalized) --------
    direct = Resampler(8000)
    captured = getattr(direct, "__confluid_kwargs__")
    assert captured == {"rate": 8000}
    assert "rate: 8000" in dump(direct)
    print("direct construction captured:", captured)

    # --- The eager=True staleness warning: applied, but the work does not re-run --------
    configure(mixed, config={"kept": 3})  # ctor param on an eager class -> logs a warning
    assert mixed.kept == 3
    assert mixed._t == 70  # __init__ work did NOT re-run — exactly what the warning says
    print("configure() applied the value; derived state untouched (see warning above)")

    # --- capture=False: heavy disposable ctor args are not kept alive -------------------
    embedder = Embedder(corpus=["a"] * 1000, dim=8)
    assert not hasattr(embedder, "__confluid_kwargs__")  # no capture — corpus is collectable
    text = dump(embedder)
    assert "dim: 8" in text  # verbatim-stored params still dump via the live attribute
    assert "corpus" not in text  # transformed params are omitted — reload uses the default
    print("capture=False dump (corpus omitted, dim kept):")
    print(text)


if __name__ == "__main__":
    main()
