"""
Confluid: Modern, hierarchical configuration and dependency injection for Python.
"""

from confluid.decorators import configurable, register
from confluid.registry import get_registry

__all__ = ["configurable", "register", "get_registry"]
