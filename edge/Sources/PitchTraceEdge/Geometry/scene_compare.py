"""按原曝光时刻对齐同一比赛的两次离线复算。"""

from collections import Counter
import json
import math

from scene_archive import _json, _stream


def _metric():
    return dict(aOnly=0, bOnly=0, paired=0, stateChanges=0, positionPairs=0,
                changedPositions=0, sumDeltaM=0.0, maxDeltaM=None)


def _observe(metric, a, b):
    if a is None or b is None:
        if a is not b:
            metric["aOnly" if b is None else "bOnly"] += 1
        return
    metric["paired"] += 1
    metric["stateChanges"] += a["state"] != b["state"]
    first, second = a.get("positionM"), b.get("positionM")
    if first is not None and second is not None:
        delta = math.dist(first, second)
        metric["positionPairs"] += 1
        metric["changedPositions"] += delta != 0
        metric["sumDeltaM"] += delta
        metric["maxDeltaM"] = max(metric["maxDeltaM"] or 0, delta)


def _timeline(db, match, epoch):
    # UTC 允许 19 位十进制字符串，按长度、字典序排序可避免 SQLite 整数溢出。
    rows = db.execute("""
        SELECT json_extract(payload,'$.captureUtcUs') AS utc,payload
        FROM edge_scenes WHERE match_id=? AND epoch=? ORDER BY length(utc), utc, seq
    """, _stream(match, epoch))
    for utc, payload in rows:
        yield int(utc), json.loads(payload)


def compare(archive, match, epoch_a, epoch_b):
    if epoch_a == epoch_b:
        raise ValueError("请选择两个不同的场景周期")
    report = {"matchId": match, "epochA": epoch_a, "epochB": epoch_b, "alignment": "captureUtcUs",
              "pairedFrames": 0, "aOnlyFrames": 0, "bOnlyFrames": 0,
              "calibrationChanges": 0, "events": {"onlyA": 0, "onlyB": 0, "byLabel": {}, "examples": []},
              "ball": _metric(), "tracks": {}}
    tracks = report["tracks"]
    with archive.db:
        archive.db.execute("BEGIN")
        left, right = _timeline(archive.db, match, epoch_a), _timeline(archive.db, match, epoch_b)
        a, b = next(left, None), next(right, None)
        for epoch, first in ((epoch_a, a), (epoch_b, b)):
            if first is None and archive.db.execute(
                "SELECT 1 FROM edge_runs WHERE match_id=? AND epoch=?", (match, epoch)
            ).fetchone() is None:
                raise ValueError(f"找不到待比对的比赛周期 {(match, epoch)}")
        while a is not None or b is not None:
            if b is None or (a is not None and a[0] < b[0]):
                before, after = a[1], None
                report["aOnlyFrames"] += 1
                a = next(left, None)
            elif a is None or b[0] < a[0]:
                before, after = None, b[1]
                report["bOnlyFrames"] += 1
                b = next(right, None)
            else:
                before, after = a[1], b[1]
                report["pairedFrames"] += 1
                report["calibrationChanges"] += before["calibrationId"] != after["calibrationId"]
                a, b = next(left, None), next(right, None)
            before, after = before or {}, after or {}
            _observe(report["ball"], before.get("ball"), after.get("ball"))
            players_a = {p["trackId"]: p for p in before.get("players", [])}
            players_b = {p["trackId"]: p for p in after.get("players", [])}
            for track_id in players_a.keys() | players_b.keys():
                if track_id not in tracks:
                    tracks[track_id] = dict(_metric(), identityChanges=0, bonesChanges=0)
                row = tracks[track_id]
                pa, pb = players_a.get(track_id), players_b.get(track_id)
                _observe(row, pa, pb)
                if pa is not None and pb is not None:
                    row["identityChanges"] += any(pa.get(k) != pb.get(k) for k in ("number", "teamId", "rosterId"))
                    row["bonesChanges"] += pa.get("bones") != pb.get("bones")
            event_a = Counter(_json(e) for e in before.get("events", []))
            event_b = Counter(_json(e) for e in after.get("events", []))
            changes = report["events"]
            for side, entries in (("onlyA", event_a - event_b), ("onlyB", event_b - event_a)):
                for payload, count in entries.items():
                    label = json.loads(payload).get("label", "")
                    changes[side] += count
                    changes["byLabel"].setdefault(label, {"onlyA": 0, "onlyB": 0})[side] += count
                    if len(changes["examples"]) < 10:
                        changes["examples"].append({"side": side, "captureUtcUs": (before or after)["captureUtcUs"],
                                                    "label": label, "count": count})
    for row in [report["ball"], *tracks.values()]:
        distance = row.pop("sumDeltaM")
        row["meanDeltaM"] = distance / row["positionPairs"] if row["positionPairs"] else None
    return report
