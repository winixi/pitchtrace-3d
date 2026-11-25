import { validSceneId } from "./scene-protocol.mjs";

export const DEFAULT_HISTORY_MS = 30_000;
const MAX_HISTORY_MS = 60_000;
const DEFAULT_PAGE_SIZE = 100;
const MAX_PAGE_SIZE = 200;
const validIndex = (value) => Number.isSafeInteger(value) && value >= 0;
const validRange = (fromMs, toMs) =>
  validIndex(fromMs) &&
  validIndex(toMs) &&
  fromMs <= toMs &&
  toMs - fromMs <= MAX_HISTORY_MS;

function readIndex(value) {
  return value !== null && /^(0|[1-9]\d*)$/.test(value) ? Number(value) : null;
}

function decodeCursor(value, matchId) {
  if (!/^[A-Za-z0-9_-]{1,512}$/.test(value)) return null;
  try {
    const parts = JSON.parse(Buffer.from(value, "base64url").toString("utf8"));
    if (!Array.isArray(parts) || parts.length !== 7) return null;
    const [match, fromMs, toMs, watermark, time, epoch, seq] = parts;
    if (
      match !== matchId ||
      !validRange(fromMs, toMs) ||
      !validIndex(watermark) ||
      !validIndex(time) ||
      time < fromMs ||
      time > toMs ||
      !validSceneId(epoch) ||
      !validIndex(seq)
    )
      return null;
    return { range: { fromMs, toMs }, watermark, time, epoch, seq };
  } catch {
    return null;
  }
}

export function encodeHistoryCursor(matchId, range, watermark, last) {
  return Buffer.from(
    JSON.stringify([
      matchId,
      range.fromMs,
      range.toMs,
      watermark,
      last.match_time_ms,
      last.stream_epoch,
      last.seq,
    ]),
  ).toString("base64url");
}

export function readHistoryQuery(params, matchId) {
  const keys = new Set();
  for (const key of params.keys()) {
    if (keys.has(key) || !["fromMs", "toMs", "limit", "cursor"].includes(key))
      return null;
    keys.add(key);
  }
  const limit = params.has("limit")
    ? readIndex(params.get("limit"))
    : DEFAULT_PAGE_SIZE;
  if (!validIndex(limit) || limit < 1 || limit > MAX_PAGE_SIZE) return null;

  let range = null;
  if (params.has("fromMs") || params.has("toMs")) {
    const fromMs = readIndex(params.get("fromMs"));
    const toMs = readIndex(params.get("toMs"));
    if (!validRange(fromMs, toMs)) return null;
    range = { fromMs, toMs };
  }

  const cursor = params.has("cursor")
    ? decodeCursor(params.get("cursor"), matchId)
    : null;
  if (
    (params.has("cursor") && !cursor) ||
    (cursor &&
      range &&
      (cursor.range.fromMs !== range.fromMs ||
        cursor.range.toMs !== range.toMs))
  )
    return null;
  return { range, limit, cursor };
}
