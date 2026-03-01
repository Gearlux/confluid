import inspect
from typing import Any, Dict

import yaml


class Dumper:
    """Recursively dumps configurable object hierarchies to dictionary or YAML."""

    def __init__(self) -> None:
        self._visited: set[int] = set()

    def to_dict(self, obj: Any) -> Any:
        """Convert a configurable object hierarchy into a dictionary."""
        self._visited.clear()
        return self._dump_recursive(obj)

    def _dump_recursive(self, obj: Any) -> Any:
        """Internal recursive dumping logic with strict gating."""
        if obj is None:
            return None

        # 1. Handle Primitives (immediately return to avoid ID tracking)
        if isinstance(obj, (int, float, str, bool)):
            return obj

        # Prevent circular references for complex objects
        obj_id = id(obj)
        if obj_id in self._visited:
            return f"<Circular reference to {obj.__class__.__name__}>"
        self._visited.add(obj_id)

        # 2. Handle Lists/Tuples
        if isinstance(obj, (list, tuple)):
            return [self._dump_recursive(item) for item in obj]

        # 3. Handle Dicts
        if isinstance(obj, dict):
            return {str(k): self._dump_recursive(v) for k, v in obj.items()}

        # 4. Handle Configurable Objects (Gating)
        cls = obj.__class__
        if not getattr(cls, "__confluid_configurable__", False):
            # STRICT GATING: If not configurable, do not inspect further
            return str(obj)

        # Extract attributes from __dict__ and properties
        cls_name = getattr(cls, "__confluid_name__", cls.__name__)
        data: Dict[str, Any] = {}

        # Inspect __init__ to know which attributes are "official" config points
        try:
            sig = inspect.signature(cls.__init__)
            params = [p for p in sig.parameters.keys() if p not in ("self", "cls")]
        except (ValueError, TypeError):
            params = []

        # Try to find these params in the object's attributes
        for param in params:
            if hasattr(obj, param):
                val = getattr(obj, param)
                data[param] = self._dump_recursive(val)

        return {cls_name: data}

    def dump(self, obj: Any) -> str:
        """Dump the object hierarchy to a YAML string."""
        data = self.to_dict(obj)
        return yaml.dump(data, sort_keys=False)


# Global convenience instance
_default_dumper = Dumper()


def dump(obj: Any) -> str:
    """Global convenience function to dump an object hierarchy."""
    return _default_dumper.dump(obj)
