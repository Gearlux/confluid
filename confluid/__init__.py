"""
Confluid: Modern, hierarchical configuration and dependency injection for Python.
"""

from confluid.configurator import configure
from confluid.decorators import configurable, ignore_config, readonly_config, register
from confluid.dumper import dump
from confluid.fluid import Fluid
from confluid.loader import load, load_config
from confluid.merger import deep_merge, expand_dotted_keys
from confluid.parser import parse_value
from confluid.registry import get_registry
from confluid.schema import get_hierarchy
from confluid.scopes import resolve_scopes
from confluid.solidify import solidify

__all__ = [
    "configurable",
    "register",
    "ignore_config",
    "readonly_config",
    "get_registry",
    "get_hierarchy",
    "parse_value",
    "configure",
    "dump",
    "Fluid",
    "solidify",
    "load",
    "load_config",
    "resolve_scopes",
    "deep_merge",
    "expand_dotted_keys",
]
