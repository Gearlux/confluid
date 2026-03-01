"""
Confluid: Modern, hierarchical configuration and dependency injection for Python.
"""

from confluid.decorators import configurable, register
from confluid.registry import get_registry
from confluid.resolver import Resolver

__all__ = ["configurable", "register", "get_registry", "Resolver"]
