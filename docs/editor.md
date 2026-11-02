# Editor Navigation

A config names classes, files and modules. `confluid.locate` says where each one points, for an editor's
go-to-definition; the **Confluid YAML** extension for VS Code (`editors/vscode/`) puts it on Cmd+click and F12.

```yaml
include: pkg://my-project/config/base.yaml        # → the file that distribution ships
import: [my_project.ops]                          # → my_project/ops.py
model: !class:my_project.models.Net(width=4)      # → `class Net` in my_project/models.py
loss: {_target_: my_project.losses.Focal}         # the plain spelling works the same
trainer: !partial:Trainer                         # a registry name: found on click
steps:
  - config: config/train.yaml                     # → the config another run reads, from the project folder
  - config: pkg://my-project/config/eval.yaml     # → a pkg:// value is the file it names, under any key
```

## What points where

| Written | Points to | Found |
|---|---|---|
| `!class:` / `!partial:` / `!lazy:` with a dotted name, `_target_:` | the `class` or `def` line | in the source, at once — nothing is imported; a package's `from .x import Name`, `from .x import *` and lazy-export tables (`{"Name": "pkg.module"}` beside a `__getattr__`) are followed |
| the same with a registry name (`!class:Trainer`), or a class made at run time | its `class` or `def` line | on request: the first lookup imports every package that registers confluid classes |
| `include:` entry | the file | as `load()` finds it: beside the file, then its project (the nearest folder with a `pyproject.toml`) and the project's `config/`, then the XDG folders ([search paths](search-paths.md)); `pkg://` through the installed distribution |
| a `pkg://<distribution>/<path>` value under any key, or an item of a list | the file | through the installed distribution, as for an `include:` — the value must be the whole string (`hint: run pkg://…` is text) |
| `config:` value ending `.yaml` or `.yml` | the file | as a run started from the project folder reads it: the project and its `config/`, then the XDG folders — never beside the file that names it |
| `import:` module | the module's file | in the source, nothing imported |

A call suffix (`Net(width=4)`) is not part of the name; a selector (`Focal@task=detection`) is, and picks the class
the registry would build. A name that points nowhere — an include or a `config:` path that is missing or interpolated
(`${env:…}`), a `pkg://` file the distribution does not ship, a module not installed — has no target.

## From Python

```python
from confluid.locate import locate, resolve_target

text = "reader: !class:confluid.loader.load\ntrainer: !partial:Trainer\n"
for m in locate(text, "config/experiment.yaml"):     # the text need not be saved
    print(m.kind, m.text, m.line, m.start, m.stop, m.target, m.deferred)
# class confluid.loader.load 0 15 35 Location(path='.../confluid/loader.py', line=1268) False
# class Trainer 1 18 25 None True

resolve_target("Trainer")   # Resolution(target=Location(...), reason=None) when a package registers a Trainer
```

Lines are 0-based; `start` and `stop` are columns counted as an editor counts them (UTF-16 code units).
`resolve_target` imports every `confluid.configurables` entry point the first time it is called in a process —
a minute in a large environment — and the document's `import:` modules; it answers a name two classes share with
the registry's message instead of a place.

## The command

```bash
python -m confluid.locate config/experiment.yaml     # the mentions of a file, as JSON
python -m confluid.locate --serve                    # the editor protocol, one JSON request per line
```

`--serve` reads `{"id": 1, "method": "locate", "params": {"path": "...", "text": "..."}}` and
`{"id": 2, "method": "resolve", "params": {"name": "Trainer", "imports": []}}`, and answers each with
`{"id": 1, "result": ...}` (or `{"id": 1, "error": "..."}`), one line each. Anything an imported package prints goes
to stderr, never into the answers.

## The VS Code extension

```bash
cd editors/vscode
npm install
npm run package
code --install-extension confluid-yaml.vsix
```

`code --install-extension` installs into VS Code's default profile; a workspace that opens in another profile needs
`--profile <name>` on the same command (`code --list-extensions --profile <name>` shows what a profile has).

It runs the helper with the `confluid.pythonPath` setting, else the nearest `.venv` above the config file, else
`python3`. Its messages are in the **Confluid** output panel; **Confluid: Restart the Helper** starts it again after
you install packages. `npm test` runs its tests; `npm run test:vscode` runs them inside this machine's VS Code, with
a profile of their own. Everything else about the extension — settings, updating, what to do when a click does
nothing — is in its own [README](../editors/vscode/README.md).

Example: [`examples/locate.py`](../examples/locate.py).
