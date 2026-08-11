# Error Handling

Every error Confluid raises is typed, rooted at `ConfluidError`, so callers can catch configuration failures distinctly:

```python
import confluid

try:
    app = confluid.load("config.yaml")
except confluid.ConfigFileNotFoundError:
    ...  # the config file (or an include) does not exist
except confluid.ConfigurationError:
    ...  # bad content: unknown !class:, unresolvable !ref:, circular include, ...
except confluid.ConfluidError:
    ...  # any other confluid-specific failure
```

Every concrete class **also inherits the builtin it replaces**, so pre-existing `except ValueError:` / `except FileNotFoundError:` code (and `pytest.raises(ValueError)` tests) keep working unchanged.

## Errors name the line that caused them

An error raised while processing a document carries `file:line:col`, so a failure is one click
away instead of a traceback to read and a config tree to grep:

```console
UnknownClassError: Cannot resolve class: waivefront.sources.HDF5WindwoSource
                   at /path/to/config/utils/hdf5_rewindow.yaml:22:7
```

That is the exact shape a rename leaves behind, and the reported node is the **offending one** —
a mistyped source nested inside a pipeline's kwargs reports its own line, not the enclosing
marker's. Loading from a string rather than a file reports `<unicode string>:33:12`; the line and
column still locate the node.

The location comes from the marker (`confluid.format_yaml_loc(fluid)` renders it, and is public
so your own code can quote it in messages about a config node). A node built in Python rather
than parsed from YAML has no location, and the suffix is simply omitted.

| Exception | Also a | Raised when |
|---|---|---|
| `ConfigurationError` | `ValueError` | base for config-content errors (all seven below) |
| `CircularIncludeError` | `ValueError` | an `include:` chain revisits a file |
| `ReferenceResolutionError` | `ValueError` | a `!ref:` cannot be resolved (unknown or self-referential) |
| `UnknownClassError` | `ValueError` | a `!class:` target is neither registered nor importable |
| `AmbiguousClassError` | `ValueError` | a `!class:` target names a class registered by SEVERAL classes and nothing narrows the choice |
| `ConfigurableDefinitionError` | `ValueError` | a `@configurable` declaration is contradictory |
| `ValidationModeError` | `ValueError` | a `CONFLUID_VALIDATE_*` env var holds an unknown mode |
| `ScopeError` | `ValueError` | a scope alias chain is circular; a block's body shape cannot splice where it sits; an activation names a value no block declares |
| `ConfigFileNotFoundError` | `FileNotFoundError` | a config or included file is missing |
| `ConstructionError` | `RuntimeError` | a target's constructor failed and the original exception class cannot be rebuilt (original chained via `__cause__`) |
| `WorkspaceEnvError` | `RuntimeError` | no `.env` found / a required key is unset / a path-typed value is missing |
| `IntrospectionError` | `TypeError` | a class or callable cannot be introspected for schema export |

`AmbiguousClassError` is a **sibling** of `UnknownClassError`, not a subclass — "unknown"
and "ambiguous" are opposite failures, and code catching the former to fall back to an
import must not swallow the latter. Its message lists every candidate with the tags that
tell them apart, and the three ways to choose one:

```console
confluid.AmbiguousClassError: 'FourierOp' is registered by 2 classes, so a bare lookup
cannot choose between them:
  myops.numpy.FourierOp (category=op, group=fft/numpy)
  myops.torch.FourierOp (category=op, group=fft/torch)
Disambiguate with the dotted path (!class:myops.numpy.FourierOp), a tag selector
(!class:FourierOp@group=fft/numpy) or get_class('FourierOp', group='fft/numpy').
```

See [Discovery](discovery.md) for when two classes may share a name in the first place.

`ScopeError` covers three unrelated conditions, so read the message rather than the type.
The one worth calling out is an activation that matches nothing:

```console
confluid.ScopeError: No scope block matches task='classifcation'. This document declares
task with: classification, segmentation. Either use one of those values, or add a
`!scope:task=classifcation` block.
```

It exists because the alternative is silence — a value matching no block used to resolve to
the document's *unscoped* keys, so a typo ran the default configuration and reported nothing.
Note that an entirely **undeclared** dimension is still an inert no-op, and a dimension with
any `!notscope:` block accepts every value; see [Scopes](scopes.md) for why.

Note: a failing constructor normally re-raises with the **original** exception class (`Failed to construct X: ...`) — `ConstructionError` is only the fallback for exception classes that cannot be rebuilt from a plain message (e.g. pydantic's `ValidationError`).

## Runnable example

[`examples/error_handling.py`](../examples/error_handling.py) triggers the most
common failures (missing file, unknown class, unresolvable reference) and shows
that each typed exception is also catchable as the builtin it replaces.
