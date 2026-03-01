import os
import re
from typing import Any, Dict, Optional

from confluid.registry import get_registry

# Regex for environment variables: ${VAR} or ${VAR:-default}
ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::-(.*))?\}")

# Regex for references: @Class, @Class(), or @key
REF_PATTERN = re.compile(r"^@([a-zA-Z0-9_./]+)(\(.*\))?$")


class Resolver:
    """Handles resolution of environment variables and hierarchical references."""

    def __init__(self, context: Optional[Dict[str, Any]] = None) -> None:
        self.context = context or {}
        self.registry = get_registry()

    def resolve(self, data: Any) -> Any:
        """Recursively resolve all patterns in the provided data structure."""
        if isinstance(data, dict):
            return {k: self.resolve(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.resolve(v) for v in data]
        elif isinstance(data, str):
            # 1. Resolve Environment Variables first
            data = self._resolve_env_vars(data)
            # 2. Resolve @References
            return self._resolve_reference(data)
        return data

    def _resolve_env_vars(self, value: str) -> str:
        """Replace ${VAR} or ${VAR:-default} with environment values."""

        def replacer(match: re.Match) -> str:
            var_name = match.group(1)
            default_value = match.group(2)
            return os.getenv(var_name, default_value if default_value is not None else match.group(0))

        return ENV_VAR_PATTERN.sub(replacer, value)

    def _resolve_reference(self, value: str) -> Any:
        """Resolve @ patterns to objects, classes, or values."""
        if not value.startswith("@"):
            return value

        match = REF_PATTERN.match(value)
        if not match:
            return value

        target = match.group(1)
        args_str = match.group(2)

        # 1. Check Config Context (@key)
        if not args_str and target in self.context:
            return self.context[target]

        # 2. Check Registry (@Class or @Class())
        cls = self.registry.get_class(target)
        if cls:
            if args_str:
                # Instantiate with args
                return self._instantiate(cls, args_str)
            return cls

        # 3. Check Registered Objects (@NamedObject)
        obj = self.registry.get_object(target)
        if obj:
            if args_str:
                raise ValueError(f"Cannot call registered object as a class: {value}")
            return obj

        return value

    def _instantiate(self, cls: type, args_str: str) -> Any:
        """Safely evaluate arguments and instantiate the class."""
        import ast

        # Strip parentheses
        content = args_str[1:-1].strip()
        if not content:
            return cls()

        # For MVP, we use a simple dict-style kwarg parser.
        kwargs = {}
        for pair in content.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                k = k.strip()
                v = v.strip()

                # If it's a nested reference, resolve it first
                if v.startswith("@") or "${" in v:
                    val = self.resolve(v)
                else:
                    # Otherwise, try to parse as a Python literal (int, float, bool, etc)
                    try:
                        val = ast.literal_eval(v)
                    except (ValueError, SyntaxError):
                        # Fallback to stripped string
                        val = self._strip_quotes(v)

                kwargs[k] = val

        return cls(**kwargs)

    @staticmethod
    def _strip_quotes(v: str) -> str:
        if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
            return v[1:-1]
        return v
