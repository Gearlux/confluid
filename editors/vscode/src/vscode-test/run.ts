// `npm run test:vscode`: the extension inside a real VS Code — this machine's installed one, never a download —
// with a throwaway profile (its own settings and extensions folders), on a fixture project made for the run.
// It opens a VS Code window for a few seconds. VSCODE_PATH names the executable when it is not in the default place;
// CONFLUID_PYTHON the Python with confluid (default: the nearest .venv above this folder).

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { runTests } from "@vscode/test-electron";
import { findPython } from "../mentions";

const DEFAULT_EXECUTABLES: Partial<Record<NodeJS.Platform, string>> = {
  darwin: "/Applications/Visual Studio Code.app/Contents/MacOS/Code",
  linux: "/usr/share/code/code",
  win32: path.join(process.env.LOCALAPPDATA ?? "", "Programs", "Microsoft VS Code", "Code.exe"),
};

async function main(): Promise<void> {
  const executable = process.env.VSCODE_PATH ?? DEFAULT_EXECUTABLES[process.platform] ?? "";
  if (!fs.existsSync(executable)) {
    throw new Error(`no VS Code at ${JSON.stringify(executable)} — set VSCODE_PATH to its executable`);
  }
  const isFile = (file: string) => fs.existsSync(file) && fs.statSync(file).isFile();
  const python = findPython(__dirname, process.env.CONFLUID_PYTHON ?? "", isFile);
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "confluid-yaml-vscode-"));
  const project = path.join(scratch, "project");
  fs.mkdirSync(path.join(project, "config"), { recursive: true });
  fs.writeFileSync(path.join(project, "pyproject.toml"), "");
  fs.writeFileSync(path.join(project, "config", "base.yaml"), "a: 1\n");
  fs.writeFileSync(
    path.join(project, "config", "run.yaml"),
    "include: base.yaml\nloader: !class:confluid.loader.load\nmodel: !class:Unregistered\nplain: 1\n",
  );
  try {
    await runTests({
      vscodeExecutablePath: executable,
      extensionDevelopmentPath: path.resolve(__dirname, "..", ".."),
      extensionTestsPath: path.resolve(__dirname, "suite.js"),
      extensionTestsEnv: { CONFLUID_TEST_PROJECT: project, CONFLUID_PYTHON: python },
      launchArgs: [
        project,
        "--disable-extensions",
        "--disable-workspace-trust",
        `--user-data-dir=${path.join(scratch, "user-data")}`,
        `--extensions-dir=${path.join(scratch, "extensions")}`,
      ],
    });
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
