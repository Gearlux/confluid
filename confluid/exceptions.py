"""Typed exception hierarchy for confluid.

Every concrete exception dual-inherits the :class:`ConfluidError` root AND the
builtin it semantically replaces (``ValueError``, ``FileNotFoundError``, …), so
pre-existing ``except ValueError:`` / ``pytest.raises(ValueError)`` call sites
keep working while new callers can catch confluid failures distinctly
(``except ConfluidError:`` or a specific subclass).
"""

from __future__ import annotations


class ConfluidError(Exception):
    """Root of the confluid exception hierarchy."""


class ConfigurationError(ConfluidError, ValueError):
    """A config document is structurally or semantically invalid."""


class CircularIncludeError(ConfigurationError):
    """An ``include:`` chain revisits a file it is already loading."""


class ReferenceResolutionError(ConfigurationError):
    """A ``!ref:`` target cannot be resolved (unknown or self-referential)."""


class UnknownClassError(ConfigurationError):
    """A ``!class:`` / ``Fluid`` target names a class not in the registry and not importable."""


class ConfigurableDefinitionError(ConfigurationError):
    """A ``@configurable`` declaration is self-contradictory (e.g. ``constant=True, random=True``)."""


class ValidationModeError(ConfigurationError):
    """A ``CONFLUID_VALIDATE_*`` env var holds an unknown :class:`ValidationMode` literal."""


class ScopeError(ConfigurationError):
    """A scope alias chain is circular."""


class ConfigFileNotFoundError(ConfluidError, FileNotFoundError):
    """A config (or included) file path does not exist."""


class ConstructionError(ConfluidError, RuntimeError):
    """A target's constructor failed and the original exception class cannot be rebuilt from a message."""


class WorkspaceEnvError(ConfluidError, RuntimeError):
    """The workspace ``.env`` is missing, or a required key is unset / points at a missing path."""


class IntrospectionError(ConfluidError, TypeError):
    """A class or callable cannot be introspected for schema export."""
