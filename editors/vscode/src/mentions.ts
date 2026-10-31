// What the extension decides without VS Code: which Python runs the helper, which mention a position is on, and
// which modules a document imports. Tested under plain Node (src/test/mentions.test.ts).

import * as path from "node:path";
import { Mention } from "./helper";

/**
 * The interpreter that runs the helper for a config file in `folder`.
 *
 * The `confluid.pythonPath` setting when given (a `~` at its start is the home folder); else the nearest
 * `.venv` above the file (`.venv/bin/python`, `.venv\Scripts\python.exe` on Windows); else `python3` (`python` on
 * Windows) from the `PATH`.
 */
export function findPython(
  folder: string,
  configured: string,
  isFile: (file: string) => boolean,
  platform: NodeJS.Platform = process.platform,
  home: string = process.env.HOME ?? "",
): string {
  const setting = configured.trim();
  if (setting) {
    return setting.startsWith("~/") ? path.join(home, setting.slice(2)) : setting;
  }
  const relative = platform === "win32" ? path.join(".venv", "Scripts", "python.exe") : path.join(".venv", "bin", "python");
  let current = path.resolve(folder);
  for (;;) {
    const candidate = path.join(current, relative);
    if (isFile(candidate)) {
      return candidate;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return platform === "win32" ? "python" : "python3";
    }
    current = parent;
  }
}

/** The mention whose range holds the position (`line`, `character`, 0-based), its last column included. */
export function mentionAt(mentions: Mention[], line: number, character: number): Mention | undefined {
  return mentions.find((m) => m.line === line && m.start <= character && character <= m.stop);
}

/** The modules a document's `import:` entries name — imported before a deferred class is looked up. */
export function importsOf(mentions: Mention[]): string[] {
  return [...new Set(mentions.filter((m) => m.kind === "import").map((m) => m.text))];
}

/** The hover text of a link: what clicking it opens. */
export function linkTooltip(mention: Mention): string {
  if (mention.kind === "class") {
    return mention.target ? `Go to ${mention.text}` : `Find ${mention.text} (imports the packages that register it)`;
  }
  return mention.kind === "include" ? `Open ${mention.text}` : `Open the module ${mention.text}`;
}
