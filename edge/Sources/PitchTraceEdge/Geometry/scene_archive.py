"""Edge 本机场景、原帧证据及按目标网关隔离的持久化回执。"""

import argparse
from contextlib import ExitStack
import fcntl
import http.client
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import urlsplit

from calibrate import is_identifier


class ScenePublishError(ValueError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _same_json(left, right):
    # JSON 数字的 1 与 1.0 相同，布尔 true 与数字 1 不同。
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_json(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return isinstance(left, bool) == isinstance(right, bool) and left == right


def _stream(match, epoch):
    if not is_identifier(match) or not is_identifier(epoch):
        raise ValueError("比赛 ID 与场景周期必须是有效的 v2 标识")
    return match, epoch


def _index(value):
    return type(value) is int and 0 <= value <= 9007199254740991


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _observation(value):
    if not isinstance(value, dict) or value.get("state") not in ("measured", "predicted", "lost"):
        raise ValueError("场景测量缺少有效状态")
    position = value.get("positionM")
    if value["state"] == "lost":
        if position is not None:
            raise ValueError("已丢失的测量不能带位置")
    elif not isinstance(position, list) or len(position) != 3 or not all(
        _number(v) and abs(v) <= 200 for v in position
    ):
        raise ValueError("测量位置必须是范围内的三维米制坐标")
    if "confidence" in value and not (_number(value["confidence"]) and 0 <= value["confidence"] <= 1):
        raise ValueError("测量置信度超出范围")
    if "views" in value and (not isinstance(value["views"], list) or not all(map(is_identifier, value["views"]))):
        raise ValueError("测量来源必须是有效的机位标识列表")


def _frame_key(frame):
    if not isinstance(frame, dict) or frame.get("v") != 2:
        raise ValueError("场景快照必须为 v2 对象")
    match, epoch = _stream(frame.get("matchId"), frame.get("streamEpoch"))
    if not _index(frame.get("seq")) or not _index(frame.get("matchTimeMs")):
        raise ValueError("场景序号与比赛毫秒必须是非负安全整数")
    utc = frame.get("captureUtcUs")
    if not isinstance(utc, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", utc):
        raise ValueError("captureUtcUs 必须是规范的 UTC 微秒字符串")
    if not is_identifier(frame.get("calibrationId")) or "ball" not in frame:
        raise ValueError("场景缺少标定版本或足球字段")
    players = frame.get("players")
    if not isinstance(players, list) or len(players) > 32:
        raise ValueError("场景必须包含 0–32 条人员轨迹")
    tracks = [p.get("trackId") if isinstance(p, dict) else None for p in players]
    if not all(is_identifier(t) for t in tracks) or len(set(tracks)) != len(tracks):
        raise ValueError("人员轨迹标识无效或重复")
    for player in players:
        _observation(player)
    if frame["ball"] is not None:
        _observation(frame["ball"])
    events = frame.get("events", [])
    if not isinstance(events, list) or not all(isinstance(e, dict) and isinstance(e.get("label", ""), str)
                                              for e in events):
        raise ValueError("事件必须是对象列表")
    return match, epoch, frame["seq"]


class SceneArchive:
    def __init__(self, path):
        self.resources = ExitStack()
        try:
            location = Path(path).resolve()
            if location.name == "scenes.sqlite" and (location.parent / "run-state.json").is_file():
                guard = self.resources.enter_context((location.parent / "scenes-usage.lock").open("a+b"))
                fcntl.flock(guard, fcntl.LOCK_SH | fcntl.LOCK_NB)
                if not location.exists() and (location.parent / "run-retention.jsonl").exists():
                    raise ValueError("该场景归档已按留存策略清理")
            self.db = sqlite3.connect(path, timeout=15)
            self.resources.callback(self.db.close)
            # 并发首次开库时 SQLite 的日志模式升级可能直接返回 BUSY。
            deadline = time.monotonic() + 15
            while True:
                try:
                    self.db.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as error:
                    if "locked" not in str(error) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS edge_runs (
                    match_id TEXT NOT NULL, epoch TEXT NOT NULL, provenance TEXT NOT NULL,
                    PRIMARY KEY (match_id,epoch)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS edge_scenes (
                    match_id TEXT NOT NULL, epoch TEXT NOT NULL, seq INTEGER NOT NULL,
                    payload TEXT NOT NULL, evidence TEXT,
                    PRIMARY KEY (match_id,epoch,seq)
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS edge_scenes_by_time
                    ON edge_scenes(match_id,epoch,json_extract(payload,'$.matchTimeMs'),seq);
                CREATE TABLE IF NOT EXISTS edge_scene_receipts (
                    gateway TEXT NOT NULL, match_id TEXT NOT NULL, epoch TEXT NOT NULL, seq INTEGER NOT NULL,
                    PRIMARY KEY (gateway,match_id,epoch,seq),
                    FOREIGN KEY (match_id,epoch,seq) REFERENCES edge_scenes(match_id,epoch,seq)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS edge_scene_destinations (
                    gateway TEXT NOT NULL, match_id TEXT NOT NULL, epoch TEXT NOT NULL,
                    PRIMARY KEY (gateway,match_id,epoch)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS edge_scene_pending (
                    gateway TEXT NOT NULL, match_id TEXT NOT NULL, epoch TEXT NOT NULL, seq INTEGER NOT NULL,
                    PRIMARY KEY (gateway,match_id,epoch,seq)
                ) WITHOUT ROWID;
                CREATE TRIGGER IF NOT EXISTS edge_queue_new_scene AFTER INSERT ON edge_scenes BEGIN
                    INSERT INTO edge_scene_pending
                      SELECT gateway,match_id,epoch,new.seq FROM edge_scene_destinations
                      WHERE match_id=new.match_id AND epoch=new.epoch;
                END;
            """)
        except BaseException:
            self.resources.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.resources.close()

    def register_run(self, match_id, stream_epoch, provenance):
        stream = _stream(match_id, stream_epoch)
        if not isinstance(provenance, dict) or not provenance:
            raise ValueError("运行记录需要配置、输入及软件版本来源")
        payload = _json(provenance)
        with self.db:
            inserted = self.db.execute("""
                INSERT INTO edge_runs VALUES(?,?,?) ON CONFLICT(match_id,epoch) DO NOTHING
            """, (*stream, payload)).rowcount
            if not inserted and not _same_json(self.run_info(*stream), provenance):
                raise ValueError(f"比赛周期 {stream} 已用于不同输入、配置或软件版本，请指定新 streamEpoch")
        return bool(inserted)

    def run_info(self, match_id, stream_epoch):
        row = self.db.execute("SELECT provenance FROM edge_runs WHERE match_id=? AND epoch=?",
                              _stream(match_id, stream_epoch)).fetchone()
        if row is None:
            raise ValueError(f"找不到比赛周期 {(match_id, stream_epoch)} 的运行记录")
        return json.loads(row[0])

    def append(self, frame, evidence=None):
        """同一键的场景与复核证据在同一事务中提交。"""
        key = _frame_key(frame)
        payload = _json(frame)
        if len(payload.encode("utf-8")) > 256000:
            raise ValueError(f"场景帧 {key} 超出本地网关的 256000 字节限制")
        proof = None if evidence is None else _json(evidence)
        with self.db:
            inserted = self.db.execute("""
                INSERT INTO edge_scenes VALUES(?,?,?,?,?)
                ON CONFLICT(match_id,epoch,seq) DO NOTHING
            """, (*key, payload, proof)).rowcount
            if not inserted:
                old, stored_proof = self.db.execute("""
                    SELECT payload,evidence FROM edge_scenes WHERE match_id=? AND epoch=? AND seq=?
                """, key).fetchone()
                if old != payload and not _same_json(json.loads(old), frame):
                    raise ValueError(f"本地场景键冲突 {key}")
                if proof is not None and stored_proof is None:
                    self.db.execute("""
                        UPDATE edge_scenes SET evidence=? WHERE match_id=? AND epoch=? AND seq=?
                    """, (proof, *key))
                elif proof is not None and stored_proof != proof and not _same_json(json.loads(stored_proof), evidence):
                    raise ValueError(f"本地原帧证据冲突 {key}")
        return bool(inserted)

    def frames(self, match_id, stream_epoch, *, from_ms=None, to_ms=None, track_id=None,
               event_label=None, calibration_id=None):
        params = list(_stream(match_id, stream_epoch))
        conditions = ["match_id=?", "epoch=?"]
        for op, bound in ((">=", from_ms), ("<=", to_ms)):
            if bound is not None:
                if not _index(bound):
                    raise ValueError("时间窗须使用非负的比赛毫秒")
                conditions.append("json_extract(payload,'$.matchTimeMs')" + op + "?")
                params.append(bound)
        if from_ms is not None and to_ms is not None and from_ms > to_ms:
            raise ValueError("起始比赛时间晚于结束时间")
        if track_id is not None:
            if not is_identifier(track_id):
                raise ValueError("轨迹 ID 无效")
            conditions.append("""EXISTS (SELECT 1 FROM json_each(payload,'$.players') p
                               WHERE json_extract(p.value,'$.trackId')=?)""")
            params.append(track_id)
        if event_label is not None:
            conditions.append("""EXISTS (SELECT 1 FROM json_each(payload,'$.events') e
                               WHERE json_extract(e.value,'$.label')=?)""")
            params.append(event_label)
        if calibration_id is not None:
            if not is_identifier(calibration_id):
                raise ValueError("标定版本无效")
            conditions.append("json_extract(payload,'$.calibrationId')=?")
            params.append(calibration_id)
        sql = "SELECT payload FROM edge_scenes WHERE " + " AND ".join(conditions) + " ORDER BY seq"
        rows = self.db.execute(sql, params)
        for payload, in rows:
            yield json.loads(payload)

    def last_seq(self, match_id, stream_epoch):
        return self.db.execute("SELECT max(seq) FROM edge_scenes WHERE match_id=? AND epoch=?",
                               _stream(match_id, stream_epoch)).fetchone()[0]

    def frame(self, match_id, stream_epoch, seq):
        row = self.db.execute("SELECT payload FROM edge_scenes WHERE match_id=? AND epoch=? AND seq=?",
                              (*_stream(match_id, stream_epoch), seq)).fetchone()
        if row is None:
            raise ValueError(f"本地场景缺少序号 {seq}")
        return json.loads(row[0])

    def evidence(self, match_id, stream_epoch, seq):
        if not _index(seq):
            raise ValueError("证据查询序号无效")
        row = self.db.execute("""
            SELECT evidence FROM edge_scenes WHERE match_id=? AND epoch=? AND seq=?
        """, (*_stream(match_id, stream_epoch), seq)).fetchone()
        if row is None:
            raise ValueError(f"找不到场景帧 {(match_id, stream_epoch, seq)}")
        return None if row[0] is None else json.loads(row[0])

    def publish(self, gateway, match_id, stream_epoch, backfill_bytes_per_second=0, max_frames=None):
        """先发当前帧，再逐帧补历史；每次网络请求前释放数据库读锁。"""
        stream = _stream(match_id, stream_epoch)
        if type(backfill_bytes_per_second) is not int or backfill_bytes_per_second < 0:
            raise ValueError("补传字节速率应为非负整数")
        if max_frames is not None and (type(max_frames) is not int or max_frames < 1):
            raise ValueError("单批提交数量必须为正整数")
        url = urlsplit(gateway)
        if url.scheme not in ("http", "https") or not url.hostname or url.query or url.fragment or url.username is not None:
            raise ValueError("网关地址须为 HTTP(S) 基址，不含用户信息、查询参数或片段")
        base = url.path.rstrip("/")
        port = url.port
        gateway_key = url.geturl().rstrip("/")
        destination = (gateway_key, *stream)
        with self.db:
            if self.db.execute("INSERT OR IGNORE INTO edge_scene_destinations VALUES(?,?,?)", destination).rowcount:
                self.db.execute("""
                    INSERT INTO edge_scene_pending SELECT ?,s.match_id,s.epoch,s.seq FROM edge_scenes s
                    WHERE s.match_id=? AND s.epoch=? AND NOT EXISTS (SELECT 1 FROM edge_scene_receipts r
                    WHERE r.gateway=? AND (r.match_id,r.epoch,r.seq)=(s.match_id,s.epoch,s.seq))
                """, (*destination, gateway_key))
        endpoint = f"{base}/v2/matches/{match_id}/frames"
        connection_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(url.hostname, port, timeout=15)
        confirmed = 0
        latest_sent = -1
        history_ready = 0
        pending = """SELECT p.seq,s.payload FROM edge_scene_pending p JOIN edge_scenes s USING(match_id,epoch,seq)
                     WHERE p.gateway=? AND p.match_id=? AND p.epoch=? """
        try:
            while max_frames is None or confirmed < max_frames:
                latest = self.db.execute("""
                    SELECT seq FROM edge_scenes WHERE match_id=? AND epoch=? ORDER BY seq DESC LIMIT 1
                """, stream).fetchone()
                if latest is None:
                    break
                is_current = latest[0] > latest_sent
                if is_current:
                    latest_sent = latest[0]
                    row = self.db.execute(pending + "AND p.seq=?", (*destination, latest_sent)).fetchone()
                    if row is None:
                        continue
                else:
                    row = self.db.execute(pending + "ORDER BY p.seq LIMIT 1", destination).fetchone()
                    if row is None:
                        break
                    delay = history_ready - time.monotonic()
                    if delay > 0:
                        # 等待补传额度时继续轮询刚生成的当前帧。
                        time.sleep(min(delay, 0.1))
                        continue
                seq, payload = row
                body = payload.encode("utf-8")
                try:
                    connection.request("POST", endpoint, body, {"Content-Type": "application/json"})
                    reply = connection.getresponse()
                    detail = reply.read(1024).decode("utf-8", errors="replace")
                    if reply.status != 202:
                        raise ScenePublishError(f"场景 {(*stream, seq)} 返回 HTTP {reply.status} {detail}；仍待提交",
                                                status=reply.status)
                except (OSError, http.client.HTTPException) as error:
                    raise ScenePublishError(f"场景 {(*stream, seq)} 未获网关确认；保留待办：{error}") from error
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO edge_scene_receipts VALUES(?,?,?,?)",
                                    (gateway_key, *stream, seq))
                    self.db.execute("DELETE FROM edge_scene_pending WHERE gateway=? AND match_id=? AND epoch=? AND seq=?",
                                    (*destination, seq))
                confirmed += 1
                if not is_current and backfill_bytes_per_second:
                    history_ready = time.monotonic() + len(body) / backfill_bytes_per_second
        finally:
            connection.close()
        return confirmed


def main():
    parser = argparse.ArgumentParser(description="Edge 离线场景归档")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("publish", "export", "evidence", "info", "compare"):
        command = sub.add_parser(name)
        command.add_argument("db", type=Path)
        if name == "publish":
            command.add_argument("gateway")
            command.add_argument("--backfill-bytes-per-second", type=int, default=0)
        if name == "export":
            command.add_argument("--from-ms", type=int)
            command.add_argument("--to-ms", type=int)
            command.add_argument("--track-id")
            command.add_argument("--event-label")
            command.add_argument("--calibration-id")
        command.add_argument("match")
        command.add_argument("epoch")
        if name == "evidence":
            command.add_argument("seq", type=int)
        if name == "compare":
            command.add_argument("other_epoch")
    args = parser.parse_args()
    if not args.db.is_file():
        raise ValueError(f"找不到本地场景数据库 {args.db}")
    with SceneArchive(args.db) as archive:
        if args.command == "publish":
            count = archive.publish(args.gateway, args.match, args.epoch, args.backfill_bytes_per_second)
            print(f"已确认 {count} 个场景快照。")
        elif args.command == "export":
            for frame in archive.frames(args.match, args.epoch, from_ms=args.from_ms, to_ms=args.to_ms,
                                        track_id=args.track_id, event_label=args.event_label,
                                        calibration_id=args.calibration_id):
                print(_json(frame))
        elif args.command == "info":
            print(_json(archive.run_info(args.match, args.epoch)))
        elif args.command == "compare":
            from scene_compare import compare
            print(_json(compare(archive, args.match, args.epoch, args.other_epoch)))
        else:
            print(_json(archive.evidence(args.match, args.epoch, args.seq)))


if __name__ == "__main__":
    try:
        main()
    except (OSError, sqlite3.Error, ValueError, TypeError) as error:
        sys.exit(f"场景归档失败：{error}")
