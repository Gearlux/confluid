import functools
import sys
import threading

import pytest

from confluid import configurable, flow, get_registry, register
from confluid.exceptions import ConfigurableDefinitionError
from confluid.fluid import Target


@pytest.fixture(autouse=True)
def clear_registry() -> None:
    get_registry().clear()


def test_class_registration() -> None:
    @configurable
    class MyModel:
        pass

    assert "MyModel" in get_registry().list_classes()
    assert get_registry().get_class("MyModel") is MyModel


def test_class_registration_with_name() -> None:
    @configurable(name="CustomName")
    class MyModel:
        pass

    assert "CustomName" in get_registry().list_classes()
    assert get_registry().get_class("CustomName") is MyModel


def test_third_party_registration() -> None:
    class ExternalModel:
        pass

    register(ExternalModel, name="Ext")
    assert "Ext" in get_registry().list_classes()
    assert get_registry().get_class("Ext") is ExternalModel


# ---------------------------------------------------------------------------
# Registration refuses what it cannot name or bind (BUGS-2026-08-19 R1/R2/R3),
# and enumeration is safe against a concurrent registration (R9).
# ---------------------------------------------------------------------------


def _base_fn(x: int = 1, y: int = 2) -> tuple:
    return (x, y)


def test_a_positional_string_to_configurable_is_refused_naming_the_keyword() -> None:
    """R1 — `@configurable("Named")` crashed with a raw AttributeError from an
    unrelated line ('str' object has no attribute '__name__')."""
    with pytest.raises(ConfigurableDefinitionError, match="name="):
        configurable("Named")  # type: ignore[call-overload]  # the typo under test


def test_a_target_with_no_name_is_refused_unless_a_name_is_given() -> None:
    """R1/R2 — a functools.partial / callable instance has no __name__: register()
    crashed raw, and the decorator silently registered the validation wrapper under
    the literal name 'wrapper', every second one clobbering the first."""

    class _CallableObj:
        def __call__(self, x: int = 1) -> int:
            return x

    with pytest.raises(ConfigurableDefinitionError, match="__name__"):
        register(functools.partial(_base_fn, y=5))
    with pytest.raises(ConfigurableDefinitionError, match="__name__"):
        configurable(functools.partial(_base_fn, y=5))
    with pytest.raises(ConfigurableDefinitionError, match="__name__"):
        configurable(_CallableObj())
    assert get_registry().get_class("wrapper") is None, "nothing may land under the wrapper's own name"


def test_a_named_nameless_target_still_registers_and_flows() -> None:
    """The con: a provided name= always suffices — this worked before and must keep working."""
    register(functools.partial(_base_fn, y=5), name="curried")
    assert flow(Target("curried", x=3)) == (3, 5)


def test_a_function_literally_named_wrapper_still_registers() -> None:
    """The false-positive guard: the refusal tests for a MISSING __name__ on the
    original callable, never for the name's text."""

    @configurable
    def wrapper(x: int = 1) -> int:
        return x

    assert get_registry().get_class("wrapper") is not None


def test_configurable_above_a_staticmethod_or_classmethod_is_refused() -> None:
    """R3 — wrapping the descriptor made a plain function (instance calls broke) or
    registered an uncallable classmethod object; the refusal names the working order."""
    with pytest.raises(ConfigurableDefinitionError, match="UNDER @staticmethod"):

        class _HolderS:
            @configurable
            @staticmethod
            def make(x: int = 1) -> int:
                return x

    with pytest.raises(ConfigurableDefinitionError, match="UNDER @classmethod"):

        class _HolderC:
            @configurable
            @classmethod
            def mk(cls, x: int = 1) -> int:
                return x


def test_the_working_decorator_order_keeps_working() -> None:
    """The con for R3: @configurable applied to the FUNCTION, the descriptor outermost."""

    class _Holder:
        @staticmethod
        @configurable
        def build_thing(x: int = 1) -> int:
            return x

    assert _Holder.build_thing(2) == 2
    assert _Holder().build_thing(3) == 3
    assert flow(Target("build_thing", x=4)) == 4


def test_enumeration_survives_a_concurrent_registration() -> None:
    """R9 — `list_classes()` iterated the LIVE index dicts, so a concurrent
    registration raised `RuntimeError: dictionary changed size during iteration`
    (an MCP server enumerating while a lazy import registers). The tiny switch
    interval forces thread switches inside the enumeration; the snapshots make
    the outcome independent of scheduling."""
    registry = get_registry()
    errors: list = []
    done = [False]
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:

        def registrar() -> None:
            try:
                for i in range(3000):
                    cls = type(f"_R9Dyn{i}", (), {"__init__": lambda self, x=1: None})
                    cls.__module__ = "r9dyn"
                    register(cls, category="r9cat")
            finally:
                done[0] = True

        def lister() -> None:
            while not done[0] and not errors:
                try:
                    registry.list_classes()
                    registry.list_classes(category="r9cat")
                    registry.list_categories()
                except Exception as exc:  # noqa: BLE001 — the failure IS the finding
                    errors.append(repr(exc))

        threads = [threading.Thread(target=registrar), threading.Thread(target=lister)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(old_interval)
    assert not errors, errors[0]
