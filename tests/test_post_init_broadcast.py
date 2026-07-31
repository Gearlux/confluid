"""Broadcasting to post-init attributes (via AST scan of ``__init__``).

The rule: when Confluid materializes a ``@configurable`` class, top-level
scalar YAML keys that match ANY ``self.X = ...`` assignment in the class's
``__init__`` body get broadcast onto the instance via the existing
post-init injection path — even when ``X`` isn't a constructor parameter.
This closes the ergonomic gap where users had to duplicate a top-level
``loss_fn: !class:...`` as ``loss_fn: !ref:loss_fn`` inside the owning
class's block.
"""

import pytest

from confluid import Instance, configurable, flow, load, materialize
from confluid.loader import _get_acceptable_keys, _get_post_init_attrs

# ---------------------------------------------------------------------------
# Fixtures: module-level @configurable classes (AST source must be importable,
# so they can't live inside a test function's <locals>).
# ---------------------------------------------------------------------------


@configurable
class _Trainerish:
    """Mirrors the marainer.Trainer pattern: ctor has ``model`` only, but the
    body wires several post-init attributes that users may want to override."""

    def __init__(self, model: str = "default_model") -> None:
        self.model = model
        self.loss_fn = "default_loss"
        self.val_metrics = None
        self.experiment_name = "default_exp"


@configurable
class _NestedAssigns:
    """Tolerate conditional / walrus-style assignments in ``__init__`` body."""

    def __init__(self, flag: bool = False) -> None:
        self.flag = flag
        if flag:
            self.branch_a = 1
        else:
            self.branch_b = 2


@configurable
class _PrivatesIgnored:
    """Underscore-prefixed post-init names must NOT enter the broadcast set."""

    def __init__(self) -> None:
        self._private_cache = None
        self.public = "default"


@configurable
class _FauxLoss:
    """Stand-in for a real loss function so tests can assert Fluid-value broadcast."""

    def __init__(self, scale: float = 1.0) -> None:
        self.scale = scale


# ---------------------------------------------------------------------------
# _get_post_init_attrs — direct surface
# ---------------------------------------------------------------------------


def test_post_init_attrs_detected_from_body_assignments() -> None:
    attrs = _get_post_init_attrs(_Trainerish)
    # All four body assignments are detected, including model (which also
    # happens to be a ctor param; the union happens in _get_acceptable_keys).
    assert {"model", "loss_fn", "val_metrics", "experiment_name"}.issubset(attrs)


def test_post_init_attrs_detects_both_branches_of_conditional_assign() -> None:
    attrs = _get_post_init_attrs(_NestedAssigns)
    assert "flag" in attrs
    assert "branch_a" in attrs
    assert "branch_b" in attrs


def test_post_init_attrs_skips_private_names() -> None:
    attrs = _get_post_init_attrs(_PrivatesIgnored)
    assert "public" in attrs
    assert "_private_cache" not in attrs


def test_post_init_attrs_empty_for_classes_without_source() -> None:
    # Built-ins have no Python source for __init__ — must not raise, just
    # return an empty set.
    attrs = _get_post_init_attrs(dict)
    assert attrs == frozenset()


# ---------------------------------------------------------------------------
# _get_acceptable_keys — unions ctor params, class-level attrs, post-init attrs
# ---------------------------------------------------------------------------


def test_acceptable_keys_includes_post_init_attr_names() -> None:
    keys = _get_acceptable_keys(_Trainerish)
    assert keys is not None
    # Ctor param + post-init body attrs are all broadcast targets.
    assert {"model", "loss_fn", "val_metrics", "experiment_name"}.issubset(keys)


# ---------------------------------------------------------------------------
# End-to-end broadcasting: top-level scalar flows into post-init attr
# ---------------------------------------------------------------------------


def test_top_level_scalar_broadcasts_into_post_init_attribute() -> None:
    # ``experiment_name`` at top level should now reach ``_Trainerish`` even
    # though it's not a ctor parameter — only a post-init body assignment.
    yaml_text = (
        "experiment_name: my_run\n"
        f"trainer: !class:{_Trainerish.__module__}.{_Trainerish.__qualname__}\n"
        "  model: my_model\n"
    )
    cfg = load(yaml_text)
    trainer = flow(cfg["trainer"])
    assert trainer.model == "my_model"
    assert trainer.experiment_name == "my_run"
    # Post-init defaults we didn't override are preserved.
    assert trainer.loss_fn == "default_loss"


