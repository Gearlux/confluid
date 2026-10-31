// The client against the REAL helper: `python -m confluid.locate --serve` with the Python the extension would pick
// for this folder (the nearest .venv), or the one `CONFLUID_PYTHON` names.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";
import { HelperClient, pythonSpawner } from "../helper";
import { findPython } from "../mentions";

const isFile = (file: string) => fs.existsSync(file) && fs.statSync(file).isFile();
const python = findPython(__dirname, process.env.CONFLUID_PYTHON ?? "", isFile);
const probe = spawnSync(python, ["-c", "import confluid.locate"], { encoding: "utf8" });
const missing = probe.status === 0 ? false : `${python} cannot import confluid.locate: ${probe.stderr || probe.error}`;

test("the real helper reads a config's classes and includes", { skip: missing }, async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "confluid-yaml-"));
  fs.writeFileSync(path.join(work, "pyproject.toml"), "");
  fs.writeFileSync(path.join(work, "base.yaml"), "a: 1\n");
  const config = path.join(work, "run.yaml");
  const text = "include: base.yaml\nloader: !class:confluid.loader.load\nmodel: !class:Unregistered\n";
  const env = { ...process.env, LOGGAIR_DIR: path.join(work, "logs") };
  const client = new HelperClient(pythonSpawner(python, work, env));
  try {
    const mentions = await client.locate(config, text, 60_000);
    assert.deepEqual(
      mentions.map((m) => [m.kind, m.text, m.line, m.start, m.stop, m.deferred]),
      [
        ["include", "base.yaml", 0, 9, 18, false],
        ["class", "confluid.loader.load", 1, 15, 35, false],
        ["class", "Unregistered", 2, 14, 26, true],
      ],
    );
    assert.equal(fs.realpathSync(mentions[0].target!.path), fs.realpathSync(path.join(work, "base.yaml")));
    assert.match(mentions[1].target!.path, /confluid[\\/]loader\.py$/);
  } finally {
    client.dispose();
    fs.rmSync(work, { recursive: true, force: true });
  }
});
