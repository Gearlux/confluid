"""``dump(defaults=True)``: an unbuilt marker is emitted with every parameter its target gives a default.

A built object already dumps every declared slot; a marker — a deferred ``_partial_: true`` slot the run builds later,
or any marker dumped before construction — dumps only what it was given. With ``defaults=True`` it also shows each
signature parameter it was not given that has a default the document can spell (``None``, a bool, a number, a
string, or a list / mapping of those; a tuple as a list). A parameter without a default (an optimizer's ``params``,
passed in by the run), ``*args`` / ``**kwargs``, and a default no document can spell (a sentinel object, a function)
are left out. The marker itself is never changed, and the default stays off.
"""

from typing import Any, Dict, List, Optional

import yaml

import confluid
from confluid import PartialClass, Target, configurable, dump

_SENTINEL = object()


def build_widget(size: int = 3, label: str = "w") -> Dict[str, Any]:
    """A builder function target."""
    return {"size": size, "label": label}


@configurable
class DeferredOptimizer:
    def __init__(
        self,
        params: Any,
        lr: float = 0.001,
        betas: tuple = (0.9, 0.999),
        name: Optional[str] = None,
        groups: Optional[List[str]] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.params = params
        self.lr = lr
        self.betas = betas
        self.name = name
        self.groups = groups


class Unregistered:
    """A plain class no registry knows, named by its dotted path."""

    def __init__(self, depth: int = 2, marker: object = _SENTINEL, hook: Any = build_widget, width: int = 7) -> None:
        self.depth = depth
        self.width = width
        self.marker = marker
        self.hook = hook


@configurable
class Host:
    def __init__(self, optimizer: Any = None, epochs: int = 1) -> None:
        self.optimizer = optimizer
        self.epochs = epochs


def _body(text: str, key: str) -> Dict[str, Any]:
    body: Dict[str, Any] = yaml.safe_load(text)[key]
    return body


def test_a_partial_marker_shows_every_default_it_was_not_given() -> None:
    marker = PartialClass(DeferredOptimizer, lr=0.01)
    body = _body(dump({"optimizer": marker}, defaults=True), "optimizer")
    assert body == {
        "_target_": "DeferredOptimizer",
        "_partial_": True,
        "lr": 0.01,  # written: kept
        "betas": [0.9, 0.999],  # a tuple default, as a list
        "name": None,
        "groups": None,
    }


def test_without_the_option_a_marker_shows_only_what_it_was_given() -> None:
    marker = PartialClass(DeferredOptimizer, lr=0.01)
    assert _body(dump({"optimizer": marker}), "optimizer") == {
        "_target_": "DeferredOptimizer",
        "_partial_": True,
        "lr": 0.01,
    }


def test_the_marker_itself_is_not_changed() -> None:
    marker = PartialClass(DeferredOptimizer, lr=0.01)
    dump({"optimizer": marker}, defaults=True)
    assert marker.kwargs == {"lr": 0.01}


def test_a_target_named_by_its_dotted_path_is_imported_for_its_defaults() -> None:
    marker = Target(f"{__name__}.Unregistered")
    body = _body(dump({"thing": marker}, defaults=True), "thing")
    # `marker` (a sentinel object) and `hook` (a function) are defaults no document can spell.
    assert body == {"_target_": f"{__name__}.Unregistered", "depth": 2, "width": 7}


def test_a_builder_function_target_shows_its_defaults() -> None:
    body = _body(dump({"w": PartialClass(build_widget)}, defaults=True), "w")
    assert body["size"] == 3
    assert body["label"] == "w"


def test_a_target_that_resolves_to_nothing_is_dumped_as_given() -> None:
    marker = Target("no_such_module.NoSuchClass", depth=1)
    assert _body(dump({"thing": marker}, defaults=True), "thing") == {
        "_target_": "no_such_module.NoSuchClass",
        "depth": 1,
    }


def test_a_marker_inside_a_built_object_gets_its_defaults() -> None:
    built = confluid.load(
        "host:\n  _target_: Host\n  epochs: 4\n  optimizer:\n    _target_: DeferredOptimizer\n    _partial_: true\n"
    )
    host = _body(dump(built, defaults=True), "host")
    assert host["epochs"] == 4
    assert host["optimizer"]["lr"] == 0.001
    assert "params" not in host["optimizer"]


def test_a_marker_nested_in_a_partials_arguments_gets_its_defaults() -> None:
    marker = PartialClass(Host, optimizer=Target(DeferredOptimizer))
    body = _body(dump({"host": marker}, defaults=True), "host")
    assert body["epochs"] == 1
    assert body["optimizer"]["betas"] == [0.9, 0.999]


def test_a_built_object_of_a_plain_class_shows_its_defaults() -> None:
    built = confluid.load(f"thing:\n  _target_: {__name__}.Unregistered\n  depth: 5\n")
    assert _body(dump(built), "thing") == {"_target_": f"{__name__}.Unregistered", "depth": 5}
    assert _body(dump(built, defaults=True), "thing") == {
        "_target_": f"{__name__}.Unregistered",
        "depth": 5,
        "width": 7,
    }


def test_the_dump_with_defaults_reloads_to_the_same_document() -> None:
    document = {"optimizer": PartialClass(DeferredOptimizer, lr=0.01)}
    first = dump(document, defaults=True)
    again = dump(confluid.load(first, until="settled"), defaults=True)
    assert again == first
    rebuilt = confluid.flow(confluid.load(first)["optimizer"], params=[1, 2])
    assert (rebuilt.lr, rebuilt.betas, rebuilt.params) == (0.01, [0.9, 0.999], [1, 2])
