"""
Confluid: Modern, hierarchical configuration and dependency injection.
"""

from confluid.configurator import configure
from confluid.decorators import configurable, ignore_config, readonly_config, register
from confluid.dumper import dump
from confluid.env import load_workspace_env
from confluid.fluid import Class, Clone, Fluid, Instance
from confluid.fluid import Lazy as LazyClass
from confluid.fluid import Reference, ScopeBlock, cast, flow, format_yaml_loc
from confluid.lazy import Lazy, is_lazy_annotation, lazy_param_names
from confluid.loader import get_configurable_attrs, load, load_config, load_config_with_paths, materialize, resolve
from confluid.merger import deep_merge, expand_dotted_keys
from confluid.pydantic_export import confluid_class_of, lazy_param_names_of, to_pydantic
from confluid.registry import get_registry
from confluid.resolver import parse_value
from confluid.schema import get_hierarchy, get_hierarchy_from_instance, parse_param_docs, shortest_unique_paths
from confluid.scopes import discover_dimensions, normalize_active, parse_scope_arg, resolve_scopes
from confluid.validation import (
    ValidationMode,
    ValidationPolicy,
    get_policy,
    override_init_mode,
    reset_policy,
    set_policy,
    validate_kwargs,
    validate_model,
    validate_setattr,
)

__all__ = [
    "configurable",
    "register",
    "ignore_config",
    "readonly_config",
    "get_registry",
    "load",
    "load_config",
    "load_config_with_paths",
    "materialize",
    "resolve",
    "deep_merge",
    "expand_dotted_keys",
    "parse_value",
    "dump",
    "load_workspace_env",
    "configure",
    "Fluid",
    "Class",
    "Clone",
    "Instance",
    "Reference",
    "ScopeBlock",
    "flow",
    "cast",
    "format_yaml_loc",
    "Lazy",
    "LazyClass",
    "is_lazy_annotation",
    "lazy_param_names",
    "get_hierarchy",
    "get_hierarchy_from_instance",
    "parse_param_docs",
    "shortest_unique_paths",
    "get_configurable_attrs",
    "to_pydantic",
    "confluid_class_of",
    "lazy_param_names_of",
    "discover_dimensions",
    "normalize_active",
    "parse_scope_arg",
    "resolve_scopes",
    "ValidationMode",
    "ValidationPolicy",
    "get_policy",
    "set_policy",
    "reset_policy",
    "override_init_mode",
    "validate_kwargs",
    "validate_setattr",
    "validate_model",
]
