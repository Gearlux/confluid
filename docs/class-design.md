# Class Design: Lazy Init & Zero-Arg Construction

Confluid configures objects **after** they are built, so every `@configurable` class is designed
to be cheap and side-effect-free to construct, then configured. Four rules:

1. **Lazy constructor — no functional work.** `__init__` only *stores* values. No I/O, network,
   file reads, dataset/model materialization, or heavy compute. Real work is deferred to a property
   or method.
2. **Zero-arg construction works.** `Cls()` must succeed — every parameter is defaulted. A value
   genuinely required to *run* is still a defaulted parameter, validated **lazily** (where it is
   used) with a clear error, not in `__init__`.
3. **Derived state → read-only `@property`, recomputed.** State derived from the configurable
   inputs is a read-only property (not a stored attribute), so it never goes stale when the inputs
   change. Read-only properties are invisible to Confluid's config surface — never set by
   `configure`, never `dump`ed, rebuilt after `load()`. Cache (into a private `_field`) **only** an
   expensive external materialization whose inputs are stable by first use.
4. **Params stay in the constructor**, documented in the `Args:` docstring — so they remain visible
   to static introspection (`to_pydantic`, `parse_param_docs` — the schema/help surface GUIs and
   agents read).

```python
from typing import Any, Optional
from confluid import configurable

@configurable
class DataSource:
    def __init__(self, path: str = "", split: str = "train") -> None:
        # Lazy: store config only — no load here. `DataSource()` is valid.
        self.path = path
        self.split = split
        self._data: Optional[Any] = None   # private cache for the expensive materialization

    @property
    def data(self) -> Any:
        """Loaded on first access and cached (reset `_data` to reload).

        A required-at-use value (`path`) is validated here, lazily — not in `__init__`.
        """
        if self._data is None:
            if not self.path:
                raise ValueError("DataSource.path is empty — set it before reading `data`.")
            self._data = _load(self.path, self.split)   # the real, expensive work
        return self._data

    @property
    def size(self) -> int:
        """Derived, cheap → a *recomputing* property (never stale, never cached)."""
        return len(self.data)
```

> **Why recompute by default?** The configuration machinery itself never executes property
> getters — both `flow()` and `configure()` walk `vars(obj)` (instance attributes only). But
> *other* `dir()`-based instance walkers do (e.g. `get_hierarchy_from_instance`), and ordinary
> domain code reads properties after reconfiguration — a
> *cached* property that derives from a *post-configured* attribute would hand them a frozen
> pre-configuration value. Recompute unless the work is expensive and its inputs are
> construction-time-stable.

## Runnable examples

Three end-to-end scripts in [`examples/`](../examples/) exercise this convention:

- [`examples/basic_registration.py`](../examples/basic_registration.py) — registering classes + the lazy/zero-arg constructor convention in its minimal form.
- [`examples/ml_pipeline.py`](../examples/ml_pipeline.py) — lazy-init/zero-arg `@configurable` classes + post-construction configuration.
- [`examples/reproducible_experiment.py`](../examples/reproducible_experiment.py) — round-trip reproducibility: `dump()` + `load()` of lazy zero-arg configs.
