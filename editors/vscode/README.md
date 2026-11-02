# Confluid YAML

Go to definition in a [confluid](https://github.com/Gearlux/confluid) config: Cmd+click (Ctrl+click on Windows and
Linux) or F12 on a class, an include or an import opens what it names.

```yaml
include: pkg://my-project/config/base.yaml        # opens the file that distribution ships
import: [my_project.ops]                          # opens my_project/ops.py
model: !class:my_project.models.Net(width=4)      # goes to `class Net` in my_project/models.py
loss: {_target_: my_project.losses.Focal}         # the plain spelling works the same
trainer: !partial:Trainer                         # a registry name: found when you click it
steps:
  - config: config/train.yaml                     # opens the config another run reads
  - config: pkg://my-project/config/eval.yaml     # a pkg:// value opens under any key
```

## What points where

| You click | It opens | How fast |
|---|---|---|
| a dotted class in `!class:`, `!partial:`, `!lazy:` or `_target_:` | the `class` or `def` line | at once — read from the source, nothing imported |
| a registry name (`!class:Trainer`), or a class a package makes at run time | the `class` or `def` line | the first such click imports every package that registers confluid classes (up to a minute in a large environment); later clicks are instant |
| an `include:` entry | the file | at once |
| a `pkg://<distribution>/<path>` value, under any key | the file that distribution ships | at once |
| a `config:` value ending `.yaml` or `.yml` | the config file another run reads | at once |
| an `import:` module | the module's source file | at once |

- **Classes.** A call suffix (`Net(width=4)`) is not part of the name; a selector (`Focal@task=detection`) is, and
  picks the class the registry would build. A package's re-exports (`from .models import Net`, `from .x import *`)
  and its lazy-export tables (a `{"Net": "my_project.models"}` dict beside a `__getattr__`) are followed to the
  module that defines the class.
- **Includes.** A relative entry is looked up as `confluid.load` looks it up: beside the config, then in its
  project — the nearest folder above it with a `pyproject.toml` — and the project's `config/`, then the XDG config
  folders. `pkg://<distribution>/<path>` opens the file the installed distribution ships (the checkout's own file
  for an editable install).
- **Other `pkg://` values and `config:` paths.** A `pkg://` value is a link under any key, in a list too, when it is
  the whole value (`config: pkg://my-project/config/eval.yaml`; `hint: run pkg://…` stays text). A `config:` path
  names the config another run reads, so it is looked up from the project folder — the project, its `config/`, the
  XDG config folders — and never beside the file that names it.
- **Unsaved edits count.** The extension reads the editor's text, not the file on disk.
- **What gets no link:** an include or `config:` path that is missing or holds an interpolation
  (`${env:CONFIGS}/a.yaml`), a `pkg://` file the distribution does not ship, a module that is not installed. F12
  there says "No definition found".

Hold Cmd and hover to see which names are links; the tooltip says what a click does ("Go to …", "Open …", "Find …
(imports the packages that register it)").

## Install

You need a Python with confluid installed. Build the extension and install it:

```bash
cd confluid/editors/vscode
npm install
npm run package
code --install-extension confluid-yaml.vsix
```

`code --install-extension` installs into VS Code's **default profile**. A workspace that opens in another profile
does not see it; name that profile:

```bash
code --install-extension confluid-yaml.vsix --profile Taidal
```

`code --list-extensions --profile <name>` shows what a profile has. The extension starts by itself when you open a
YAML file; a window that was already open picks it up without a reload.

**Update** — the same `npm run package` and `code --install-extension …` (with `--profile`), then
**Confluid: Restart the Helper**. **Uninstall** — `code --uninstall-extension gearlux.confluid-yaml` (with
`--profile <name>` for another profile).

## Settings and commands

| | |
|---|---|
| `confluid.pythonPath` | The Python that has confluid. Empty (the default): the nearest `.venv` above the config file (`.venv/bin/python`, `.venv\Scripts\python.exe` on Windows), else `python3` (`python` on Windows) on the `PATH`. A leading `~/` is your home folder. |
| **Confluid: Restart the Helper** | Starts the Python helper again — after you install or change packages, so it sees the new classes. |

## How it works

The extension starts one background process per Python:

```bash
python -m confluid.locate --serve
```

It sends the editor's text and gets back every class, include and import with its range and target, one JSON line
each way. The reading is confluid's own: the same include rules as `confluid.load`, and a class looked up in its
source file — the import system's finders walked and the file parsed, nothing imported. A registry name needs the
packages that register it, so the helper imports them on the first such click and keeps them for the rest of the
window.

- **Output panel** (View → Output → **Confluid**) — which Python started (`starting the helper with …`), and
  everything the helper prints, its errors included.
- **The helper's own log file** — `~/Library/Application Support/Code/User/globalStorage/gearlux.confluid-yaml/logs/`
  on macOS (VS Code's global storage folder elsewhere); it is never written into your project.

The same reading is available from the command line and from Python:

```bash
python -m confluid.locate config/experiment.yaml   # every mention of a file, as JSON
```

The confluid guide describes it: [Editor Navigation](https://github.com/Gearlux/confluid/blob/main/docs/editor.md).

## When something does not work

| You see | Why | Do |
|---|---|---|
| Cmd+click does nothing, and the **Confluid** output panel is missing or empty | The window's profile does not have the extension | `code --list-extensions --profile <name>`; install with `--profile <name>` |
| `No module named confluid.locate` in the output panel | The Python it picked has no confluid, or an older one | Set `confluid.pythonPath`, or install confluid into that `.venv`; then **Confluid: Restart the Helper** (after a failed start it waits 30 s before trying again on its own) |
| `confluid: finding Trainer…` in the status bar for a long time | The first registry-name click imports every registering package | Wait; later clicks are instant |
| `confluid: 'Trainer' is registered by 2 classes …`, then VS Code's "Failed to open this link because its target is missing" | Two classes share the name | Write the dotted path or a selector (`Trainer@framework=torch`), as the message says |
| `confluid: no class or function named 'X' is registered or importable` | No installed package defines or registers it | Check the name; install the package; **Restart the Helper** |
| A class you just added is not found | The helper imported the packages before you added it | **Confluid: Restart the Helper** |

Limits: only files on disk (`file:` documents) are read; a window on a remote machine (SSH, tunnel) has not been
tried — the extension and confluid would both have to be installed on that machine.

## Development

| File | What it does |
|---|---|
| `src/extension.ts` | The VS Code side: a link provider (Cmd+click), a definition provider (F12), the restart command, a cache per document version |
| `src/helper.ts` | The helper's client: one JSON request per line, answers matched by id, timeouts, restart after the helper ends, a 30 s back-off when it never answered |
| `src/mentions.ts` | Choosing the Python, finding the mention under the cursor, a document's `import:` modules, the tooltips |
| `src/test/` | Node tests: the client against a fake process, the choices above, and one run against the real helper |
| `src/vscode-test/` | The extension inside this machine's VS Code, with a throwaway profile, on a fixture project |

```bash
npm test               # the Node tests
npm run test:vscode    # inside the installed VS Code — a window opens for a few seconds; VSCODE_PATH names another executable
npm run package        # confluid-yaml.vsix
```

`CONFLUID_PYTHON` names the Python both test runs use (default: the nearest `.venv`). CI runs `npm test` in its Node
Tests stage (`npm ci` first on Jenkins and GitHub; the local nightly tests the checkout's own `node_modules`, so run
`npm install` here once). `test:vscode` needs a display and an installed VS Code, and stays a run of your own. The
Python side, `confluid/locate.py`, is tested by confluid's own suite (`tests/test_locate.py`).
