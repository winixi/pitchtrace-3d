import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

// 设备凭据：`pt1.<设备 ID>.<32 字节随机数的 base64url>`，Edge 以 `Authorization: Bearer` 发送。
// 网关只保存随机部分的 SHA-256；签发时明文只显示一次，重新签发即轮换并作废旧凭据。
export const TOKEN_PREFIX = "pt1";
const idPattern = /^[A-Za-z0-9_-]{1,64}$/;
const secretPattern = /^[A-Za-z0-9_-]{43}$/;
export const ALL_MATCHES = "*";

export function validDeviceId(value) {
  return typeof value === "string" && idPattern.test(value);
}

function digest(secret) {
  return createHash("sha256").update(secret, "utf8").digest();
}

export function parseToken(token) {
  if (typeof token !== "string") return null;
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== TOKEN_PREFIX) return null;
  const [, deviceId, secret] = parts;
  if (!validDeviceId(deviceId) || !secretPattern.test(secret)) return null;
  return { deviceId, secret };
}

function openDatabase(filename) {
  const sqlite = process.getBuiltinModule?.("node:sqlite");
  if (!sqlite) return null;
  if (filename !== ":memory:") mkdirSync(dirname(filename), { recursive: true });
  const db = new sqlite.DatabaseSync(filename);
  db.exec(`
    PRAGMA busy_timeout = 1000;
    CREATE TABLE IF NOT EXISTS device_credentials (
      device_id TEXT PRIMARY KEY,
      token_sha256 BLOB NOT NULL,
      matches TEXT NOT NULL,
      label TEXT,
      issued_at TEXT NOT NULL,
      revoked_at TEXT
    ) STRICT;
  `);
  return db;
}

/** 签发、列出与吊销设备凭据，供 tools/devices.mjs 使用。 */
export function openDeviceRegistry(filename) {
  const db = openDatabase(filename);
  if (!db) return null;
  return {
    issue(deviceId, matches, label = null) {
      if (!validDeviceId(deviceId)) throw new Error("设备 ID 须为 1–64 位字母、数字、下划线或连字符");
      if (
        !Array.isArray(matches) ||
        !matches.length ||
        !matches.every((m) => m === ALL_MATCHES || validDeviceId(m))
      )
        throw new Error("需要至少一个有效的比赛 ID，或用 * 表示全部比赛");
      const secret = randomBytes(32).toString("base64url");
      db.prepare(
        `INSERT INTO device_credentials VALUES (?, ?, ?, ?, ?, NULL)
         ON CONFLICT(device_id) DO UPDATE SET token_sha256 = excluded.token_sha256,
           matches = excluded.matches, label = excluded.label,
           issued_at = excluded.issued_at, revoked_at = NULL`,
      ).run(
        deviceId,
        digest(secret),
        JSON.stringify([...new Set(matches)].sort()),
        label,
        new Date().toISOString(),
      );
      return `${TOKEN_PREFIX}.${deviceId}.${secret}`;
    },
    revoke(deviceId) {
      return (
        db
          .prepare(
            "UPDATE device_credentials SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
          )
          .run(new Date().toISOString(), deviceId).changes > 0
      );
    },
    list() {
      return db
        .prepare(
          "SELECT device_id, matches, label, issued_at, revoked_at FROM device_credentials ORDER BY device_id",
        )
        .all()
        .map((row) => ({
          deviceId: row.device_id,
          matches: JSON.parse(row.matches),
          label: row.label ?? null,
          issuedAt: row.issued_at,
          revokedAt: row.revoked_at ?? null,
        }));
    },
    close() {
      db.close();
    },
  };
}

function loopback(host) {
  return ["127.0.0.1", "::1", "localhost"].includes(host) || /^127\./.test(host);
}

/**
 * 写入请求的设备校验。
 *
 * - required：缺凭据、凭据无效或已吊销返回 401，凭据未授权该比赛返回 403。
 * - optional（回环地址且未要求时的本地试播）：无凭据照常写入；带了凭据仍按 required 校验。
 *
 * 每次请求都查库，吊销与轮换立即生效。返回 null 表示放行，否则返回 { status, message }。
 */
export function createDeviceAuth(filename, { host, mode = process.env.PITCHTRACE_DEVICE_AUTH } = {}) {
  if (mode !== undefined && mode !== "" && mode !== "required" && mode !== "optional")
    throw new Error("PITCHTRACE_DEVICE_AUTH 须为 required 或 optional");
  if (mode === "optional" && !loopback(host))
    throw new Error("非回环地址监听时必须校验设备凭据");
  const required = mode === "required" || !loopback(host);
  let db;
  let statement;
  function lookup(deviceId) {
    if (db === undefined) {
      db = openDatabase(filename);
      statement = db?.prepare(
        "SELECT token_sha256, matches FROM device_credentials WHERE device_id = ? AND revoked_at IS NULL",
      );
    }
    return statement?.get(deviceId);
  }
  return {
    required,
    check(request, matchId) {
      const header = request.headers.authorization;
      if (header === undefined) {
        return required ? { status: 401, message: "Device credential required" } : null;
      }
      const match = /^Bearer (\S+)$/.exec(header);
      const parsed = match && parseToken(match[1]);
      const row = parsed && lookup(parsed.deviceId);
      const expected = row && Buffer.from(row.token_sha256);
      if (!row || !timingSafeEqual(expected, digest(parsed.secret)))
        return { status: 401, message: "Invalid or revoked device credential" };
      const matches = JSON.parse(row.matches);
      if (matchId !== null && !matches.includes(ALL_MATCHES) && !matches.includes(matchId))
        return { status: 403, message: "Device is not allowed to write this match" };
      request.deviceId = parsed.deviceId;
      return null;
    },
    close() {
      db?.close();
    },
  };
}

/** 拒绝未授权写入：不读取请求体并关闭连接，避免客户端继续推送大文件。 */
export function refuseWrite(request, response, { status, message }) {
  const headers = { "Content-Type": "text/plain; charset=utf-8", Connection: "close" };
  if (status === 401) headers["WWW-Authenticate"] = 'Bearer realm="pitchtrace-device"';
  response.writeHead(status, headers);
  response.end(message);
  request.resume();
}
