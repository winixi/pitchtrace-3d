import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import v1 from "../../../schemas/v1/examples/demo-frame.json" with { type: "json" };
import empty from "../../../schemas/v2/examples/empty-pitch.json" with { type: "json" };
import { createDeviceAuth, parseToken } from "../device-auth.mjs";

const entry = fileURLToPath(new URL(process.env.PITCHTRACE_TEST_ENTRY ?? "../server.mjs", import.meta.url));
const tool = fileURLToPath(new URL("../tools/devices.mjs", import.meta.url));
const frames = `/v2/matches/${empty.matchId}/frames`;
const init = Buffer.from("not decoded at upload time");
const initPath = `/v2/matches/${empty.matchId}/cameras/A/epochs/live/init.mp4`;

async function folder(t) {
  const dir = await mkdtemp(join(tmpdir(), "pitchtrace-devices-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  return dir;
}

function devices(database, ...args) {
  return execFileSync(process.execPath, [tool, ...args], {
    env: { ...process.env, PITCHTRACE_DB: database },
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

async function launch(t, database, mode) {
  const env = { ...process.env, PITCHTRACE_HOST: "127.0.0.1", PITCHTRACE_PORT: "0", PITCHTRACE_DB: database };
  if (mode) env.PITCHTRACE_DEVICE_AUTH = mode;
  else delete env.PITCHTRACE_DEVICE_AUTH;
  const child = spawn(process.execPath, [entry], { env, stdio: ["ignore", "pipe", "pipe"] });
  const exited = once(child, "exit");
  t.after(async () => {
    child.kill();
    await exited;
  });
  const [data] = await once(child.stdout, "data", { signal: AbortSignal.timeout(5000) });
  const base = String(data).match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];
  assert.ok(base);
  async function call(method, path, body, token, headers = {}) {
    const response = await fetch(base + path, {
      method,
      headers: { ...headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body,
      signal: AbortSignal.timeout(5000),
    });
    return { status: response.status, challenge: response.headers.get("www-authenticate"), text: await response.text() };
  }
  return {
    frame: (token, path = frames) => call("POST", path, JSON.stringify(empty), token, { "Content-Type": "application/json" }),
    v1: (token) => call("POST", "/frame", JSON.stringify(v1), token, { "Content-Type": "application/json" }),
    init: (token) =>
      call("PUT", initPath, init, token, {
        "Content-Type": "video/mp4",
        "X-Content-SHA256": createHash("sha256").update(init).digest("hex"),
      }),
    history: () => call("GET", frames),
  };
}

test("required 模式下写入需设备凭据，按比赛授权并即时吊销与轮换", { timeout: 20000 }, async (t) => {
  const database = join(await folder(t), "gateway.sqlite");
  const gateway = await launch(t, database, "required");
  for (const write of [gateway.frame, gateway.v1, gateway.init]) {
    const refused = await write();
    assert.equal(refused.status, 401);
    assert.equal(refused.challenge, 'Bearer realm="pitchtrace-device"');
  }
  // 读取不在设备凭据范围内。
  assert.equal((await gateway.history()).status, 200);

  const token = devices(database, "issue", "edge-a", "--match", empty.matchId, "--label", "场边 Mac");
  assert.ok(parseToken(token));
  assert.equal((await gateway.frame(token)).status, 202);
  assert.equal((await gateway.init(token)).status, 202);
  assert.equal((await gateway.v1(token)).status, 202);
  const other = await gateway.frame(token, "/v2/matches/other-match/frames");
  assert.equal(other.status, 403);
  assert.equal(other.challenge, null);

  const [prefix, device, secret] = token.split(".");
  const wrong = `${prefix}.${device}.${secret.slice(0, -1)}${secret.endsWith("A") ? "B" : "A"}`;
  for (const bad of [wrong, "pt1.edge-b." + secret, "garbage", token + "x"])
    assert.equal((await gateway.frame(bad)).status, 401, bad);

  const listed = JSON.parse(devices(database, "list"));
  assert.deepEqual(listed.map(({ deviceId, matches, label, revokedAt }) => ({ deviceId, matches, label, revokedAt })), [
    { deviceId: "edge-a", matches: [empty.matchId], label: "场边 Mac", revokedAt: null },
  ]);
  assert.ok(!JSON.stringify(listed).includes(secret), "列表不得包含凭据");

  const rotated = devices(database, "issue", "edge-a", "--all-matches");
  assert.equal((await gateway.frame(token)).status, 401, "轮换后旧凭据失效");
  assert.equal((await gateway.frame(rotated, "/v2/matches/other-match/frames")).status, 422);
  devices(database, "revoke", "edge-a");
  assert.equal((await gateway.frame(rotated)).status, 401, "吊销无需重启网关");
});

test("回环试播默认不强制凭据，但带了凭据仍须有效", { timeout: 10000 }, async (t) => {
  const database = join(await folder(t), "gateway.sqlite");
  const gateway = await launch(t, database);
  assert.equal((await gateway.frame()).status, 202);
  assert.equal((await gateway.init()).status, 202);
  assert.equal((await gateway.frame("pt1.edge-a." + "A".repeat(43))).status, 401);
  const token = devices(database, "issue", "edge-a", "--match", empty.matchId);
  assert.equal((await gateway.frame(token)).status, 202);
});

test("签发到文件时新建 0600 文件并拒绝覆盖", async (t) => {
  const dir = await folder(t);
  const database = join(dir, "gateway.sqlite");
  const out = join(dir, "edge-a.token");
  devices(database, "issue", "edge-a", "--match", empty.matchId, "--out", out);
  assert.ok(parseToken((await readFile(out, "utf8")).trim()));
  assert.equal((await stat(out)).mode & 0o777, 0o600);
  assert.throws(() => devices(database, "issue", "edge-a", "--match", empty.matchId, "--out", out));
  assert.throws(() => devices(database, "issue", "bad.id", "--match", empty.matchId));
  assert.throws(() => devices(database, "issue", "edge-c"));
  assert.throws(() => devices(database, "revoke", "nobody"));
});

test("非回环监听时不能关闭凭据校验", () => {
  assert.equal(createDeviceAuth(":memory:", { host: "0.0.0.0" }).required, true);
  assert.equal(createDeviceAuth(":memory:", { host: "127.0.0.1" }).required, false);
  assert.equal(createDeviceAuth(":memory:", { host: "127.0.0.1", mode: "required" }).required, true);
  assert.throws(() => createDeviceAuth(":memory:", { host: "0.0.0.0", mode: "optional" }));
  assert.throws(() => createDeviceAuth(":memory:", { host: "127.0.0.1", mode: "off" }));
});
