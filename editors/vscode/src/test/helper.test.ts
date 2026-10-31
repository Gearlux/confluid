// The helper client against a fake process: the line protocol, errors, timeouts, the end of a helper and the
// back-off after a helper that never answered.

import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { test } from "node:test";
import { HelperClient, HelperProcess, RESTART_BACKOFF_MS } from "../helper";

/** A helper process the test drives: it reads what the client wrote and answers by hand. */
class FakeHelper extends EventEmitter {
  readonly stdin = new PassThrough();
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  killed = false;
  readonly written: string[] = [];

  constructor() {
    super();
    this.stdin.setEncoding("utf8");
    this.stdin.on("data", (chunk: string) => this.written.push(...chunk.split("\n").filter(Boolean)));
  }

  kill(): boolean {
    this.killed = true;
    return true;
  }

  /** The requests written so far, parsed. */
  requests(): Array<{ id: number; method: string; params: Record<string, unknown> }> {
    return this.written.map((line) => JSON.parse(line));
  }

  reply(text: string): void {
    this.stdout.write(text);
  }

  end(code: number): void {
    this.emit("exit", code, null);
  }
}

function harness(now: () => number = Date.now) {
  const helpers: FakeHelper[] = [];
  const logged: string[] = [];
  const client = new HelperClient(
    () => {
      const helper = new FakeHelper();
      helpers.push(helper);
      return helper as unknown as HelperProcess;
    },
    (line) => logged.push(line),
    now,
  );
  return { client, helpers, logged };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

test("a request is one JSON line and its answer settles it, whatever the chunking", async () => {
  const { client, helpers } = harness();
  const answer = client.locate("/w/a.yaml", "m: !class:X\n");
  await tick();
  assert.deepEqual(helpers[0].requests(), [
    { id: 1, method: "locate", params: { path: "/w/a.yaml", text: "m: !class:X\n" } },
  ]);
  const mention = { kind: "class", text: "X", line: 0, start: 10, stop: 11, target: null, deferred: true };
  const line = JSON.stringify({ id: 1, result: [mention] });
  helpers[0].reply(line.slice(0, 7));
  helpers[0].reply(line.slice(7) + "\n");
  assert.deepEqual(await answer, [mention]);
});

test("answers are matched by id, and a stray line is logged, not taken as an answer", async () => {
  const { client, helpers, logged } = harness();
  const first = client.resolve("A", []);
  const second = client.resolve("B", ["pkg.ops"]);
  await tick();
  assert.deepEqual(helpers[0].requests()[1].params, { name: "B", imports: ["pkg.ops"] });
  helpers[0].reply("warming up\n\n");
  helpers[0].reply(JSON.stringify({ id: 99, result: null }) + "\n");
  helpers[0].reply(JSON.stringify({ id: 2, result: { target: { path: "/b.py", line: 3 }, reason: null } }) + "\n");
  helpers[0].reply(JSON.stringify({ id: 1, result: { target: null, reason: "ambiguous" } }) + "\n");
  assert.deepEqual(await second, { target: { path: "/b.py", line: 3 }, reason: null });
  assert.deepEqual(await first, { target: null, reason: "ambiguous" });
  assert.deepEqual(logged.slice(0, 2), ["helper said (not a reply): warming up", `helper reply to no request: {"id":99,"result":null}`]);
});

test("an error answer rejects with the helper's message", async () => {
  const { client, helpers } = harness();
  const answer = client.request("bogus", {}, 1000);
  await tick();
  helpers[0].reply(JSON.stringify({ id: 1, error: "ValueError: unknown method 'bogus'" }) + "\n");
  await assert.rejects(answer, /unknown method 'bogus'/);
});

test("a request the helper never answers times out", async () => {
  const { client } = harness();
  await assert.rejects(client.request("locate", {}, 20), /did not answer locate within 0.02 s/);
});

test("a helper that ends fails what is pending with its last stderr lines, and the next request starts another", async () => {
  const { client, helpers, logged } = harness();
  const answered = client.request("locate", {}, 0);
  await tick();
  helpers[0].reply(JSON.stringify({ id: 1, result: [] }) + "\n");
  await answered;
  const pending = client.request("resolve", {}, 0);
  await tick();
  helpers[0].stderr.write("Traceback\nMemoryError\n");
  await tick();
  helpers[0].end(1);
  await assert.rejects(pending, /the confluid helper ended \(exit code 1\): Traceback \| MemoryError/);
  assert.ok(logged.includes("MemoryError"));
  const again = client.request("locate", {}, 0);
  assert.equal(helpers.length, 2); // it had answered once: restarted at once
  await tick();
  helpers[1].reply(JSON.stringify({ id: 3, result: [] }) + "\n");
  assert.deepEqual(await again, []);
});

test("a helper that ends before answering anything is left down for the back-off", async () => {
  let clock = 1_000;
  const { client, helpers } = harness(() => clock);
  const first = client.request("locate", {}, 0);
  await tick();
  helpers[0].stderr.write("No module named confluid.locate\n");
  await tick();
  helpers[0].end(1);
  await assert.rejects(first, /No module named confluid.locate/);
  await assert.rejects(client.request("locate", {}, 0), /No module named confluid.locate/);
  assert.equal(helpers.length, 1);
  clock += RESTART_BACKOFF_MS;
  void client.request("locate", {}, 0).catch(() => undefined);
  assert.equal(helpers.length, 2);
});

test("a helper that cannot start reports why", async () => {
  const { client, helpers } = harness();
  const answer = client.request("locate", {}, 0);
  helpers[0].emit("error", new Error("spawn python3 ENOENT"));
  await assert.rejects(answer, /could not start the confluid helper: spawn python3 ENOENT/);
  helpers[0].end(1); // the exit after an error event is the same end, reported once
});

test("restart ends the helper, fails what is pending and skips the back-off", async () => {
  let clock = 0;
  const { client, helpers } = harness(() => clock);
  const pending = client.request("resolve", {}, 0);
  client.restart();
  await assert.rejects(pending, /the helper was restarted/);
  assert.ok(helpers[0].killed);
  helpers[0].end(0); // the killed helper's exit comes later and changes nothing
  void client.request("locate", {}, 0).catch(() => undefined);
  assert.equal(helpers.length, 2);
  client.dispose();
  assert.ok(helpers[1].killed);
  clock += 1;
});
