"""
Confluid: Modern, hierarchical configuration and dependency injection for Python.
"""

from confluid.configurator import configure
from confluid.decorators import configurable, register
from confluid.dumper import dump
from confluid.fluid import Fluid, flow
from confluid.loader import load, load_config
from confluid.registry import get_registry
from confluid.resolver import Resolver

__all__ = [
    "configurable",
    "register",
    "get_registry",
    "Resolver",
    "configure",
    "load_config",
    "dump",
    "Fluid",
    "flow",
    "load",
]
