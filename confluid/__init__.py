"""
Confluid: Modern, hierarchical configuration and dependency injection for Python.
"""

from confluid.configurator import configure
from confluid.decorators import configurable, register
from confluid.dumper import dump
from confluid.fluid import Fluid, flow
from confluid.loader import load, load_config
from confluid.merger import deep_merge
from confluid.registry import get_registry
from confluid.scopes import resolve_scopes

__all__ = [
    "configurable",
    "register",
    "get_registry",
    "configure",
    "dump",
    "Fluid",
    "flow",
    "load",
    "load_config",
    "resolve_scopes",
    "deep_merge",
]
