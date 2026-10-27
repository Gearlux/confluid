# Config-File Search Paths (XDG)

Confluid resolves every relative config-file path — the path handed to
`load()` at any stage — AND each `include:` entry — through an ordered
list of locations. Local files always win; the XDG base directories are the
last resort. A leading `~` expands to the home directory first; an **absolute path bypasses the search entirely** and is used
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

A file held in memory for the call — `load(path, texts={…})`, see
[Files you hold in memory](interpolation.md#files-you-hold-in-memory--texts) —
counts as existing at its own tier, exactly like a file on disk. For
`include: common.yaml` written in `graph/flow.yaml`:

| Held in memory | On disk | Read |
|---|---|---|
| `graph/common.yaml` | `./common.yaml` | the held `graph/common.yaml` — tier 1 beats tier 2 |
| `./common.yaml` | `graph/common.yaml` | `graph/common.yaml` from disk — a held file does not jump the order |
| `graph/common.yaml` | `graph/common.yaml` | the held text — it is the same file, and the held text wins |

On a total miss, `load(path)` raises `ConfigFileNotFoundError` listing every
location that was searched. (A `Path`, or a one-line string ending in `.yaml` /
`.yml`, always names a FILE — a missing one raises rather than parsing as YAML
text.)

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

`load(path, return_paths=True)` records the **resolved** locations, so run-artifact
logging always names the actual files that contributed.

## Configs shipped by an installed distribution

`pkg://<distribution>/<path>` names a config by the distribution that ships it — the name `pip install` takes — and
its path inside that project's folder. It works as an `include:` entry and as the config handed to `load()` (and to a
CLI built on it):

```yaml
# my-app/config/experiment.yaml
include: pkg://shared-configs/config/datasets/base.yaml
epochs: 5
```

No tier is searched. Where the file is read depends on how the distribution is installed:

| `shared-configs` installed | `pkg://shared-configs/config/datasets/base.yaml` reads |
|---|---|
| editable (`pip install -e ./shared-configs`) | `<the checkout>/config/datasets/base.yaml` — the file you edit |
| from its wheel | `<prefix>/share/shared-configs/config/datasets/base.yaml` — the file the wheel installed |
| not at all | `ConfigFileNotFoundError: … includes pkg://shared-configs/config/datasets/base.yaml: the distribution 'shared-configs' is not installed` |
| from a wheel without that file | `ConfigFileNotFoundError: … includes pkg://shared-configs/…/base.yaml: shared-configs 1.2 ships no config/datasets/base.yaml` |

Only the named distribution's files are read: `pkg://shared/…` never finds a file `shared-5g` ships. The name is
matched as installers normalize it (`shared_configs` and `Shared.Configs` are `shared-configs`). A relative include
inside a shipped file resolves next to it, so a distribution's own includes stay relative. A reference with no
distribution, no path or a `..` is refused with a `ConfigurationError`. An unopened scope block's `pkg://` include is
never looked up.

**Shipping configs in a wheel.** List each config as wheel data under `share/<distribution>/`, keeping its path in the
project — with setuptools, in `setup.py`:

```python
from pathlib import Path

from setuptools import setup

DIST = "shared-configs"  # the normalized name: lowercase, `-` for every run of `-`, `_` and `.`
here = Path(__file__).parent
files = sorted(p.relative_to(here) for top in ("config", "examples") for p in (here / top).rglob("*.yaml"))
setup(data_files=[(f"share/{DIST}/{d}", [str(f) for f in files if f.parent == d]) for d in sorted({f.parent for f in files})])
```

## Runnable example

[`examples/search_paths.py`](../examples/search_paths.py) builds a sandboxed
`XDG_CONFIG_HOME` in a temp directory and demonstrates the tier order, the
app-name namespacing, `resolve_config_path`, and a `pkg://` include of a config a (stand-in) wheel installed.
