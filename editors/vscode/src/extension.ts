// Confluid YAML: Cmd+click / F12 on a confluid config's names. The reading is done by the Python helper
// (`python -m confluid.locate --serve`, confluid's own include rules and source reading); this file only turns its
// mentions into VS Code links and definitions.

import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { HelperClient, Location, Mention, pythonSpawner } from "./helper";
import { findPython, importsOf, linkTooltip, mentionAt } from "./mentions";

const SELECTOR: vscode.DocumentSelector = [{ scheme: "file", language: "yaml" }];

/** A link whose class the helper has yet to find: resolved on click. */
class DeferredLink extends vscode.DocumentLink {
  constructor(
    range: vscode.Range,
    readonly mention: Mention,
    readonly document: vscode.TextDocument,
  ) {
    super(range);
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("Confluid");
  const clients = new Map<string, HelperClient>();
  const cache = new Map<string, { version: number; mentions: Promise<Mention[]> }>();
  const storage = context.globalStorageUri.fsPath;
  fs.mkdirSync(storage, { recursive: true });

  const clientFor = (document: vscode.TextDocument): HelperClient => {
    const configured = vscode.workspace.getConfiguration("confluid", document.uri).get<string>("pythonPath", "");
    const python = findPython(path.dirname(document.uri.fsPath), configured, isFile);
    let client = clients.get(python);
    if (client === undefined) {
      output.appendLine(`starting the helper with ${python}`);
      // The helper's own log files go to the extension's storage, never into the user's project.
      const env = { ...process.env, LOGGAIR_DIR: path.join(storage, "logs"), PYTHONUNBUFFERED: "1" };
      client = new HelperClient(pythonSpawner(python, storage, env), (line) => output.appendLine(line));
      clients.set(python, client);
    }
    return client;
  };

  const mentionsOf = (document: vscode.TextDocument): Promise<Mention[]> => {
    const key = document.uri.toString();
    const cached = cache.get(key);
    if (cached !== undefined && cached.version === document.version) {
      return cached.mentions;
    }
    const mentions = clientFor(document)
      .locate(document.uri.fsPath, document.getText())
      .catch((error: Error) => {
        output.appendLine(`${document.uri.fsPath}: ${error.message}`);
        cache.delete(key);
        return [] as Mention[];
      });
    cache.set(key, { version: document.version, mentions });
    return mentions;
  };

  const resolve = async (document: vscode.TextDocument, mention: Mention): Promise<Location | undefined> => {
    const imports = importsOf(await mentionsOf(document));
    const found = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Window, title: `confluid: finding ${mention.text}…` },
      () => clientFor(document).resolve(mention.text, imports),
    );
    if (found.target === null) {
      vscode.window.showWarningMessage(`confluid: ${found.reason ?? `cannot find ${mention.text}`}`);
      return undefined;
    }
    return found.target;
  };

  const links: vscode.DocumentLinkProvider<vscode.DocumentLink> = {
    async provideDocumentLinks(document) {
      return (await mentionsOf(document)).flatMap((mention) => {
        const range = new vscode.Range(mention.line, mention.start, mention.line, mention.stop);
        let link: vscode.DocumentLink;
        if (mention.target !== null) {
          link = new vscode.DocumentLink(range, targetUri(mention.target));
        } else if (mention.deferred) {
          link = new DeferredLink(range, mention, document);
        } else {
          return [];
        }
        link.tooltip = linkTooltip(mention);
        return [link];
      });
    },
    async resolveDocumentLink(link) {
      if (link instanceof DeferredLink) {
        const target = await resolve(link.document, link.mention);
        if (target !== undefined) {
          link.target = targetUri(target);
        }
      }
      return link;
    },
  };

  const definitions: vscode.DefinitionProvider = {
    async provideDefinition(document, position) {
      const mention = mentionAt(await mentionsOf(document), position.line, position.character);
      if (mention === undefined) {
        return undefined;
      }
      const target = mention.target ?? (mention.deferred ? await resolve(document, mention) : undefined);
      return target === undefined ? undefined : new vscode.Location(vscode.Uri.file(target.path), new vscode.Position(target.line, 0));
    },
  };

  context.subscriptions.push(
    output,
    vscode.languages.registerDocumentLinkProvider(SELECTOR, links),
    vscode.languages.registerDefinitionProvider(SELECTOR, definitions),
    vscode.commands.registerCommand("confluid.restartHelper", () => {
      cache.clear();
      clients.forEach((client) => client.restart());
      output.appendLine("helper restarted");
    }),
    vscode.workspace.onDidCloseTextDocument((document) => cache.delete(document.uri.toString())),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("confluid.pythonPath")) {
        cache.clear();
        clients.forEach((client) => client.dispose());
        clients.clear();
      }
    }),
    { dispose: () => clients.forEach((client) => client.dispose()) },
  );
}

export function deactivate(): void {
  // Everything is disposed through context.subscriptions.
}

/** A file URI opening at `line` (VS Code reads a `#L<n>` fragment, 1-based, as the line to show). */
function targetUri(target: Location): vscode.Uri {
  return vscode.Uri.file(target.path).with({ fragment: `L${target.line + 1}` });
}

function isFile(file: string): boolean {
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}
