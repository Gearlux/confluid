"""
Confluid: Modern, hierarchical configuration and dependency injection.
"""

from confluid.configurator import configure
from confluid.decorators import configurable, ignore_config, readonly_config, register
from confluid.dumper import dump
from confluid.fluid import Fluid, flow
from confluid.loader import load, load_config, materialize
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
    "load",
    "load_config",
    "materialize",
    "resolve_scopes",
    "deep_merge",
    "expand_dotted_keys",
    "parse_value",
    "solidify",
    "dump",
    "configure",
    "Fluid",
    "flow",
    "get_hierarchy",
]
