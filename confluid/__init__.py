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

# Each name from its REAL home — laundering these through the `engine` compat
# shim gave the shim internal consumers, defeating any future zero-user audit.
from confluid.broadcast import accepts_any_key, accepts_broadcast, accepts_key, declares_key
from confluid.configurator import configure, configure_from_file
from confluid.decorators import configurable, ignore_config, output, register
from confluid.dumper import dump
from confluid.engine import cast, flow, get_configurable_attrs, materialize, resolve
from confluid.exceptions import (
    AmbiguousClassError,
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
from confluid.fluid import Clone, Fluid
from confluid.fluid import Partial as PartialClass
from confluid.fluid import Reference, Target, format_yaml_loc
from confluid.llm_schema import sanitize_schema
from confluid.loader import get_app_name, load, load_config, load_config_with_paths, resolve_config_path, set_app_name
from confluid.mandatory import Mandatory, mandatory_param_names
from confluid.merger import deep_merge, expand_dotted_keys
from confluid.no_broadcast import NoBroadcast, no_broadcast_param_names

# became `Bad number of arguments for type alias, expected 0, given 1` (measured
# in three real slots). An import alias stays the same generic alias.
from confluid.partial import Partial, partial_param_names
from confluid.registry import Marks, get_registry, load_configurables, marks
from confluid.report import ConfigurationReport
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
from confluid.scopes import discover_dimension_values, discover_dimensions
from confluid.state import active_context, collect_report
from confluid.validation import ValidationMode, ValidationPolicy, get_policy, reset_policy, set_policy, validate_model

# --------------------------------------------------------------------------- #
__all__ = [
    "ConfluidError",
    "ConfigurationError",
    "AmbiguousClassError",
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
    "load_configurables",
    "Marks",
    "marks",
    "load",
    "load_config",
    "load_config_with_paths",
    "resolve_config_path",
    "set_app_name",
    "get_app_name",
    "materialize",
    "resolve",
    "active_context",
    "deep_merge",
    "expand_dotted_keys",
    "parse_value",
    "dump",
    "configure",
    "configure_from_file",
    "ConfigurationReport",
    "collect_report",
    "Fluid",
    "Target",
    "Clone",
    "Reference",
    "flow",
    "cast",
    "format_yaml_loc",
    "Partial",
    "PartialClass",
    "partial_param_names",
    # deprecated (see the alias block above)
    "partial_param_names",
    "Mandatory",
    "mandatory_param_names",
    "NoBroadcast",
    "no_broadcast_param_names",
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
    "discover_dimension_values",
    "discover_dimensions",
    "accepts_key",
    "accepts_broadcast",
    "accepts_any_key",
    "declares_key",
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
