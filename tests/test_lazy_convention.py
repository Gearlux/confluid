"""Pins the "Lazy Initialization & Zero-Arg Construction" class-design convention.

These tests assert the convention's interactions WITH confluid's machinery (not just the style):

  * zero-arg construction works and does no functional work;
  * a value required only at use-time is validated lazily (in a property), never in ``__init__``;
  * derived state behind a read-only ``@property`` is invisible to the config surface — never set
    by ``configure``, never ``dump``ed, rebuilt after ``load()``;
  * a recomputing property reflects post-construction ``configure`` changes (never stale), while a
    cached property (private ``_backing``) materializes once;
  * fully-defaulted constructor params are all optional in the generated pydantic schema.

See confluid ``AGENTS.md`` → "Lazy Initialization & Zero-Arg Construction". The reference
implementation in the workspace is ``dataflux.sources.HuggingFaceSource``.
"""

from typing import Any, List, Optional

import pytest

from confluid import configurable, configure, dump, get_registry, load, to_pydantic


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


def test_zero_arg_construction_does_no_work() -> None:
    @configurable
    class Source:
        def __init__(self, path: str = "", split: str = "train") -> None:
            # Lazy: store config only — no functional work.
            self.path = path
            self.split = split
            self._data: Optional[List[int]] = None  # private lazy cache

    src = Source()  # zero-arg construction must succeed
    assert src.path == "" and src.split == "train"
    assert src._data is None  # nothing materialized at construction time


def test_required_at_use_value_validated_lazily_not_in_init() -> None:
    @configurable
    class Source:
        def __init__(self, path: str = "") -> None:
            self.path = path
            self._data: Optional[List[int]] = None

        @property
        def data(self) -> List[int]:
            if self._data is None:
                if not self.path:
                    raise ValueError("path is empty — set it before reading `data`.")
                self._data = [1, 2, 3]
            return self._data

    src = Source()  # no error at construction even though `path` is needed to function
    with pytest.raises(ValueError, match="path is empty"):
        _ = src.data
    src.path = "x"  # post-construction config
    assert src.data == [1, 2, 3]


def test_readonly_property_is_not_a_config_attr() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

        @property
        def weights(self) -> List[float]:
            return [0.0] * self.layers

    model = Model()
    # configure() walks the instance (which triggers the property getter) but must NOT try to
    # set the read-only property, and must not choke on it.
    configure(model, config={"Model": {"layers": 5, "weights": "should-be-ignored"}})
    assert model.layers == 5
    assert model.weights == [0.0] * 5  # recomputed from the CURRENT layers — never stale


def test_recompute_property_reflects_post_configuration() -> None:
    @configurable
    class Model:
        def __init__(self, scale: float = 1.0) -> None:
            self.scale = scale

        @property
        def calibrated(self) -> float:
            return self.scale * 2  # recomputes from current input

    model = Model()
    assert model.calibrated == 2.0
    configure(model, config={"Model": {"scale": 10.0}})
    assert model.calibrated == 20.0  # reflects the new config, not a frozen pre-config value


def test_cached_property_materializes_once() -> None:
    @configurable
    class Source:
        def __init__(self, path: str = "ds") -> None:
            self.path = path
            self._loads = 0
            self._data: Optional[List[int]] = None

        @property
        def data(self) -> List[int]:
            if self._data is None:
                self._loads += 1  # the "expensive" materialization
                self._data = [1, 2, 3]
            return self._data

    src = Source()
    assert src.data == [1, 2, 3]
    assert src.data == [1, 2, 3]
    assert src._loads == 1  # cached: the expensive work ran exactly once
    src._data = None  # resetting the private backing forces a reload
    _ = src.data
    assert src._loads == 2


def test_dump_load_roundtrips_without_derived_state() -> None:
    @configurable
    class Preprocessor:
        def __init__(self, mode: str = "standard", scale: float = 1.0) -> None:
            self.mode = mode
            self.scale = scale

        @property
        def fitted(self) -> dict:
            return {"mode": self.mode, "offset": self.scale * 2}

    original = Preprocessor(mode="minmax", scale=2.0)
    _ = original.fitted  # touch the derived property — it must still not leak into the dump
    state = dump(original)
    assert "fitted" not in state  # read-only derived property is not part of the config surface

    restored: Any = load(state)
    assert restored.mode == "minmax" and restored.scale == 2.0
    assert restored.fitted == {"mode": "minmax", "offset": 4.0}  # rebuilt lazily on the reconstruction


def test_fully_defaulted_params_are_optional_in_schema() -> None:
    @configurable
    class Source:
        def __init__(self, path: str = "", split: str = "train", count: Optional[int] = None) -> None:
            self.path = path
            self.split = split
            self.count = count

    model = to_pydantic(Source)
    assert set(model.model_fields) == {"path", "split", "count"}
    # Zero-arg construction ⇒ no required fields in the generated schema.
    assert model.model_json_schema().get("required", []) == []
