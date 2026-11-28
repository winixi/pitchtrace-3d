#!/usr/bin/env node
// 签发、列出与吊销 Edge 设备凭据。只用 Node.js 内置模块，可在只有 server.cjs 的试播机上运行。
//
//   node live/server/tools/devices.mjs issue <设备 ID> --match <比赛 ID> [--match …] [--label 说明] [--out 文件]
//   node live/server/tools/devices.mjs issue <设备 ID> --all-matches
//   node live/server/tools/devices.mjs list
//   node live/server/tools/devices.mjs revoke <设备 ID>
//
// 凭据库与网关共用 PITCHTRACE_DB（默认 live/server/data/scenes.sqlite）。网关每次写入都查库，
// 吊销与重新签发立即生效，无需重启。
import { openSync, writeSync, closeSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ALL_MATCHES, openDeviceRegistry } from "../device-auth.mjs";

const usage = `用法：
  devices.mjs issue <设备 ID> (--match <比赛 ID>… | --all-matches) [--label 说明] [--out 文件]
  devices.mjs list
  devices.mjs revoke <设备 ID>`;

function fail(message) {
  console.error(message);
  process.exit(1);
}

const serverDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const database = process.env.PITCHTRACE_DB || path.join(serverDir, "data", "scenes.sqlite");
const [command, ...rest] = process.argv.slice(2);
const registry = openDeviceRegistry(database);
if (!registry) fail("需要支持 node:sqlite 的 Node.js 22.13+");

try {
  if (command === "issue") {
    const [deviceId, ...options] = rest;
    const matches = [];
    let label = null;
    let out = null;
    for (let i = 0; i < options.length; i++) {
      const option = options[i];
      if (option === "--all-matches") matches.push(ALL_MATCHES);
      else if (["--match", "--label", "--out"].includes(option) && i + 1 < options.length) {
        const value = options[++i];
        if (option === "--match") matches.push(value);
        else if (option === "--label") label = value;
        else out = value;
      } else fail(usage);
    }
    if (!deviceId) fail(usage);
    const token = registry.issue(deviceId, matches, label);
    if (out) {
      // 新建 0600 文件，拒绝覆盖，避免凭据留在终端历史或被其他用户读取。
      const fd = openSync(out, "wx", 0o600);
      writeSync(fd, token + "\n");
      closeSync(fd);
      console.error(`已签发设备 ${deviceId} 的凭据并写入 ${out}，旧凭据（如有）已作废`);
    } else {
      console.error(`已签发设备 ${deviceId} 的凭据，只显示这一次，旧凭据（如有）已作废：`);
      console.log(token);
    }
  } else if (command === "list" && rest.length === 0) {
    console.log(JSON.stringify(registry.list(), null, 2));
  } else if (command === "revoke" && rest.length === 1) {
    if (!registry.revoke(rest[0])) fail(`没有可吊销的设备 ${rest[0]}`);
    console.error(`已吊销设备 ${rest[0]}`);
  } else fail(usage);
} catch (error) {
  fail(error.message);
} finally {
  registry.close();
}