def test_post_init_broadcast_coexists_with_ctor_param_broadcast() -> None:
    yaml_text = (
        "model: broadcasted_model\n"  # ctor param
        "experiment_name: broadcasted_run\n"  # post-init attr
        f"trainer: !class:{_Trainerish.__module__}.{_Trainerish.__qualname__}\n"
    )
    cfg = load(yaml_text)
    trainer = flow(cfg["trainer"])
    assert trainer.model == "broadcasted_model"
    assert trainer.experiment_name == "broadcasted_run"


def test_post_init_broadcast_via_materialize() -> None:
    # The materialize entry point (what Liquify uses) must honor the same
    # broadcast rule.
    trainer_marker = Instance(f"{_Trainerish.__module__}.{_Trainerish.__qualname__}")
    trainer_marker.kwargs.update({"model": "m"})
    config = {
        "loss_fn": "custom_loss",
        "trainer": trainer_marker,
    }
    result = materialize(config, context=config)
    trainer = result["trainer"]
    assert trainer.loss_fn == "custom_loss"


def test_post_init_broadcast_does_not_override_explicit_kwarg() -> None:
    # Explicit values inside the class block win over top-level broadcasts —
    # same precedence that ctor params already enforce.
    yaml_text = (
        "experiment_name: broadcast_value\n"
        f"trainer: !class:{_Trainerish.__module__}.{_Trainerish.__qualname__}\n"
        "  experiment_name: explicit_value\n"
    )
    cfg = load(yaml_text)
    trainer = flow(cfg["trainer"])
    assert trainer.experiment_name == "explicit_value"


def test_post_init_broadcast_filters_lists_and_dicts() -> None:
    # Lists and dicts at top level are still excluded from broadcasting.
    # Lists are ambiguous vs collection attributes, and dicts at top level
    # are typically class-scope blocks (``ClassName: {...}``). Only scalars
    # and Fluid definitions broadcast.
    yaml_text = (
        "experiment_name: [1, 2, 3]\n"  # list, must not broadcast
        f"trainer: !class:{_Trainerish.__module__}.{_Trainerish.__qualname__}\n"
    )
    cfg = load(yaml_text)
    trainer = flow(cfg["trainer"])
    # The post-init default survives — the list did not broadcast.
    assert trainer.experiment_name == "default_exp"


def test_post_init_broadcast_carries_fluid_values() -> None:
    # The whole point of Option C: a top-level ``loss_fn: !class:...`` must
    # reach the owning Trainer's post-init ``loss_fn`` slot without the user
    # having to duplicate the reference under the trainer block.
    yaml_text = (
        f"loss_fn: !class:{_FauxLoss.__module__}.{_FauxLoss.__qualname__}\n"
        "  scale: 3.14\n"
        f"trainer: !class:{_Trainerish.__module__}.{_Trainerish.__qualname__}\n"
        "  model: my_model\n"
    )
    cfg = load(yaml_text)
    trainer = flow(cfg["trainer"])
    assert isinstance(trainer.loss_fn, _FauxLoss)
    assert trainer.loss_fn.scale == 3.14


# ---------------------------------------------------------------------------
# Non-@configurable classes are unaffected
# ---------------------------------------------------------------------------


class _NotConfigurable:
    def __init__(self, x: int = 0) -> None:
        self.x = x
        self.y = "default_y"  # post-init body attr


def test_non_configurable_classes_do_not_inherit_post_init_broadcast() -> None:
    # Post-init broadcast is gated on @configurable, matching the existing
    # class-attribute scan. Non-@configurable classes keep the old behavior:
    # only ctor params are broadcast targets.
    keys = _get_acceptable_keys(_NotConfigurable)
    assert keys is not None
    assert "x" in keys
    assert "y" not in keys


if __name__ == "__main__":
    pytest.main([__file__])
