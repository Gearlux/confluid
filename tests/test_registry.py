import pytest
from confluid import configurable, get_registry, register


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


def test_object_registration() -> None:
    obj = {"key": "value"}
    get_registry().register_object(obj, "MyObj")
    assert get_registry().get_object("MyObj") is obj
