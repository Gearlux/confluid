import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union


@dataclass
class Reference:
    """Internal representation of a variable reference (!ref)."""

    path: str


@dataclass
class ClassReference:
    """Internal representation of a class instantiation (!class)."""

    cls_name: str
    args_str: Union[str, Dict[str, Any]] = ""


class Resolver:
    """
    Advanced resolution engine.
    Handles environment variables, context references, and dynamic instantiation.
    """

    def __init__(self, context: Optional[Dict[str, Any]] = None) -> None:
        self.context = context or {}

    def resolve(self, val: Any) -> Any:
        """
        Recursively resolve references and environment variables.
        """
        # 1. Handle IR Objects
        if isinstance(val, Reference):
            return self._resolve_reference(val.path)

        if isinstance(val, ClassReference):
            return self._resolve_class(val)

        # 2. Handle Strings (where our tags live)
        if isinstance(val, str):
            # Resolve environment variables first
            val = self._resolve_env_vars(val)

            # Check for our professional tag prefixes
            if val.startswith("!ref:"):
                return self._resolve_reference(val[5:])

            if val.startswith("!class:"):
                # Extract Name(args)
                content = val[7:]
                if "(" in content and content.endswith(")"):
                    name, args = content[:-1].split("(", 1)
                    return self._resolve_class(ClassReference(name, args))
                return self._resolve_class(ClassReference(content))

            # Internal legacy support for @ (to be removed once fully migrated)
            if val.startswith("@"):
                return self._resolve_reference(val[1:])

            return val

        # 3. Recurse into Containers
        if isinstance(val, dict):
            return {k: self.resolve(v) for k, v in val.items()}

        if isinstance(val, (list, tuple)):
            return [self.resolve(item) for item in val]

        return val

    def _resolve_reference(self, path: str) -> Any:
        """Resolve a Reference object against the context or registry."""
        current = self.context
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                # If path not found in context, check registry for class type
                from confluid.registry import get_registry

                cls = get_registry().get_class(path)
                if cls:
                    return cls
                return f"ref:{path}"  # Safe fallback
        return current

    def _resolve_class(self, ref: ClassReference) -> Any:
        """Resolve a ClassReference into a live instance."""
        from confluid.registry import get_registry

        cls = get_registry().get_class(ref.cls_name)
        if not cls:
            return f"class:{ref.cls_name}"

        # Handle different argument formats
        if isinstance(ref.args_str, dict):
            # Arguments are already a resolved/raw dictionary
            kwargs = {k: self.resolve(v) for k, v in ref.args_str.items()}
            return self._instantiate_from_dict(cls, kwargs)

        # Fallback to string parsing
        return self._instantiate(cls, f"({ref.args_str})")

    def _instantiate_from_dict(self, cls: type, kwargs: Dict[str, Any]) -> Any:
        """Instantiate a class using a dictionary of arguments and context merging."""
        from confluid.parser import parse_value

        # 1. Type parse all arguments
        final_kwargs = {}
        for k, v in kwargs.items():
            if isinstance(v, str):
                v = self._strip_quotes(v)
                v = parse_value(v)
            final_kwargs[k] = v

        # 2. MERGE LOGIC: Get global settings for this class from the context
        if self.context and cls.__name__ in self.context:
            global_settings = self.context[cls.__name__]
            if isinstance(global_settings, dict):
                final_kwargs = {**global_settings, **final_kwargs}

        return cls(**final_kwargs)

    def _resolve_env_vars(self, val: str) -> str:
        """Replace ${VAR} or ${VAR:default} with environment variables."""
        pattern = re.compile(r"\$\{([\w_]+)(?::([^}]+))?\}")

        def replacer(match: re.Match) -> str:
            name, default = match.groups()
            if default and default.startswith("-"):
                default = default[1:]
            return os.environ.get(name, default if default is not None else match.group(0))

        return pattern.sub(replacer, val)

    def _instantiate(self, cls: type, args_str: str) -> Any:
        """Safely evaluate arguments and instantiate the class."""
        from confluid.parser import parse_value

        content = args_str[1:-1].strip()
        kwargs = {}
        if content:
            # Comma-separated pair parser
            for pair in content.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    # 1. Recursive resolve
                    val = self.resolve(v)
                    # 2. Type parsing (Essential for CLI/YAML parity)
                    if isinstance(val, str):
                        val = self._strip_quotes(val)
                        val = parse_value(val)
                    kwargs[k] = val

        # MERGE LOGIC: Get global settings for this class from the context
        if self.context and cls.__name__ in self.context:
            global_settings = self.context[cls.__name__]
            if isinstance(global_settings, dict):
                kwargs = {**global_settings, **kwargs}

        return cls(**kwargs)

    @staticmethod
    def _strip_quotes(v: str) -> str:
        if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
            return v[1:-1]
        return v
