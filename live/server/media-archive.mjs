import { linkSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { maxFrameDurationUs, maxUtcUs } from "./media-index.mjs";

export function openMediaArchive(filename, directory) {
  const sqlite = process.getBuiltinModule?.("node:sqlite");
  if (!sqlite) return null;
  mkdirSync(join(directory, "parts"), { recursive: true });
  mkdirSync(join(directory, "uploads"), { recursive: true });
  if (filename !== ":memory:")
    mkdirSync(dirname(filename), { recursive: true });
  const db = new sqlite.DatabaseSync(filename);
  try {
    db.exec(`
      PRAGMA busy_timeout = 1000;
      CREATE TABLE IF NOT EXISTS media_init (
        match_id TEXT, camera_id TEXT, epoch TEXT, digest TEXT NOT NULL, bytes INTEGER NOT NULL,
        PRIMARY KEY (match_id, camera_id, epoch)
      ) STRICT, WITHOUT ROWID;
      CREATE TABLE IF NOT EXISTS media_segments (
        match_id TEXT, camera_id TEXT, epoch TEXT, segment INTEGER,
        digest TEXT NOT NULL, bytes INTEGER NOT NULL,
        PRIMARY KEY (match_id, camera_id, epoch, segment)
      ) STRICT, WITHOUT ROWID;
      CREATE TABLE IF NOT EXISTS media_indexes (
        match_id TEXT, camera_id TEXT, epoch TEXT, segment INTEGER,
        init_digest TEXT NOT NULL, segment_digest TEXT NOT NULL, segment_bytes INTEGER NOT NULL,
        first_pts INTEGER NOT NULL, duration REAL NOT NULL,
        numerator INTEGER NOT NULL, denominator INTEGER NOT NULL,
        first_utc INTEGER NOT NULL, last_utc INTEGER NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (match_id, camera_id, epoch, segment)
      ) STRICT, WITHOUT ROWID;
      CREATE TABLE IF NOT EXISTS media_exposures (
        match_id TEXT, camera_id TEXT, utc INTEGER, epoch TEXT, segment INTEGER,
        pts INTEGER NOT NULL, duration_pts INTEGER NOT NULL, tolerance_us INTEGER NOT NULL,
        PRIMARY KEY (match_id, camera_id, utc, epoch, segment),
        FOREIGN KEY (match_id, camera_id, epoch, segment)
          REFERENCES media_indexes (match_id, camera_id, epoch, segment)
      ) STRICT, WITHOUT ROWID;
      CREATE VIEW IF NOT EXISTS ready_media_segments AS
        SELECT i.*, s.bytes, b.bytes AS init_bytes FROM media_indexes i
        JOIN media_segments s USING (match_id, camera_id, epoch, segment)
        JOIN media_init b USING (match_id, camera_id, epoch)
        WHERE i.init_digest = b.digest AND i.segment_digest = s.digest AND i.segment_bytes = s.bytes;
    `);
    return prepareArchive(db, directory);
  } catch (error) {
    db.close();
    throw error;
  }
}

function prepareArchive(db, directory) {
  const epochKey = "match_id = ? AND camera_id = ? AND epoch = ?";
  const segmentKey = epochKey + " AND segment = ?";
  const getInit = db.prepare(
    "SELECT digest, bytes FROM media_init WHERE " + epochKey,
  );
  const getSegment = db.prepare(
    "SELECT digest, bytes FROM media_segments WHERE " + segmentKey,
  );
  const expectedInit = db.prepare(
    "SELECT init_digest FROM media_indexes WHERE " + epochKey + " LIMIT 1",
  );
  const getIndex = db.prepare(
    "SELECT init_digest, segment_digest, segment_bytes, payload FROM media_indexes WHERE " +
      segmentKey,
  );
  const addInit = db.prepare("INSERT INTO media_init VALUES (?, ?, ?, ?, ?)");
  const addSegment = db.prepare(
    "INSERT INTO media_segments VALUES (?, ?, ?, ?, ?, ?)",
  );
  const addIndex = db.prepare(
    "INSERT INTO media_indexes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
  );
  const addExposure = db.prepare(
    "INSERT INTO media_exposures VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
  );
  const cameras = db.prepare(
    "SELECT DISTINCT camera_id FROM ready_media_segments WHERE match_id = ? ORDER BY camera_id",
  );
  const fields =
    "epoch, segment, first_pts, duration, numerator, denominator, segment_digest, init_digest, bytes, init_bytes";
  const getReady = db.prepare(
    "SELECT " + fields + " FROM ready_media_segments WHERE " + segmentKey,
  );
  const readyInit = db.prepare(
    "SELECT init_digest AS digest, init_bytes AS bytes FROM ready_media_segments WHERE " +
      epochKey +
      " LIMIT 1",
  );
  const playlist = db.prepare(
    "SELECT " +
      fields +
      " FROM ready_media_segments WHERE match_id = ? AND camera_id = ? ORDER BY first_utc, epoch, segment",
  );
  const nearest = db.prepare(`
    SELECT r.epoch, r.segment, r.first_pts, r.numerator, r.denominator,
      CAST(e.utc AS TEXT) AS exposure, e.pts, e.duration_pts,
      CAST(e.utc - ? AS INTEGER) AS skew
    FROM media_exposures e JOIN ready_media_segments r USING (match_id, camera_id, epoch, segment)
    WHERE e.match_id = ? AND e.camera_id = ? AND e.utc BETWEEN ? AND ?
      AND abs(e.utc - ?) <= e.tolerance_us
    ORDER BY abs(e.utc - ?), e.utc, e.epoch, e.segment LIMIT 1
  `);
  const part = (digest) => join(directory, "parts", digest);
  function transaction(write) {
    db.exec("BEGIN IMMEDIATE");
    try {
      const result = write();
      db.exec("COMMIT");
      return result;
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
  }

  function publishFile(upload) {
    // 同一摘要只存一份。先提交完整文件，再提交引用它的 SQLite 记录。
    try {
      linkSync(upload.filename, part(upload.digest));
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }
  }

  return {
    uploads: join(directory, "uploads"),
    part,
    close: () => db.close(),
    writeInit(key, upload) {
      return transaction(() => {
        const existing = getInit.get(...key);
        if (existing) return existing.digest === upload.digest;
        const expected = expectedInit.get(...key);
        if (expected && expected.init_digest !== upload.digest) return false;
        publishFile(upload);
        addInit.run(...key, upload.digest, upload.bytes);
        return true;
      });
    },
    writeSegment(key, upload) {
      return transaction(() => {
        const existing = getSegment.get(...key);
        if (existing) return existing.digest === upload.digest;
        const expected = getIndex.get(...key);
        if (
          expected &&
          (expected.segment_digest !== upload.digest ||
            expected.segment_bytes !== upload.bytes)
        )
          return false;
        publishFile(upload);
        addSegment.run(...key, upload.digest, upload.bytes);
        return true;
      });
    },
    writeIndex(key, index) {
      return transaction(() => {
        const existing = getIndex.get(...key);
        if (existing) return existing.payload === index.payload;
        const epoch = key.slice(0, 3);
        const init = getInit.get(...epoch);
        const expected = expectedInit.get(...epoch);
        const segment = getSegment.get(...key);
        if (
          (init && init.digest !== index.initSha256) ||
          (expected && expected.init_digest !== index.initSha256) ||
          (segment &&
            (segment.digest !== index.segmentSha256 ||
              segment.bytes !== index.segmentBytes))
        )
          return false;
        const { frames } = index;
        addIndex.run(
          ...key,
          index.initSha256,
          index.segmentSha256,
          index.segmentBytes,
          index.firstPts,
          index.duration,
          index.numerator,
          index.denominator,
          frames[0].utc,
          frames.at(-1).utc,
          index.payload,
        );
        const [match, camera, mediaEpoch, number] = key;
        for (const frame of frames)
          addExposure.run(
            match,
            camera,
            frame.utc,
            mediaEpoch,
            number,
            frame.pts,
            frame.durationPts,
            frame.toleranceUs,
          );
        return true;
      });
    },
    cameraIds: (matchId) =>
      cameras.all(matchId).map(({ camera_id }) => camera_id),
    playlist: (matchId, cameraId) => playlist.all(matchId, cameraId),
    readySegment: (key) => getReady.get(...key),
    readyInit: (key) => readyInit.get(...key),
    locate(matchId, cameraId, utc) {
      const window = BigInt(maxFrameDurationUs / 2);
      const low = utc > window ? utc - window : 0n;
      const high = utc + window < maxUtcUs ? utc + window : maxUtcUs;
      return nearest.get(utc, matchId, cameraId, low, high, utc, utc);
    },
  };
}
