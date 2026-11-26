// Runs inside the VS Code that run.ts starts: the extension's links and definitions on the fixture project, asked
// through VS Code's own commands, as the editor asks for them on Cmd+click and F12.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

const same = (a: string, b: string) => fs.realpathSync(a) === fs.realpathSync(b);

export async function run(): Promise<void> {
  const project = process.env.CONFLUID_TEST_PROJECT as string;
  await vscode.workspace
    .getConfiguration("confluid")
    .update("pythonPath", process.env.CONFLUID_PYTHON, vscode.ConfigurationTarget.Global);
  const uri = vscode.Uri.file(path.join(project, "config", "run.yaml"));
  const document = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(document);
  assert.equal(document.languageId, "yaml");
  // Opening a YAML file activates the extension, asynchronously: wait for it, as the editor's next request would.
  await vscode.extensions.getExtension("gearlux.confluid-yaml")!.activate();

  // No link is resolved here (0): resolving the registry name would import every registering package.
  const links = await vscode.commands.executeCommand<vscode.DocumentLink[]>("vscode.executeLinkProvider", uri, 0);
  const seen = links.map((link) => [document.getText(link.range), link.target?.fragment ?? null]);
  assert.deepEqual(seen.map(([text]) => text), ["base.yaml", "confluid.loader.load", "Unregistered"]);
  assert.ok(same(links[0].target!.fsPath, path.join(project, "config", "base.yaml")));
  assert.equal(links[0].target!.fragment, "L1");
  assert.match(links[1].target!.fsPath, /confluid[\\/]loader\.py$/);
  assert.equal(links[2].target, undefined); // deferred: found on click

  const definition = async (line: number, character: number) =>
    vscode.commands.executeCommand<vscode.Location[]>("vscode.executeDefinitionProvider", uri, new vscode.Position(line, character));
  const onClass = await definition(1, 20);
  assert.equal(onClass.length, 1);
  assert.match(onClass[0].uri.fsPath, /confluid[\\/]loader\.py$/);
  const source = fs.readFileSync(onClass[0].uri.fsPath, "utf8").split("\n");
  assert.match(source[onClass[0].range.start.line], /^def load\(/);
  const onInclude = await definition(0, 12);
  assert.ok(same(onInclude[0].uri.fsPath, path.join(project, "config", "base.yaml")));
  assert.deepEqual(await definition(3, 2), []);
}
