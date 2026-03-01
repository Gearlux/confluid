import inspect
from typing import Any, Dict, Optional, Type

from pydantic import ConfigDict, create_model

from confluid.resolver import Resolver


class Configurator:
    """Core engine for applying validated configuration to object instances."""

    def __init__(self, resolver: Optional[Resolver] = None) -> None:
        self.resolver = resolver or Resolver()

    def configure(self, instance: Any, data: Any, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Apply configuration data to an existing object instance.

        Args:
            instance: The object to configure.
            data: Raw configuration data (dict, YAML string, etc.)
            context: Optional shared context for reference resolution.
        """
        if data is None:
            return

        # 1. Resolve references and environment variables in the data
        resolver = Resolver(context=context) if context else self.resolver
        resolved_data = resolver.resolve(data)

        if not isinstance(resolved_data, dict):
            # If data is a direct reference (e.g. @Model()), it might return an instance
            # In post-construction, we usually expect a dict of attributes.
            return

        # 2. Extract configuration for this specific class
        cls = instance.__class__
        cls_name = getattr(cls, "__confluid_name__", cls.__name__)

        # Check if the data is scoped by class name (e.g. { "Model": { ... } })
        config_dict = resolved_data.get(cls_name, resolved_data) if isinstance(resolved_data, dict) else {}

        # 3. Create a transient Pydantic model for validation
        pydantic_model = self._create_pydantic_model(cls)

        # 4. Validate and coerce data
        validated = pydantic_model(**config_dict)

        # 5. Apply to instance
        for key, value in validated.model_dump(exclude_unset=True).items():
            setattr(instance, key, value)

    def _create_pydantic_model(self, cls: Type[Any]) -> Any:
        """Dynamically create a Pydantic model from class __init__ signature."""
        sig = inspect.signature(cls.__init__)
        fields: Dict[str, Any] = {}

        for name, param in sig.parameters.items():
            if name in ("self", "cls"):
                continue

            # Extract type hint. In post-construction, every field is optional
            # because the instance already exists.
            annotation = param.annotation if param.annotation is not inspect.Parameter.empty else Any

            # Default to None to make it optional in Pydantic
            fields[name] = (Optional[annotation], None)

        # Enable arbitrary_types_allowed to support custom class types in __init__
        config = ConfigDict(arbitrary_types_allowed=True)
        return create_model(f"{cls.__name__}Config", __config__=config, **fields)


# Global convenience instance
_default_configurator = Configurator()


def configure(instance: Any, data: Any, context: Optional[Dict[str, Any]] = None) -> None:
    """Global convenience function to configure an object."""
    _default_configurator.configure(instance, data, context=context)
