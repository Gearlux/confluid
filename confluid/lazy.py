"""Deprecated alias module — ``confluid.lazy`` is now :mod:`confluid.partial`.

The ``Lazy*`` vocabulary was renamed to ``Partial*`` (2026-08-11) so the Python
API and the YAML ``_partial_`` key are the same word. This module keeps the old
import path resolving for the deprecation window and is REMOVED with the tag
spelling (phase 5).

It exists because the rename is not confined to the names a caller sees: a
consumer importing ``from confluid.lazy import lazy_param_names`` breaks on the
MODULE path even though every name it asks for still exists. New code imports
from :mod:`confluid.partial`, or the top-level ``confluid`` surface.

The aliases are ASSIGNMENTS rather than ``import ... as`` lines on purpose: an
import-only alias reads as unused to every linter, and the ``# noqa`` markers it
needs do not survive an import sorter re-joining the lines.
"""

from confluid import partial as _partial

Partial = _partial.Partial
partial_param_names = _partial.partial_param_names
is_partial_annotation = _partial.is_partial_annotation
body_slot_partial_names = _partial.body_slot_partial_names

# The pre-rename spellings.
Lazy = _partial.Partial
lazy_param_names = _partial.partial_param_names
is_lazy_annotation = _partial.is_partial_annotation
body_slot_lazy_names = _partial.body_slot_partial_names
_LAZY_MARKER = _partial._PARTIAL_MARKER

__all__ = [
    "Lazy",
    "Partial",
    "body_slot_lazy_names",
    "body_slot_partial_names",
    "is_lazy_annotation",
    "is_partial_annotation",
    "lazy_param_names",
    "partial_param_names",
]
