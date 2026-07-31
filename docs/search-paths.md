# Config-File Search Paths (XDG)

Confluid resolves every relative config-file path — the path handed to
`load()` / `load_config()` AND each `include:` entry — through an ordered
list of locations. Local files always win; the XDG base directories are the
last resort. An **absolute path bypasses the search entirely** and is used
verbatim.

## Resolution order

For a relative path `name.yaml`, the first existing candidate wins:

1. **The including file's directory** (include resolution only) — an
   `include: common.yaml` next to the including file behaves exactly as it
   always has.
2. **The current working directory** — `./name.yaml`.
3. **`./config/`** — the conventional per-project config folder.
4. **The XDG base directories**, in spec order:
   `$XDG_CONFIG_HOME` (default `~/.config`), then each colon-separated entry
   of `$XDG_CONFIG_DIRS` (default `/etc/xdg`). An empty environment variable
   is treated as unset, per the
   [XDG Base Directory spec](https://specifications.freedesktop.org/basedir-spec/latest/).

On a total miss, `load_config` raises `ConfigFileNotFoundError` listing every
location that was searched.

## Namespacing with an app name

Within each XDG base directory, the lookup is namespaced by an optional
process-wide **app name**:

| App name | Locations searched under each XDG base dir |
| --- | --- |
| `set_app_name("my-app")` | `<base>/my-app/name.yaml`, then `<base>/confluid/name.yaml` |
| not set (default) | `<base>/name.yaml` (un-namespaced) |

```python
import confluid

confluid.set_app_name("my-app")   # a CLI framework typically calls this once at startup
confluid.load("experiment.yaml")  # may resolve to ~/.config/my-app/experiment.yaml
```

`get_app_name()` returns the current value; `set_app_name(None)` resets it.

> **Set an app name.** Without one, the un-namespaced tier means
> `include: common.yaml` can pick up `~/.config/common.yaml` — a file any
> tool might own. A namespaced directory keeps your configs yours.

## Resolving a path yourself

`resolve_config_path(path)` is the public resolver behind the lookup —
useful when a CLI wants to resolve a user-supplied config path once and
display/store the real location:

```python
from confluid import resolve_config_path

real = resolve_config_path("experiment.yaml")   # first existing candidate,
                                                # or the input path on a total miss
```

Shared include files can therefore live in `~/.config/my-app/` instead of
being copied next to every experiment config:

```yaml
# ./experiment.yaml
include: common.yaml    # not found locally -> resolves to ~/.config/my-app/common.yaml
lr: 0.001
```

`load_config_with_paths` records the **resolved** locations, so run-artifact
logging always names the actual files that contributed.

## Runnable example

[`examples/search_paths.py`](../examples/search_paths.py) builds a sandboxed
`XDG_CONFIG_HOME` in a temp directory and demonstrates the tier order, the
app-name namespacing, and `resolve_config_path`.
