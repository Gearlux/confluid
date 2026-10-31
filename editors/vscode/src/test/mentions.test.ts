// Choosing the interpreter, finding the mention under the cursor, a document's imports, a link's tooltip.

import assert from "node:assert/strict";
import * as path from "node:path";
import { test } from "node:test";
import { Mention } from "../helper";
import { findPython, importsOf, linkTooltip, mentionAt } from "../mentions";

const mention = (overrides: Partial<Mention>): Mention => ({
  kind: "class",
  text: "pkg.Model",
  line: 0,
  start: 10,
  stop: 19,
  target: null,
  deferred: false,
  ...overrides,
});

test("the setting wins, with ~ read as the home folder", () => {
  const never = () => false;
  assert.equal(findPython("/w/p/config", "  /opt/py/bin/python ", never), "/opt/py/bin/python");
  assert.equal(findPython("/w/p/config", "~/venvs/x/bin/python", never, "darwin", "/home/me"), "/home/me/venvs/x/bin/python");
});

test("without the setting, the nearest .venv above the file", () => {
  const files = new Set([path.resolve("/w/.venv/bin/python"), path.resolve("/w/p/.venv/bin/python")]);
  const isFile = (file: string) => files.has(file);
  assert.equal(findPython("/w/p/config/deep", "", isFile, "darwin"), path.resolve("/w/p/.venv/bin/python"));
  assert.equal(findPython("/w/q", "", isFile, "darwin"), path.resolve("/w/.venv/bin/python"));
});

test("without a .venv, python3 from the PATH — python and Scripts on Windows", () => {
  assert.equal(findPython("/w/p", "", () => false, "linux"), "python3");
  assert.equal(findPython("/w/p", "", () => false, "win32"), "python");
  const windows = path.join(path.resolve("/w"), ".venv", "Scripts", "python.exe");
  assert.equal(findPython("/w/p", "", (file) => file === windows, "win32"), windows);
});

test("the mention under a position, both ends of its range included", () => {
  const first = mention({ line: 2, start: 4, stop: 9 });
  const second = mention({ line: 2, start: 12, stop: 20, text: "b.yaml", kind: "include" });
  const mentions = [first, second];
  assert.equal(mentionAt(mentions, 2, 4), first);
  assert.equal(mentionAt(mentions, 2, 9), first);
  assert.equal(mentionAt(mentions, 2, 15), second);
  assert.equal(mentionAt(mentions, 2, 10), undefined);
  assert.equal(mentionAt(mentions, 3, 5), undefined);
});

test("a document's imports, each once, in order", () => {
  const mentions = [
    mention({ kind: "import", text: "pkg.ops" }),
    mention({ kind: "class", text: "Model" }),
    mention({ kind: "import", text: "pkg.more" }),
    mention({ kind: "import", text: "pkg.ops" }),
  ];
  assert.deepEqual(importsOf(mentions), ["pkg.ops", "pkg.more"]);
});

test("a link's tooltip says what clicking it does", () => {
  assert.equal(linkTooltip(mention({ target: { path: "/m.py", line: 3 } })), "Go to pkg.Model");
  assert.equal(linkTooltip(mention({ text: "Trainer", deferred: true })), "Find Trainer (imports the packages that register it)");
  assert.equal(linkTooltip(mention({ kind: "include", text: "base.yaml" })), "Open base.yaml");
  assert.equal(linkTooltip(mention({ kind: "import", text: "pkg.ops" })), "Open the module pkg.ops");
});
