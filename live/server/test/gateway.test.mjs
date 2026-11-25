import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { on, once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { WebSocket } from "ws";
import v1 from "../../../schemas/v1/examples/demo-frame.json" with { type: "json" };
import empty from "../../../schemas/v2/examples/empty-pitch.json" with { type: "json" };
import lost from "../../../schemas/v2/examples/lost-ball.json" with { type: "json" };
import unknown from "../../../schemas/v2/examples/unknown-number.json" with { type: "json" };
import clockReset from "../../../schemas/v2/examples/clock-reset.json" with { type: "json" };
import invalidTrack from "../../../schemas/v2/examples/invalid/duplicate-track.json" with { type: "json" };
import { readScene } from "../../web/src/live-frame.js";

const entry = fileURLToPath(
  new URL(
    process.env.PITCHTRACE_TEST_ENTRY ?? "../server.mjs",
    import.meta.url,
  ),
);
const match = (id) => "/v2/matches/" + id;
const trial = match(empty.matchId);

async function launch(t, database) {
  const child = spawn(process.execPath, [entry], {
    env: {
      ...process.env,
      PITCHTRACE_HOST: "127.0.0.1",
      PITCHTRACE_PORT: "0",
      PITCHTRACE_DB: database,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const exited = once(child, "exit");
  let errors = "";
  child.stderr.on("data", (chunk) => (errors += chunk));
  const stop = async () => {
    child.kill();
    await exited;
  };
  t.after(stop);
  const base = await Promise.race([
    once(child.stdout, "data", { signal: AbortSignal.timeout(5000) }).then(
      ([data]) => String(data).match(/http:\/\/127\.0\.0\.1:\d+/)?.[0],
    ),
    exited.then(() => assert.fail("网关启动失败\n" + errors)),
  ]);
  assert.ok(base);

  return {
    base,
    stop,
    async post(path, frame) {
      const response = await fetch(base + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: typeof frame === "string" ? frame : JSON.stringify(frame),
        signal: AbortSignal.timeout(5000),
      });
      await response.text();
      return response.status;
    },
    async subscribe(path) {
      const url = new URL(path, base);
      url.protocol = "ws:";
      const socket = new WebSocket(url);
      t.after(() => socket.terminate());
      const messages = on(socket, "message", { close: ["close"] });
      await once(socket, "open", { signal: AbortSignal.timeout(5000) });
      return async () => {
        const { value } = await messages.next();
        assert.ok(value, "订阅在收到下一帧前关闭");
        return JSON.parse(value[0]);
      };
    },
  };
}

test(
  "v2 校验、幂等、比赛隔离、旧帧归档及重启恢复",
  { timeout: 10000 },
  async (t) => {
    const folder = await mkdtemp(join(tmpdir(), "pitchtrace-test-"));
    const database = join(folder, "archive.sqlite");
    t.after(() => rm(folder, { recursive: true, force: true }));
    const server = await launch(t, database);
    const page = await fetch(server.base + "/?match=trial-01");
    assert.equal(page.status, 200);
    await page.body.cancel();

    const frames = trial + "/frames";
    const next = await server.subscribe(trial + "/live");
    const other = { ...empty, matchId: "trial-02" };
    const otherNext = await server.subscribe(match(other.matchId) + "/live");
    assert.equal(await server.post(frames, "{"), 400);
    assert.equal(await server.post(frames, " ".repeat(256001)), 413);
    for (const invalid of [
      invalidTrack,
      other,
      { ...empty, captureUtcUs: Number(empty.captureUtcUs) },
      { ...empty, calibrationId: null },
      { ...lost, ball: { state: "lost", positionM: [0, 0, 0] } },
      { ...lost, players: [{ trackId: "p8", state: "measured" }] },
      {
        ...lost,
        players: [{ ...lost.players[0], bones: { neck: [0, 0, 0, 0] } }],
      },
    ])
      assert.equal(await server.post(frames, invalid), 422);
    assert.equal(readScene(invalidTrack), null);

    const reordered = Object.fromEntries(Object.entries(lost).reverse());
    reordered.players = lost.players.map((player) =>
      Object.fromEntries(Object.entries(player).reverse()),
    );
    assert.deepEqual(
      await Promise.all([
        server.post(frames, empty),
        server.post(frames, empty),
      ]),
      [202, 202],
    );
    assert.deepEqual(await next(), empty);
    assert.equal(await server.post(frames, lost), 202);
    assert.deepEqual(await next(), lost);
    assert.equal(await server.post(frames, reordered), 202);
    assert.equal(await server.post(frames, { ...lost, ball: null }), 409);
    assert.equal(
      await server.post(match(other.matchId) + "/frames", other),
      202,
    );
    assert.deepEqual(await otherNext(), other);
    assert.equal(await server.post(frames, unknown), 202);
    const converted = readScene(await next());
    assert.equal(converted.players.length, 1);
    assert.equal(converted.players[0].number, null);
    assert.equal(converted.ballState, "predicted");

    const outOfOrder = [
      { ...empty, seq: 4, matchTimeMs: unknown.matchTimeMs + 100 },
      { ...empty, seq: 8 },
    ];
    for (const frame of outOfOrder)
      assert.equal(await server.post(frames, frame), 202);

    const [before, after, backfill] = clockReset;
    for (const live of [before, after]) {
      assert.equal(await server.post(frames, live), 202);
      assert.deepEqual(await next(), live);
    }
    assert.equal(await server.post(frames, backfill), 202);
    await server.stop();

    const restarted = await launch(t, database);
    const liveAfterRestart = await restarted.subscribe(trial + "/live");
    assert.deepEqual(await liveAfterRestart(), after);
    assert.equal(await restarted.post(frames, backfill), 202);
    const retired = {
      ...backfill,
      seq: 253,
      matchTimeMs: after.matchTimeMs + 1000,
    };
    assert.equal(await restarted.post(frames, retired), 202);

    const db = new DatabaseSync(database);
    try {
      const count = db.prepare("SELECT count(*) AS n FROM scene_frames");
      const expected = 8 + outOfOrder.length;
      assert.equal(count.get().n, expected);
      const stored = db.prepare(
        "SELECT payload FROM scene_frames WHERE match_id = ? AND stream_epoch = ? AND seq = ?",
      );
      for (const frame of [...outOfOrder, backfill, retired])
        assert.deepEqual(
          JSON.parse(
            stored.get(frame.matchId, frame.streamEpoch, frame.seq).payload,
          ),
          frame,
        );
      db.exec(`
      CREATE TRIGGER reject_live BEFORE UPDATE ON scene_live
      WHEN NEW.seq = 1 BEGIN SELECT RAISE(ABORT, '测试归档失败'); END
    `);
      const frame = { ...after, seq: 1, matchTimeMs: after.matchTimeMs + 100 };
      assert.equal(await restarted.post(frames, frame), 500);
      assert.equal(count.get().n, expected);
      db.exec("DROP TRIGGER reject_live");
      const recovered = { ...frame, seq: 2 };
      assert.equal(await restarted.post(frames, recovered), 202);
      assert.deepEqual(await liveAfterRestart(), recovered);
    } finally {
      db.close();
    }
  },
);

test(
  "v2 历史按比赛和时间分页，补传不会改变正在读取的结果",
  { timeout: 10000 },
  async (t) => {
    const server = await launch(t, ":memory:");
    const path = trial + "/frames";
    const [before, after, late] = clockReset;
    const get = (query = "", endpoint = path) =>
      fetch(server.base + endpoint + query, {
        signal: AbortSignal.timeout(5000),
      });
    const read = async (query, endpoint) => {
      const response = await get(query, endpoint);
      assert.equal(response.status, 200);
      return response.json();
    };

    assert.deepEqual(await read(), {
      bounds: null,
      range: null,
      frames: [],
      nextCursor: null,
    });
    for (const frame of [empty, lost, unknown, before, after, late])
      assert.equal(await server.post(path, frame), 202);
    const other = { ...lost, matchId: "trial-02" };
    const otherPath = match(other.matchId) + "/frames";
    assert.equal(await server.post(otherPath, other), 202);
    assert.deepEqual((await read("", otherPath)).frames, [other]);

    const query = "?fromMs=1250&toMs=25100&limit=2";
    const first = await read(query);
    assert.deepEqual(first.bounds, {
      fromMs: empty.matchTimeMs,
      toMs: after.matchTimeMs,
    });
    assert.deepEqual(first.range, {
      fromMs: lost.matchTimeMs,
      toMs: after.matchTimeMs,
    });
    assert.deepEqual(first.frames, [lost, unknown]);
    assert.ok(first.nextCursor);
    const trailing = { ...late, seq: late.seq + 1 };
    assert.equal(await server.post(path, trailing), 202);
    const second = await read("?limit=2&cursor=" + first.nextCursor);
    assert.deepEqual(second.frames, [before, late]);
    const third = await read("?limit=2&cursor=" + second.nextCursor);
    assert.deepEqual(third.frames, [after]);
    assert.equal(third.nextCursor, null);
    const refreshed = await read("?fromMs=25050&toMs=25050");
    assert.deepEqual(refreshed.frames, [late, trailing]);

    for (const query of [
      "?fromMs=0",
      "?fromMs=50&toMs=49",
      "?fromMs=0&toMs=60001",
      "?fromMs=0.25&toMs=1",
      "?fromMs=0&toMs=9007199254740992",
      "?limit=0",
      "?limit=201",
      "?limit=1&limit=2",
      "?cursor=invalid",
      "?fromMs=1200&toMs=25100&cursor=" + first.nextCursor,
    ])
      assert.equal((await get(query)).status, 400);
    assert.equal(
      (await get("?cursor=" + first.nextCursor, otherPath)).status,
      400,
    );
    assert.deepEqual((await read("?fromMs=0&toMs=1199")).frames, []);

    const newer = { ...after, seq: 1, matchTimeMs: 60000 };
    assert.equal(await server.post(path, newer), 202);
    const recent = await read();
    assert.deepEqual(recent.range, { fromMs: 30000, toMs: 60000 });
    assert.deepEqual(recent.frames, [newer]);
  },
);

test("v2 历史单页限制载荷并保留下一页起点", { timeout: 5000 }, async (t) => {
  const server = await launch(t, ":memory:");
  const path = trial + "/frames";
  const padding = "x".repeat(250000);
  for (let seq = 0; seq < 4; seq++)
    assert.equal(await server.post(path, { ...empty, seq, padding }), 202);
  const response = await fetch(server.base + path);
  assert.equal(response.status, 200);
  assert.ok(Number(response.headers.get("content-length")) < 1_000_000);
  const first = await response.json();
  assert.deepEqual(
    first.frames.map(({ seq }) => seq),
    [0, 1, 2],
  );
  const next = await fetch(server.base + path + "?cursor=" + first.nextCursor);
  const second = await next.json();
  assert.deepEqual(
    second.frames.map(({ seq }) => seq),
    [3],
  );
  assert.equal(second.nextCursor, null);
});

test("v1 试播、校验和转发可继续独立运行", { timeout: 5000 }, async (t) => {
  const server = await launch(t, ":memory:");
  const demo = await server.subscribe("/demo");
  assert.equal((await demo()).v, 1);
  const live = await server.subscribe("/live");
  assert.equal(await server.post("/frame", "{"), 400);
  assert.equal(await server.post("/frame", " ".repeat(256001)), 413);
  assert.equal(await server.post("/frame", { v: 1 }), 422);
  const depth = 10000;
  const nested =
    JSON.stringify(v1).slice(0, -1) +
    ',"extra":' +
    "[".repeat(depth) +
    "0" +
    "]".repeat(depth) +
    "}";
  assert.equal(await server.post("/frame", nested), 400);
  assert.equal(await server.post("/frame", v1), 202);
  assert.deepEqual(await live(), v1);
});
