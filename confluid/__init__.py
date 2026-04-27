"""
Confluid: Modern, hierarchical configuration and dependency injection.
"""

from confluid.configurator import configure
from confluid.decorators import configurable, ignore_config, readonly_config, register
from confluid.dumper import dump
from confluid.fluid import Class, Clone, Fluid, Instance, Reference, flow, format_yaml_loc
from confluid.lazy import Lazy, is_lazy_annotation, lazy_param_names
from confluid.loader import load, load_config, materialize
from confluid.merger import deep_merge, expand_dotted_keys
from confluid.registry import get_registry
from confluid.resolver import parse_value
from confluid.schema import get_hierarchy, get_hierarchy_from_instance
from confluid.scopes import resolve_scopes

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
    "dump",
    "configure",
    "Fluid",
    "Class",
    "Clone",
    "Instance",
    "Reference",
    "flow",
    "format_yaml_loc",
    "Lazy",
    "is_lazy_annotation",
    "lazy_param_names",
    "get_hierarchy",
    "get_hierarchy_from_instance",
]
