import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DEFAULT_HISTORY_MS, encodeHistoryCursor } from "./history-query.mjs";

const pagePayloadBytes = 1_000_000;

export function openSceneArchive(filename) {
  const sqlite = process.getBuiltinModule?.("node:sqlite");
  if (!sqlite) return null;
  if (filename !== ":memory:")
    mkdirSync(dirname(filename), { recursive: true });
  const db = new sqlite.DatabaseSync(filename);
  try {
    db.exec(`
      PRAGMA busy_timeout = 1000;
      CREATE TABLE IF NOT EXISTS scene_frames (
        match_id TEXT NOT NULL,
        stream_epoch TEXT NOT NULL,
        seq INTEGER NOT NULL,
        match_time_ms INTEGER NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (match_id, stream_epoch, seq)
      ) STRICT;
      CREATE INDEX IF NOT EXISTS scene_frames_by_time
        ON scene_frames (match_id, match_time_ms, stream_epoch, seq);
      CREATE TABLE IF NOT EXISTS scene_live_epochs (
        match_id TEXT NOT NULL,
        stream_epoch TEXT NOT NULL,
        PRIMARY KEY (match_id, stream_epoch)
      ) STRICT;
      CREATE TABLE IF NOT EXISTS scene_live (
        match_id TEXT PRIMARY KEY,
        stream_epoch TEXT NOT NULL,
        seq INTEGER NOT NULL,
        FOREIGN KEY (match_id, stream_epoch, seq)
          REFERENCES scene_frames (match_id, stream_epoch, seq)
      ) STRICT;
    `);
    return prepareArchive(db);
  } catch (error) {
    db.close();
    throw error;
  }
}

function prepareArchive(db) {
  const stored = db.prepare(`
    SELECT payload FROM scene_frames
    WHERE match_id = ? AND stream_epoch = ? AND seq = ?
  `);
  const latest = db.prepare(`
    SELECT f.stream_epoch, f.seq, f.match_time_ms, f.payload
    FROM scene_live l JOIN scene_frames f USING (match_id, stream_epoch, seq)
    WHERE l.match_id = ?
  `);
  const knownEpoch = db.prepare(`
    SELECT 1 FROM scene_live_epochs WHERE match_id = ? AND stream_epoch = ?
  `);
  const insert = db.prepare("INSERT INTO scene_frames VALUES (?, ?, ?, ?, ?)");
  const markEpoch = db.prepare("INSERT INTO scene_live_epochs VALUES (?, ?)");
  const setLatest = db.prepare(`
    INSERT INTO scene_live VALUES (?, ?, ?)
    ON CONFLICT (match_id) DO UPDATE SET stream_epoch = excluded.stream_epoch, seq = excluded.seq
  `);
  const latestRowId = db.prepare(
    "SELECT COALESCE(MAX(rowid), 0) AS watermark FROM scene_frames",
  );
  const historyBounds = db.prepare(`
    SELECT
      (SELECT match_time_ms FROM scene_frames WHERE match_id = ? AND rowid <= ?
        ORDER BY match_time_ms LIMIT 1) AS fromMs,
      (SELECT match_time_ms FROM scene_frames WHERE match_id = ? AND rowid <= ?
        ORDER BY match_time_ms DESC LIMIT 1) AS toMs
  `);
  const historyRows = db.prepare(`
    SELECT match_time_ms, stream_epoch, seq, payload FROM scene_frames
    WHERE match_id = ? AND match_time_ms BETWEEN ? AND ? AND rowid <= ?
      AND (match_time_ms, stream_epoch, seq) > (?, ?, ?)
    ORDER BY match_time_ms, stream_epoch, seq
    LIMIT ?
  `);

  function advancesLive(frame, current) {
    if (!current) return true;
    if (frame.streamEpoch === current.stream_epoch)
      return (
        frame.seq > current.seq && frame.matchTimeMs >= current.match_time_ms
      );
    return (
      frame.matchTimeMs > current.match_time_ms &&
      !knownEpoch.get(frame.matchId, frame.streamEpoch)
    );
  }

  return {
    latest: (matchId) => latest.get(matchId)?.payload,
    history(matchId, query) {
      const { cursor, limit } = query;
      // 固定本次分页可见的最后一条入库记录，补传会在下一次查询出现。
      const watermark = cursor?.watermark ?? latestRowId.get().watermark;
      const extent = historyBounds.get(matchId, watermark, matchId, watermark);
      const bounds = extent.fromMs === null ? null : extent;
      const range =
        cursor?.range ??
        query.range ??
        (bounds && {
          fromMs: Math.max(bounds.fromMs, bounds.toMs - DEFAULT_HISTORY_MS),
          toMs: bounds.toMs,
        });
      if (!range) return { bounds, range, frames: [], nextCursor: null };

      const { fromMs, toMs } = range;
      const frames = [];
      let bytes = 0;
      let last;
      let nextCursor = null;
      for (const row of historyRows.iterate(
        matchId,
        fromMs,
        toMs,
        watermark,
        cursor?.time ?? fromMs,
        cursor?.epoch ?? "",
        cursor?.seq ?? -1,
        limit + 1,
      )) {
        bytes += Buffer.byteLength(row.payload);
        if (frames.length === limit || (last && bytes > pagePayloadBytes)) {
          nextCursor = encodeHistoryCursor(matchId, range, watermark, last);
          break;
        }
        frames.push(JSON.parse(row.payload));
        last = row;
      }
      return { bounds, range, frames, nextCursor };
    },
    write(frame, payload) {
      const { matchId, streamEpoch, seq, matchTimeMs } = frame;
      db.exec("BEGIN IMMEDIATE");
      try {
        const existing = stored.get(matchId, streamEpoch, seq);
        if (existing) {
          db.exec("COMMIT");
          return existing.payload === payload ? "duplicate" : "conflict";
        }
        insert.run(matchId, streamEpoch, seq, matchTimeMs, payload);
        const current = latest.get(matchId);
        const publish = advancesLive(frame, current);
        if (publish) {
          if (current?.stream_epoch !== streamEpoch)
            markEpoch.run(matchId, streamEpoch);
          setLatest.run(matchId, streamEpoch, seq);
        }
        db.exec("COMMIT");
        return publish ? "live" : "archived";
      } catch (error) {
        db.exec("ROLLBACK");
        throw error;
      }
    },
    close: () => db.close(),
  };
}
