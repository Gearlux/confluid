from copy import copy
from typing import Any, Type, Union

from confluid.registry import get_registry, resolve_class


class Fluid:
    """Base class for all deferred configuration objects."""

    __confluid_configurable__ = True

    def __init__(self, target: Any, **kwargs: Any) -> None:
        self.target = target
        self.kwargs = kwargs

    def __repr__(self) -> str:
        name = self.target if isinstance(self.target, str) else getattr(self.target, "__name__", str(self.target))
        return f"{self.__class__.__name__}({name}, {self.kwargs})"


class Class(Fluid):
    """Deferred class initializer. Stays deferred until explicitly flow()'d."""

    def __init__(self, target: Union[Type[Any], str], **kwargs: Any) -> None:
        super().__init__(target, **kwargs)


class Instance(Fluid):
    """Instant class initializer. Materialized immediately by materialize()/flow()."""

    def __init__(self, target: Union[Type[Any], str], **kwargs: Any) -> None:
        super().__init__(target, **kwargs)


class Reference(Fluid):
    """Late-bound reference to another part of the config."""

    def __init__(self, path: str, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)


class Clone(Fluid):
    """Deep-copy reference. Resolves like !ref: but returns a deepcopy."""

    def __init__(self, path: str, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)


def flow(obj: Any, **runtime_kwargs: Any) -> Any:
    """Instantiate a deferred object (Class, Reference, marker dict) into a live instance.

    Idempotent: already-live objects are returned unchanged.
    Accepts runtime kwargs that merge with stored kwargs (runtime wins).

    Within a ``materialize()`` pass, the same ``Instance`` marker (reached
    directly or via ``!ref:``) produces a single live object — subsequent
    ``flow()`` calls on the same marker return the cached instance.
    """
    from confluid.loader import _state, get_active_context, materialize
    from confluid.resolver import Resolver

    # 1. Idempotency — already-live objects pass through
    if not isinstance(obj, (Fluid, str, type, dict)):
        return obj

    # 2. Resolve context from the global active context
    context = get_active_context()

    # Instance memoization — only within an active materialize() pass and only
    # when no runtime kwargs override the stored ones (overrides must yield a
    # fresh object).
    instance_memo = getattr(_state, "instance_memo", None)
    if isinstance(obj, Instance) and instance_memo is not None and not runtime_kwargs:
        cached = instance_memo.get(id(obj))
        if cached is not None:
            return cached

    # 3. Class/Instance — resolve, merge kwargs, instantiate
    if isinstance(obj, (Class, Instance)):
        import inspect

        target = obj.target
        if isinstance(target, str):
            resolved = resolve_class(target)
            if resolved is None:
                raise ValueError(f"Cannot resolve class: {target}")
            target = resolved

        # kwargs already contain broadcasting (merged by _flow_recursive)
        merged: dict[str, Any] = dict(obj.kwargs)
        merged.update(runtime_kwargs)

        # Flow Instance values (instant), keep Class/Reference deferred
        # Apply broadcasting from full context to deferred Class objects
        broadcast_ctx = context or merged

        def _resolve_value(v: Any) -> Any:
            if isinstance(v, Instance):
                return flow(v)
            if isinstance(v, Class):
                # Apply broadcasting: pull matching keys from full context
                from confluid.loader import _get_acceptable_keys

                broadcasted = dict(v.kwargs)
                acceptable = _get_acceptable_keys(v.target)
                inner_target_cls = v.target if isinstance(v.target, type) else resolve_class(v.target) if isinstance(v.target, str) else None
                for bk, bv in broadcast_ctx.items():
                    if bk in broadcasted or isinstance(bv, (dict, list)):
                        continue
                    if isinstance(bv, Fluid):
                        # Fluids only broadcast through an explicit accepted
                        # key — never via the **kwargs catchall (which would
                        # pull the outer Class into nested targets and loop).
                        if acceptable is None or bk not in acceptable:
                            continue
                        # Self-broadcast guard: skip a Fluid whose target is
                        # the same class we're filling. Avoids infinite
                        # recursion when an inherited attribute (e.g.
                        # pl.LightningModule.trainer) makes the class's own
                        # name an acceptable broadcast target.
                        if inner_target_cls is not None:
                            from confluid.loader import _same_target

                            if _same_target(bv.target, inner_target_cls):
                                continue
                    elif acceptable is not None and bk not in acceptable:
                        continue
                    broadcasted[bk] = bv
                v_copy = copy(v)
                v_copy.kwargs = broadcasted
                return v_copy
            if isinstance(v, Reference) and context:
                try:
                    return flow(v)
                except ValueError:
                    return v  # Unresolvable reference — keep deferred
            if isinstance(v, Fluid):
                return v  # Other Fluid types stay as-is
            if isinstance(v, list):
                return [_resolve_value(item) for item in v]
            if isinstance(v, dict):
                return {dk: _resolve_value(dv) for dk, dv in v.items()}
            return v

        merged = {k: _resolve_value(v) for k, v in merged.items()}

        # Instantiate: constructor params go to __init__, rest set as attributes
        try:
            init_method = getattr(target, "__init__", None)
            if init_method is None:
                return obj
            sig = inspect.signature(init_method)
            params = {p for p in sig.parameters if p not in ("self", "cls")}
        except (ValueError, TypeError):
            params = set()

        ctor = {k: v for k, v in merged.items() if k in params} if params else merged
        instance = target(**ctor)

        # Memoize so a second flow() of the same Instance marker returns this
        # exact object (see module docstring).
        if isinstance(obj, Instance) and instance_memo is not None and not runtime_kwargs:
            instance_memo[id(obj)] = instance

        # Preserve Confluid origin for serialization round-trip
        try:
            instance.__confluid_class__ = target
            instance.__confluid_kwargs__ = ctor
        except (TypeError, AttributeError):
            pass  # Built-in types / __slots__-only classes may reject arbitrary attrs

        # Only set non-constructor attributes on configurable classes
        if getattr(target, "__confluid_configurable__", False):
            extra_keys: list[str] = []
            for k, v in merged.items():
                if params and k not in params:
                    member = getattr(target, k, None)
                    if isinstance(member, property) and member.fset is None:
                        continue
                    if getattr(member, "__confluid_ignore__", False):
                        continue
                    # Post-init attrs land on a live instance — if the value
                    # is still a Fluid marker (e.g. a nested ``!class:X`` that
                    # broadcasting carried in), materialize it now. Unlike
                    # constructor args, post-init attrs have no runtime-kwarg
                    # injection channel, so a deferred marker here would just
                    # pollute a slot typed as the real dependency (and e.g.
                    # ``nn.Module.__setattr__`` would outright reject it).
                    if isinstance(v, Fluid):
                        v = flow(v)
                    setattr(instance, k, v)
                    extra_keys.append(k)
            try:
                instance.__confluid_extra__ = extra_keys
            except (TypeError, AttributeError):
                pass

        # Apply broadcasting to any Fluid-valued instance attribute — whether it
        # came from a constructor default or was assigned in __init__'s body
        # (e.g. ``self.lightning = Class(L.Trainer)`` without a ``lightning``
        # ctor parameter). This lets users keep @configurable signatures clean
        # without sacrificing broadcast reach.
        seen: set[str] = set()
        for attr_name, attr_val in list(vars(instance).items()):
            if attr_name.startswith("__confluid_"):
                continue
            if not isinstance(attr_val, Fluid):
                continue
            resolved = _resolve_value(attr_val)
            if resolved is not attr_val:
                try:
                    setattr(instance, attr_name, resolved)
                except (AttributeError, TypeError):
                    pass  # Read-only property or __slots__
            seen.add(attr_name)

        # Preserve prior behaviour for ctor-default params that don't appear
        # on __dict__ yet (e.g. slot descriptors that getattr resolves but
        # vars() misses).
        for param_name in params - seen:
            if param_name not in ctor:
                attr_val = getattr(instance, param_name, None)
                if isinstance(attr_val, Fluid):
                    resolved = _resolve_value(attr_val)
                    if resolved is not attr_val:
                        try:
                            setattr(instance, param_name, resolved)
                        except (AttributeError, TypeError):
                            pass  # Read-only property or __slots__

        return instance

    # 4. Bare type passed directly (e.g., flow(MyClass, x=1))
    if isinstance(obj, type):
        if get_registry().is_configurable(obj):
            cls_name = getattr(obj, "__confluid_name__", obj.__name__)
            marker = {"_confluid_class_": cls_name, **runtime_kwargs}
            return materialize(marker, context=context)
        else:
            return obj(**runtime_kwargs)

    # 5. Reference objects — resolve from context
    if isinstance(obj, Reference):
        obj_context = context
        if obj_context and obj.target in obj_context:
            return flow(obj_context[obj.target], **runtime_kwargs)
        # Try dotted path / method call resolution
        if obj_context:
            from confluid.loader import _resolve_dotted_ref

            dotted = _resolve_dotted_ref(obj.target, obj_context)
            if dotted is not None:
                return dotted
        # Fallback: try resolver for nested paths
        resolver = Resolver(context=obj_context or {})
        resolved = resolver._resolve_ref(obj.target)
        if resolved is not None and resolved != f"!ref:{obj.target}":
            return flow(resolved, **runtime_kwargs)
        raise ValueError(f"Cannot resolve Reference: {obj.target}")

    # 5b. Clone objects — resolve reference then deepcopy
    if isinstance(obj, Clone):
        from copy import deepcopy

        resolved = flow(Reference(obj.target), **runtime_kwargs)
        cloned = deepcopy(resolved)
        for k, v in obj.kwargs.items():
            setattr(cloned, k, v)
        return cloned

    # 6. Generic Fluid fallback — treat as Class if target resolves to a type
    if isinstance(obj, Fluid):
        target = obj.target
        if isinstance(target, str):
            resolved = resolve_class(target)
            if resolved is not None:
                base_kwargs = {**obj.kwargs, **runtime_kwargs}
                return resolved(**base_kwargs)
            raise ValueError(f"Class '{target}' not found in registry.")
        return flow(target, **{**obj.kwargs, **runtime_kwargs})

    # 7. Marker dictionaries (legacy format)
    if isinstance(obj, dict) and ("_confluid_class_" in obj or "_confluid_ref_" in obj):
        if "_confluid_class_" in obj and runtime_kwargs:
            obj = {**obj, **runtime_kwargs}
        return materialize(obj, context=context)

    # 8. String tags ("!class:Name" or "!ref:path")
    if isinstance(obj, str) and (obj.startswith("!class:") or obj.startswith("!ref:")):
        resolver = Resolver(context=context)
        resolved = resolver.resolve(obj)
        if isinstance(resolved, str) and (resolved.startswith("!class:") or resolved.startswith("!ref:")):
            return obj
        return flow(resolved, **runtime_kwargs)

    return obj
