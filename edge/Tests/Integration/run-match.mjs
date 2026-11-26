import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const repo = fileURLToPath(new URL("../../../", import.meta.url));
const binary = join(repo, "edge/.build/debug/pitchtrace-edge");

test("运行比赛先完成离线归档，再向本地网关交付场景和两路原片", async (t) => {
  const temporary = await mkdtemp(join(tmpdir(), "pitchtrace-run-"));
  t.after(() => rm(temporary, { recursive: true, force: true }));
  await run("python3", ["-B", join(repo, "edge/samples/generate-match.py"), temporary]);
  const path = join(temporary, "match.json");
  const config = JSON.parse(await readFile(path, "utf8"));
  const directory = join(temporary, "比赛结果");
  const execute = () => run(binary, ["run-match", path, directory]);
  const state = async () => JSON.parse(await readFile(join(directory, "run-state.json"), "utf8"));
  await execute();
  const offline = await state();
  assert.equal(offline.status, "completed");
  assert.equal(offline.destinations, undefined);
  const gateway = spawn(process.execPath, [join(repo, "live/server/server.cjs")], {
    env: { ...process.env, PITCHTRACE_HOST: "127.0.0.1", PITCHTRACE_PORT: "0",
      PITCHTRACE_DB: join(temporary, "gateway.sqlite"), PITCHTRACE_MEDIA_DIR: join(temporary, "gateway-media") },
    stdio: ["ignore", "pipe", "inherit"],
  });
  const exited = once(gateway, "exit");
  t.after(async () => { gateway.kill(); await exited; });
  const lines = createInterface({ input: gateway.stdout });
  const line = await Promise.race([
    once(lines, "line", { signal: AbortSignal.timeout(10_000) }).then(([value]) => value),
    exited.then(() => assert.fail("网关未正常启动")),
  ]);
  config.gateway = line.match(/http:\/\/127\.0\.0\.1:\d+$/)[0];
  await writeFile(path, JSON.stringify(config));
  await execute();
  const published = await state();
  assert.equal(published.sceneSha256, offline.sceneSha256);
  assert.equal(published.mediaSha256, offline.mediaSha256);
  assert.deepEqual(published.destinations[config.gateway], {
    scenes: "confirmed", "media/A": "confirmed", "media/B": "confirmed",
  });
  const match = config.gateway + "/v2/matches/" + config.matchId;
  const history = await fetch(match + "/frames");
  assert.equal(history.status, 200);
  assert.equal((await history.json()).frames.length, 20);
  for (const id of ["A", "B"]) {
    const hit = await fetch(match + "/cameras/" + id + "/locate?captureUtcUs=" + config.startUtcUs);
    assert.equal(hit.status, 200);
    assert.equal((await hit.json()).mediaEpoch, config.mediaEpoch ?? config.streamEpoch);
  }
  await execute();
  assert.deepEqual((await state()).destinations, published.destinations);
});
