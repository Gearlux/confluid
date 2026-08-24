"""Round-trip reproducibility with lazy, zero-arg ``@configurable`` classes.

Shows that the lazy-init convention (``docs/class-design.md``) and full-hierarchy
serialization (``docs/serialization.md``) compose cleanly:

  * ``Preprocessor()`` / ``Pipeline()`` are **zero-arg constructible** (every field defaulted);
  * ``Preprocessor.fitted_params`` is **derived state behind a read-only cached property** — it is
    therefore *not* part of the config surface, so ``dump()`` omits it and ``load()`` rebuilds it
    lazily on the reconstructed object. The serialized form carries only the inputs (``mode`` /
    ``scale`` / ``name`` / ``steps``), which is exactly what makes the round-trip reproducible.
"""

from typing import Any, Dict, List, Optional

from confluid import configurable, dump, load


@configurable
class Preprocessor:
    def __init__(self, mode: str = "standard", scale: float = 1.0) -> None:
        # Partial constructor: only stores config (both knobs defaulted → ``Preprocessor()`` works).
        self.mode = mode
        self.scale = scale
        self._fitted: Optional[Dict[str, Any]] = None  # lazy derived state — see ``fitted_params``

    @property
    def fitted_params(self) -> Dict[str, Any]:
        """Calibration derived lazily from (``mode``, ``scale``) and cached.

        Read-only cached property → NOT part of the config surface, so ``dump()`` omits it and
        ``load()`` rebuilds it on demand. Resetting ``_fitted`` to ``None`` recomputes.
        """
        if self._fitted is None:
            self._fitted = {"mode": self.mode, "offset": self.scale * 2}
        return self._fitted

    def __repr__(self) -> str:
        return f"Preprocessor(mode='{self.mode}', scale={self.scale})"


@configurable
class Pipeline:
    def __init__(self, name: str = "", steps: Optional[List[Any]] = None) -> None:
        # Zero-arg constructible: ``steps`` uses a None sentinel → fresh [] (no shared-mutable-default
        # trap), and no work happens in the constructor.
        self.name = name
        self.steps = steps if steps is not None else []

    def __repr__(self) -> str:
        return f"Pipeline(name='{self.name}', steps={self.steps})"


def main() -> None:
    # Zero-arg construction works — nothing functional happens in either constructor.
    print("--- Zero-Arg Construction ---")
    print(Pipeline())  # Pipeline(name='', steps=[])

    # 1. Create a complex live hierarchy
    p1 = Preprocessor(mode="minmax", scale=2.0)
    p2 = Preprocessor(mode="robust", scale=0.5)
    original_pipeline = Pipeline(name="ProductionPipeline", steps=[p1, p2])

    print("\n--- Original Hierarchy ---")
    print(original_pipeline)
    # Touch the derived property so we can prove it does NOT leak into the dump below.
    print(f"p1 fitted (derived): {p1.fitted_params}")

    # 2. Dump the entire state to YAML — only the inputs are serialized, not the derived cache.
    state_yaml = dump(original_pipeline)
    print("\n--- Exported YAML State ---")
    print(state_yaml)
    assert "fitted" not in state_yaml  # derived, read-only property is not persisted

    # 3. Reconstruct exactly in one line
    reconstructed_pipeline = load(state_yaml)

    print("--- Reconstructed Hierarchy ---")
    print(reconstructed_pipeline)

    # 4. Verify equality + that derived state rebuilds lazily on the reconstructed object
    assert reconstructed_pipeline.name == original_pipeline.name
    assert len(reconstructed_pipeline.steps) == len(original_pipeline.steps)
    assert reconstructed_pipeline.steps[0].mode == "minmax"
    assert reconstructed_pipeline.steps[0].fitted_params == {"mode": "minmax", "offset": 4.0}
    print("\n[SUCCESS] Hierarchy reconstructed with 100% fidelity; derived state rebuilt lazily.")


class _Series:
    """A third-party value type — not configurable, no registry entry."""

    def __init__(self, values: list) -> None:
        self.values = list(values)


def _series_from_values(values: list) -> "_Series":
    """The reload target of the spelling below — an ordinary callable."""
    return _Series(values)


def dump_spellings() -> None:
    """`register_dump_spelling` — a faithful spelling for a value the dumper cannot respell.

    Without it, an opaque value degrades to a bare `{_target_: X}` placeholder that
    reloads DEFAULT-constructed. The registered recipe emits an ordinary marker, so
    the reload needs nothing beyond the load machinery that already exists.
    """
    from confluid import Target, register, register_dump_spelling

    @configurable(name="SeriesHost")
    class SeriesHost:
        def __init__(self) -> None:
            self.payload: object = None

    register(_series_from_values, name="SeriesFromValues")
    register_dump_spelling(_Series, lambda s: Target(_series_from_values, values=s.values))

    host = SeriesHost()
    host.payload = _Series([0.25, 0.5, 1.0])
    text = dump(host)
    assert "SeriesFromValues" in text and "_Series" not in text, "the placeholder is gone"
    reloaded = load(text)
    assert isinstance(reloaded.payload, _Series) and reloaded.payload.values == [0.25, 0.5, 1.0]
    print("[SUCCESS] registered dump spelling: the opaque value round-trips as its VALUES.")


if __name__ == "__main__":
    main()
    dump_spellings()
