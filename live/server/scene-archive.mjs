import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

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
