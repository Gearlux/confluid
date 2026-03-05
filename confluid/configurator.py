import inspect
from typing import Any, Dict, List, Optional, Set

from confluid.merger import expand_dotted_keys
from confluid.resolver import Resolver


class Configurator:
    """
    Advanced recursive configuration engine.
    Supports attribute matching, dotted-path scoping, and broadcast configuration.
    """

    def __init__(self, resolver: Optional[Resolver] = None) -> None:
        self.resolver = resolver or Resolver()
        self._visited: Set[int] = set()

    def configure(self, *instances: Any, data: Any, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Apply configuration to one or more object instances.
        """
        if data is None:
            return

        # 1. Resolve references and environment variables
        # If context is not provided, use data as the context for reference resolution
        resolved_context = context if context is not None else (data if isinstance(data, dict) else {})
        resolver = Resolver(context=resolved_context)
        resolved_data = resolver.resolve(data)

        if not isinstance(resolved_data, dict):
            return

        # 2. Expand any top-level dotted keys for easier lookup
        config_data = expand_dotted_keys(resolved_data)

        # 3. Recursively walk each instance
        self._visited.clear()
        for instance in instances:
            # We start with empty prefix
            # Resolve the config data specifically for this walker to handle IR objects
            final_config = self.resolver.resolve(config_data)
            self._walk_and_configure(instance, final_config, resolved_context, "")

    def _walk_and_configure(self, obj: Any, config: Dict[str, Any], context: Dict[str, Any], path_prefix: str) -> None:
        """Recursively traverse the object graph and apply matching configuration."""
        if obj is None:
            return

        # 0. Solidify the object before inspection
        from confluid.solidify import solidify

        obj = solidify(obj)

        obj_id = id(obj)
        if obj_id in self._visited:
            return
        self._visited.add(obj_id)

        # 1. Recurse into containers
        if isinstance(obj, (list, tuple)):
            for item in obj:
                self._walk_and_configure(item, config, context, path_prefix)
            return

        if isinstance(obj, dict):
            for v in obj.values():
                self._walk_and_configure(v, config, context, path_prefix)
            return

        # 2. Configure object if marked as configurable
        cls = obj.__class__
        new_prefix = path_prefix
        if getattr(cls, "__confluid_configurable__", False):
            # Calculate the path for this specific object
            instance_name = getattr(obj, "name", None)
            if instance_name and isinstance(instance_name, str):
                new_prefix = f"{path_prefix}.{instance_name}" if path_prefix else instance_name

            # We apply config using the NEW prefix (which includes this object's name)
            self._apply_obj_config(obj, config, context, new_prefix)

        # 3. Recursively walk into object attributes
        for attr_name in dir(obj):
            if attr_name.startswith("_"):
                continue
            try:
                attr_val = getattr(obj, attr_name)
                if not callable(attr_val):
                    self._walk_and_configure(attr_val, config, context, new_prefix)
            except Exception:
                continue

    def _apply_obj_config(self, obj: Any, config: Dict[str, Any], context: Dict[str, Any], path_prefix: str) -> None:
        """Collect and apply configuration specifically for one object."""
        cls = obj.__class__
        cls_name = getattr(cls, "__confluid_name__", cls.__name__)
        instance_name = getattr(obj, "name", None)

        # Build local configuration overlay
        obj_config: Dict[str, Any] = {}
        if cls_name in config and isinstance(config[cls_name], dict):
            obj_config.update(config[cls_name])
        if instance_name and instance_name in config and isinstance(config[instance_name], dict):
            obj_config.update(config[instance_name])

        scoped_name = f"{cls_name}.{instance_name}" if instance_name else None
        if scoped_name and scoped_name in config and isinstance(config[scoped_name], dict):
            obj_config.update(config[scoped_name])

        # Apply settings to attributes
        for attr_name in self._get_configurable_attributes(obj):
            val = self._match_attr_value(attr_name, cls_name, instance_name, config, obj_config, path_prefix)

            if val is not None:
                # 1. Resolve references in the value
                resolver = Resolver(context=context)
                resolved_val = resolver.resolve(val)

                # 2. Type parsing (Ensure strings like "100" become int 100)
                from confluid.parser import parse_value

                if isinstance(resolved_val, str):
                    resolved_val = parse_value(resolved_val)

                # 3. RECURSION PROTECTION
                # If current attribute is an object and the config value is a dict,
                # we should configure the object instead of overwriting it.
                current_val = getattr(obj, attr_name, None)
                if isinstance(resolved_val, dict) and hasattr(current_val.__class__, "__confluid_configurable__"):
                    self._walk_and_configure(current_val, resolved_val, context, path_prefix)
                else:
                    setattr(obj, attr_name, resolved_val)

    def _match_attr_value(
        self,
        attr_name: str,
        cls_name: str,
        instance_name: Optional[str],
        config: Dict[str, Any],
        obj_config: Dict[str, Any],
        path_prefix: str,
    ) -> Any:
        """Find the best matching configuration value for an attribute based on priority."""
        # 0. Hierarchical Path (Highest Priority) e.g. "a.b.c.value"
        if path_prefix:
            full_path = f"{path_prefix}.{attr_name}"
            val = self._deep_get(config, full_path)
            if val is not None:
                return val

        # 1. ClassName.instance_name.attr
        if instance_name:
            val = self._deep_get(config, f"{cls_name}.{instance_name}.{attr_name}")
            if val is not None:
                return val

        # 2. ClassName.attr
        val = self._deep_get(config, f"{cls_name}.{attr_name}")
        if val is not None:
            return val

        # 3. instance_name.attr
        if instance_name:
            val = self._deep_get(config, f"{instance_name}.{attr_name}")
            if val is not None:
                return val

        # 4. Direct attribute in object config
        if attr_name in obj_config:
            return obj_config[attr_name]

        # 5. Broadcast check: direct attribute in global config
        if attr_name in config and not isinstance(config[attr_name], dict):
            return config[attr_name]

        return None

    def _deep_get(self, data: Dict[str, Any], path: str) -> Any:
        """Retrieve a value from a nested dictionary using a dotted path."""
        # First try literal match (flat key)
        if path in data:
            return data[path]

        # Then try walking the nested structure
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _get_configurable_attributes(self, obj: Any) -> List[str]:
        """Identify which attributes of an object are candidates for configuration."""
        attrs = []
        cls = obj.__class__

        # 1. Check __init__ signature
        try:
            sig = inspect.signature(cls.__init__)
            attrs.extend([p for p in sig.parameters.keys() if p not in ("self", "cls")])
        except (ValueError, TypeError):
            pass

        # 2. Add public attributes and properties
        for name in dir(obj):
            if name.startswith("_") or callable(getattr(obj, name)):
                continue

            # Check visibility markers on the class member
            member = getattr(cls, name, None)
            if member and getattr(member, "__confluid_ignore__", False):
                continue

            # If it's a property, it MUST have a setter to be configurable
            if isinstance(member, property) and member.fset is None:
                continue

            if name not in attrs:
                attrs.append(name)
        return attrs


# Global convenience instance
_default_configurator = Configurator()


def configure(*instances: Any, config: Any, context: Optional[Dict[str, Any]] = None) -> None:
    """Global convenience function to configure one or more objects."""
    # Support both string (YAML) and dict configuration
    if isinstance(config, str) and (":" in config or "\n" in config):
        import yaml

        config_dict = yaml.safe_load(config)
    else:
        config_dict = config

    _default_configurator.configure(*instances, data=config_dict, context=context)
