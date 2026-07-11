"""
Confluid: Modern, hierarchical configuration and dependency injection.

The pydantic-powered schema-export API (``to_pydantic``, ``confluid_class_of``)
is exposed lazily via :pep:`562` ``__getattr__`` so importing confluid never
requires pydantic — it is the optional ``confluid[pydantic]`` extra. Accessing
those names without pydantic installed raises an ``ImportError`` naming the
extra.

``__all__`` is the CURATED public surface (pruned 2026-07): internal
machinery (validation plumbing, scope resolution, annotation predicates,
marker internals) stays importable from its home module but is deliberately
not re-exported here.
"""

from typing import TYPE_CHECKING, Any

from confluid.configurator import configure, configure_from_file
from confluid.decorators import configurable, ignore_config, output, register
from confluid.dumper import dump
from confluid.engine import cast, flow, get_configurable_attrs, materialize, resolve
from confluid.exceptions import (
    CircularIncludeError,
    ConfigFileNotFoundError,
    ConfigurableDefinitionError,
    ConfigurationError,
    ConfluidError,
    ConstructionError,
    IntrospectionError,
    ReferenceResolutionError,
    ScopeError,
    UnknownClassError,
    ValidationModeError,
    WorkspaceEnvError,
)
from confluid.fluid import Class, Clone, Fluid, Instance
from confluid.fluid import Lazy as LazyClass
from confluid.fluid import Reference, format_yaml_loc
from confluid.lazy import Lazy, lazy_param_names
from confluid.llm_schema import sanitize_schema
from confluid.loader import load, load_config, load_config_with_paths
from confluid.mandatory import Mandatory, mandatory_param_names
from confluid.merger import deep_merge, expand_dotted_keys
from confluid.registry import get_registry
from confluid.resolver import parse_value
from confluid.schema import (
    InputSpec,
    OutputSpec,
    get_hierarchy,
    get_hierarchy_from_instance,
    input_specs,
    output_specs,
    parse_param_docs,
    shortest_unique_paths,
)
from confluid.scopes import discover_dimensions
from confluid.validation import ValidationMode, ValidationPolicy, get_policy, reset_policy, set_policy, validate_model

__all__ = [
    "ConfluidError",
    "ConfigurationError",
    "CircularIncludeError",
    "ReferenceResolutionError",
    "UnknownClassError",
    "ConfigurableDefinitionError",
    "ValidationModeError",
    "ScopeError",
    "ConfigFileNotFoundError",
    "ConstructionError",
    "WorkspaceEnvError",
    "IntrospectionError",
    "configurable",
    "register",
    "ignore_config",
    "output",
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
    "configure",
    "configure_from_file",
    "Fluid",
    "Class",
    "Clone",
    "Instance",
    "Reference",
    "flow",
    "cast",
    "format_yaml_loc",
    "Lazy",
    "LazyClass",
    "lazy_param_names",
    "Mandatory",
    "mandatory_param_names",
    "get_hierarchy",
    "get_hierarchy_from_instance",
    "input_specs",
    "output_specs",
    "InputSpec",
    "OutputSpec",
    "parse_param_docs",
    "shortest_unique_paths",
    "get_configurable_attrs",
    "to_pydantic",
    "confluid_class_of",
    "discover_dimensions",
    "ValidationMode",
    "ValidationPolicy",
    "get_policy",
    "set_policy",
    "reset_policy",
    "validate_model",
    "sanitize_schema",
]

if TYPE_CHECKING:
    from confluid.pydantic_export import confluid_class_of, to_pydantic

# Names served lazily from ``confluid.pydantic_export`` (requires the
# ``confluid[pydantic]`` extra) — see the module docstring.
_PYDANTIC_EXPORTS = ("to_pydantic", "confluid_class_of")


def __getattr__(name: str) -> Any:
    if name in _PYDANTIC_EXPORTS:
        try:
            from confluid import pydantic_export
        except ModuleNotFoundError as exc:
            if exc.name in ("pydantic", "annotated_types"):
                raise ImportError(
                    f"confluid.{name} requires pydantic, which is an optional dependency — "
                    "install the extra: pip install 'confluid[pydantic]'"
                ) from exc
            raise
        return getattr(pydantic_export, name)
    raise AttributeError(f"module 'confluid' has no attribute {name!r}")
