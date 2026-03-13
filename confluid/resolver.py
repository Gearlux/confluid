import os
import re
from typing import Any, Dict, Optional

from logflow import get_logger

logger = get_logger("confluid.resolver")


class Resolver:
    """Resolves references (!ref), environment variables (${ENV}), and deep keys."""

    def __init__(self, context: Optional[Dict[str, Any]] = None) -> None:
        self.context = context or {}

    def resolve(self, value: Any, local_context: Optional[Dict[str, Any]] = None) -> Any:
        """
        Recursively resolves markers with support for local scoping.
        """
        # 1. Handle Strings (Interpolation and Tags)
        if isinstance(value, str):
            value = self._interpolate(value)
            if not isinstance(value, str):
                return value

            if value.startswith("!ref:"):
                ref_path = value[5:]
                res = self._resolve_ref(ref_path, local_context)
                # Recurse only if the resolved value is DIFFERENT from the input
                if res != value and isinstance(res, (str, dict)):
                    return self.resolve(res, local_context)
                return res

            if value.startswith("!class:"):
                content = value[7:]
                return self._parse_class_string(content, local_context)

            if value.startswith("@"):
                content = value[1:]
                if "(" in content:
                    return self._parse_class_string(content, local_context)
                # Pure reference
                res = self._resolve_ref(content, local_context)
                # Recurse only if the resolved value is DIFFERENT from the input
                if res != value and isinstance(res, (str, dict)):
                    return self.resolve(res, local_context)
                return res

            return value

        # 2. Handle Dictionary Markers
        if isinstance(value, dict):
            if "_confluid_ref_" in value:
                ref_path = value["_confluid_ref_"]
                # Try local context first, then global
                res = self._resolve_ref(ref_path, local_context)

                # Check for recursion (is the result another marker?)
                if isinstance(res, (dict, str)):
                    return self.resolve(res, local_context)

                # MANDATE: Ensure the resolved value is correctly typed (YAML conversion)
                if isinstance(res, str):
                    return self._parse_primitive(res)
                return res

            if "_confluid_class_" in value:
                # We don't resolve classes here; materialization handles them.
                return value

            # Recurse into normal dicts, passing the current dict as local_context
            return {k: self.resolve(v, local_context=value) for k, v in value.items()}

        # 3. Handle Lists
        if isinstance(value, list):
            return [self.resolve(item, local_context) for item in value]

        return value

    def _parse_class_string(self, content: str, local_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Helper to parse 'ClassName(args)' into a marker dict."""
        if "(" in content and content.endswith(")"):
            cls_name, args_str = content[:-1].split("(", 1)
            kwargs = {}
            if args_str.strip():
                for pair in args_str.split(","):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        # Resolve and Parse the value!
                        resolved_v = self.resolve(v, local_context)
                        if isinstance(resolved_v, str):
                            resolved_v = self._parse_primitive(resolved_v)
                        kwargs[k] = resolved_v
            return {"_confluid_class_": cls_name, **kwargs}
        return {"_confluid_class_": content}

    def _resolve_ref(self, ref_path: str, local_context: Optional[Dict[str, Any]] = None) -> Any:
        """
        Resolve a dotted path against local and global contexts.
        """
        # 1. Try Local Context First
        if local_context:
            val = self._lookup_path(ref_path, local_context)
            if val is not None and (not isinstance(val, str) or not val.startswith("!ref:")):
                return val

        # 2. Try Global Context
        val = self._lookup_path(ref_path, self.context)
        if val is not None:
            return val

        logger.warning(f"Failed to resolve reference: {ref_path}")
        return f"!ref:{ref_path}"

    def _lookup_path(self, path: str, context: Dict[str, Any]) -> Any:
        """Helper to drill into a dictionary via dotted path."""
        parts = path.split(".")
        current = context

        # Try direct literal lookup first (handles keys with dots)
        if path in context:
            return context[path]

        # Try recursive navigation
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _interpolate(self, value: str) -> Any:
        env_pattern = r"\$\{([\w_]+)(?::([^}]+))?\}"

        def env_replacer(match: re.Match) -> str:
            var_name = match.group(1)
            default_val = match.group(2)
            return os.getenv(var_name, default_val or match.group(0))

        if "${" in value:
            match = re.fullmatch(env_pattern, value)
            if match:
                var_name = match.group(1)
                default_val = match.group(2)
                env_val = os.getenv(var_name)
                if env_val is not None:
                    return self._parse_primitive(env_val)
                return self._parse_primitive(default_val) if default_val is not None else value

            value = re.sub(env_pattern, env_replacer, value)

        return value

    def _parse_primitive(self, value: str) -> Any:
        """Convert string to appropriate Python primitive (YAML-like conversion)."""
        # Handle Confluid internal markers
        if value.startswith("!ref:"):
            return value

        low = value.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        if low == "none" or low == "null":
            return None

        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return value
