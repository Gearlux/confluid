// The client of `python -m confluid.locate --serve`: one JSON request per line on its stdin, one answer per line on
// its stdout. Nothing here imports `vscode`, so the protocol is tested under plain Node (src/test/helper.test.ts).

import { ChildProcess, spawn } from "node:child_process";

/** A place in a file: its absolute path and a 0-based line. */
export interface Location {
  path: string;
  line: number;
}

/** One name in a config text — `line`, `start`, `stop` 0-based, columns in UTF-16 code units. */
export interface Mention {
  kind: "class" | "include" | "import";
  text: string;
  line: number;
  start: number;
  stop: number;
  target: Location | null;
  /** The target is unknown until the helper imports what registers it (`resolve`). */
  deferred: boolean;
}

/** What `resolve` found: the definition, or null and the reason. */
export interface Resolution {
  target: Location | null;
  reason: string | null;
}

/** The part of a child process the client uses — a real `ChildProcess`, or a fake in the tests. */
export type HelperProcess = Pick<ChildProcess, "stdin" | "stdout" | "stderr" | "kill" | "once">;

/** Starts one helper process. */
export type Spawner = () => HelperProcess;

/** A spawner running `python -m confluid.locate --serve` with the given interpreter, folder and environment. */
export function pythonSpawner(python: string, cwd: string, env: NodeJS.ProcessEnv): Spawner {
  return () => spawn(python, ["-m", "confluid.locate", "--serve"], { cwd, env, stdio: "pipe" });
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout | undefined;
}

/** How long a helper that ended before answering anything is left down before the next start. */
export const RESTART_BACKOFF_MS = 30_000;

/**
 * One helper process, started on the first request and again after it ends.
 *
 * A helper that ends before answering a single request (no confluid with `confluid.locate` in that Python, a wrong
 * path) is not restarted for {@link RESTART_BACKOFF_MS}: every request meanwhile fails at once with the reason, so
 * a document's links never start a process per keystroke.
 */
export class HelperClient {
  private process: HelperProcess | undefined;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private buffer = "";
  private stderrTail: string[] = [];
  private answered = false;
  private downUntil = 0;
  private downReason = "";

  constructor(
    private readonly spawner: Spawner,
    private readonly log: (line: string) => void = () => undefined,
    private readonly now: () => number = Date.now,
  ) {}

  /** The mentions of `text`, the file at `path`. */
  locate(path: string, text: string, timeoutMs = 15_000): Promise<Mention[]> {
    return this.request("locate", { path, text }, timeoutMs) as Promise<Mention[]>;
  }

  /** The definition of the class `name` — slow the first time: the helper imports every registering package. */
  resolve(name: string, imports: string[], timeoutMs = 600_000): Promise<Resolution> {
    return this.request("resolve", { name, imports }, timeoutMs) as Promise<Resolution>;
  }

  /** Send one request; the promise settles with its answer, its error, a timeout or the helper's end. */
  request(method: string, params: object, timeoutMs: number): Promise<unknown> {
    const child = this.ensureStarted();
    if (child === undefined) {
      return Promise.reject(new Error(this.downReason));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer =
        timeoutMs > 0
          ? setTimeout(() => {
              this.pending.delete(id);
              reject(new Error(`the confluid helper did not answer ${method} within ${timeoutMs / 1000} s`));
            }, timeoutMs)
          : undefined;
      this.pending.set(id, { resolve, reject, timer });
      child.stdin?.write(JSON.stringify({ id, method, params }) + "\n");
    });
  }

  /** End the helper; the next request starts a new one, with no back-off. */
  restart(): void {
    this.downUntil = 0;
    this.stop("the helper was restarted");
  }

  dispose(): void {
    this.stop("the extension stopped");
  }

  private stop(reason: string): void {
    const child = this.process;
    this.process = undefined;
    this.failPending(reason);
    child?.kill();
  }

  private ensureStarted(): HelperProcess | undefined {
    if (this.process !== undefined) {
      return this.process;
    }
    if (this.now() < this.downUntil) {
      return undefined;
    }
    const child = this.spawner();
    this.process = child;
    this.buffer = "";
    this.stderrTail = [];
    this.answered = false;
    child.stdout?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => this.onStdout(chunk));
    child.stderr?.setEncoding("utf8");
    child.stderr?.on("data", (chunk: string) => this.onStderr(chunk));
    child.once("error", (error: Error) => this.onEnd(child, `could not start the confluid helper: ${error.message}`));
    child.once("exit", (code: number | null, signal: string | null) =>
      this.onEnd(child, `the confluid helper ended (${signal ?? `exit code ${code}`})`),
    );
    return child;
  }

  private onStdout(chunk: string): void {
    this.buffer += chunk;
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (line) {
        this.onLine(line);
      }
      newline = this.buffer.indexOf("\n");
    }
  }

  private onLine(line: string): void {
    let reply: { id?: unknown; result?: unknown; error?: unknown };
    try {
      reply = JSON.parse(line);
    } catch {
      this.log(`helper said (not a reply): ${line}`);
      return;
    }
    const pending = typeof reply.id === "number" ? this.pending.get(reply.id) : undefined;
    if (pending === undefined) {
      this.log(`helper reply to no request: ${line}`);
      return;
    }
    this.answered = true;
    this.pending.delete(reply.id as number);
    clearTimeout(pending.timer);
    if (reply.error !== undefined) {
      pending.reject(new Error(String(reply.error)));
    } else {
      pending.resolve(reply.result);
    }
  }

  private onStderr(chunk: string): void {
    for (const line of chunk.split("\n")) {
      if (line.trim()) {
        this.log(line);
        this.stderrTail = [...this.stderrTail, line].slice(-5);
      }
    }
  }

  private onEnd(child: HelperProcess, what: string): void {
    if (this.process !== child) {
      return; // an earlier helper, already replaced
    }
    this.process = undefined;
    const tail = this.stderrTail.length ? `: ${this.stderrTail.join(" | ")}` : "";
    const reason = what + tail;
    this.log(reason);
    if (!this.answered) {
      this.downUntil = this.now() + RESTART_BACKOFF_MS;
      this.downReason = reason;
    }
    this.failPending(reason);
  }

  private failPending(reason: string): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new Error(reason));
    }
    this.pending.clear();
  }
}
