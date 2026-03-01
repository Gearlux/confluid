import ast
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
            resolved = self._resolve_reference(data)

            # 3. If resolved is still a string, try to parse as literal
            if isinstance(resolved, str):
                try:

                    # Only attempt literal_eval if it doesn't look like a raw string
                    # or starts with quotes
                    return ast.literal_eval(resolved)
                except (ValueError, SyntaxError):
                    return self._strip_quotes(resolved)
            return resolved
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

                # Resolve v if it contains references or env vars
                val = self.resolve(v)

                # If it's a string from literal_eval, strip quotes if needed
                if isinstance(val, str):
                    val = self._strip_quotes(val)

                kwargs[k] = val

        return cls(**kwargs)

    @staticmethod
    def _strip_quotes(v: str) -> str:
        if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
            return v[1:-1]
        return v
