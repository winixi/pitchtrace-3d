"""按独立参考曝光、人物身份及动作时间窗评估一场离线重建。"""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys

from calibrate import unique_object, vector, is_identifier
from match_config import utc_us

KINDS = ("pass", "kick", "dribble", "beat", "tackle")


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def span(row):
    a, b = (utc_us(row.get(k), k) for k in ("startUtcUs", "endUtcUs"))
    if b < a:
        raise ValueError("动作或评估窗口的起止时刻倒置")
    return a, b


def assign(costs, gate):
    """矩形匈牙利算法。虚拟列使目标先最大化有效配对数，再最小化总距离。"""
    if not costs or not costs[0]:
        return {}
    n, width = len(costs), len(costs[0])
    m = width + n
    missing = (n + 1) * (gate + 1)
    rows = [[v if v <= gate else missing * 2 for v in row] + [missing] * n for row in costs]
    u, v = [0.] * (n + 1), [0.] * (m + 1)
    owner, parent = [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        owner[0] = i
        j = 0
        slack, visited = [math.inf] * (m + 1), set()
        while owner[j]:
            visited.add(j)
            current = owner[j]
            for k in range(1, m + 1):
                if k in visited:
                    continue
                difference = rows[current - 1][k - 1] - u[current] - v[k]
                if difference < slack[k]:
                    slack[k], parent[k] = difference, j
            next_col = min((k for k in range(1, m + 1) if k not in visited), key=slack.__getitem__)
            step = slack[next_col]
            for k in range(m + 1):
                if k in visited:
                    u[owner[k]] += step
                    v[k] -= step
                else:
                    slack[k] -= step
            j = next_col
        while j:
            previous = parent[j]
            owner[j], j = owner[previous], previous
    return {owner[j] - 1: j - 1 for j in range(1, width + 1)
            if owner[j] and costs[owner[j] - 1][j - 1] <= gate}


@dataclass
class Positions:
    count: int = 0
    measured: int = 0
    errors: list = field(default_factory=list)

    def sample(self, truth, actual):
        self.count += 1
        if actual is not None:
            self.measured += 1
            if truth is not None:
                self.errors.append(math.dist(truth, actual))

    def report(self):
        errors = sorted(self.errors)
        return dict(samples=self.count, measured=self.measured, coverage=ratio(self.measured, self.count),
                    errorSamples=len(errors), meanErrorM=ratio(sum(errors), len(errors)),
                    p95ErrorM=errors[math.ceil(.95 * len(errors)) - 1] if errors else None)


def read_reference(path, match):
    fingerprint = hashlib.sha256()
    def records(stream):
        for line in stream:
            fingerprint.update(line)
            yield json.loads(line.decode("utf-8"), object_pairs_hook=unique_object)

    with path.open("rb") as stream:
        rows = records(stream)
        header = next(rows, None)
        if (not isinstance(header, dict) or header.get("type") != "evaluation" or header.get("matchId") != match
                or header.get("sampleSource") not in ("synthetic", "field")):
            raise ValueError("真值首行必须声明 evaluation、matchId 与 sampleSource")
        duration = header.get("sampleDurationUs")
        if duration is not None and (type(duration) is not int or duration <= 0):
            raise ValueError("每个取样时隙时长必须为正整数微秒")
        gate = header.get("personGateM", 2.0)
        if type(gate) not in (int, float) or not math.isfinite(gate) or not 0 < gate <= 400:
            raise ValueError("人员匹配距离门限应为 0 至 400 米的正数")
        window = span(header["eventWindow"]) if "eventWindow" in header else None
        frames, events = {}, []
        previous = None
        for row in rows:
            if row.get("type") == "event":
                interval = span(row)
                people = row.get("personIds")
                if (row.get("kind") not in (*KINDS, "shot") or not isinstance(people, list) or not people
                        or not all(map(is_identifier, people)) or len(set(people)) != len(people)):
                    raise ValueError("动作真值需给出有效类别与有序参与者")
                if window is None or not window[0] <= interval[0] <= window[1]:
                    raise ValueError("真值动作起始时刻须位于已完整标注的 eventWindow")
                entry = (row["kind"], interval, tuple(people))
                if entry in events:
                    raise ValueError("重复的真值动作")
                events.append(entry)
                continue
            t = utc_us(row.get("captureUtcUs"), "captureUtcUs")
            if previous is not None and t - previous < (duration or 1):
                raise ValueError("真值曝光必须递增，取样时隙不能重叠")
            previous = t
            players, seen = row.get("players"), set()
            if not isinstance(players, list) or len(players) > 32 or "ball" not in row:
                raise ValueError("每个真值曝光须完整标注人员数组与球")
            for person in players:
                who = person.get("personId")
                if not is_identifier(who) or who in seen:
                    raise ValueError("真值 personId 无效或同帧重复")
                seen.add(who)
                vector(person.get("positionM"), 3, "真值人员位置")
                if person.get("number") is not None:
                    num = person["number"]
                    if type(num) is not int or not 0 <= num <= 99 or not is_identifier(person.get("numberSegmentId")):
                        raise ValueError("可读号码须在 0–99 内，并附 numberSegmentId")
                for name, point in person.get("joints", {}).items():
                    if not is_identifier(name):
                        raise ValueError("关节名无效")
                    if point is not None:
                        vector(point, 3, "关节真值")
            ball = row["ball"]
            if ball is not None:
                if ball.get("kind") not in ("ground", "high"):
                    raise ValueError("可见球需指定 ground 或 high")
                if ball.get("positionM") is not None:
                    vector(ball["positionM"], 3, "球心真值")
            frames[t] = row
        if not frames:
            raise ValueError("至少需要一个独立参考曝光")
    return header, frames, events, gate, window, fingerprint.hexdigest()


def measured(observation):
    return observation.get("positionM") if observation and observation.get("state") == "measured" else None


class Scores:
    def __init__(self):
        self.players, self.joints = Positions(), Positions()
        self.balls = {k: Positions() for k in ("ground", "high")}
        self.switches = self.extra_players = self.extra_balls = 0
        self.last_track = {}
        self.identities = defaultdict(list)
        self.numbers = {}

    def frame(self, t, ref, scene, evidence, gate):
        truth = sorted(ref["players"], key=lambda p: p["personId"])
        predicted = sorted((p for p in scene.get("players", []) if measured(p) is not None),
                           key=lambda p: p["trackId"])
        pairs = assign([[math.dist(person["positionM"], p["positionM"]) for p in predicted]
                        for person in truth], gate)
        self.extra_players += len(predicted) - len(pairs)
        for index, person in enumerate(truth):
            actual = predicted[pairs[index]] if index in pairs else {}
            who, track = person["personId"], actual.get("trackId")
            detail = evidence.get("measurements", {}).get(track, {})
            self.players.sample(person["positionM"], measured(actual))
            if track:
                if self.last_track.get(who, track) != track:
                    self.switches += 1
                self.last_track[who] = track
                self.identities[track].append((t, who))
            for name, xyz in person.get("joints", {}).items():
                audit = detail.get("joints", {}).get(name, {})
                self.joints.sample(xyz, audit.get("reconstructedM") if audit.get("status") == "measured" else None)
            if person.get("number") is not None:
                expected, answers = self.numbers.setdefault((who, person["numberSegmentId"]), (person["number"], set()))
                if expected != person["number"]:
                    raise ValueError("同一真值号码段出现不同号码")
                if detail.get("identitySource") == "vote" and actual.get("number") is not None:
                    answers.add(actual["number"])
        visible, actual = ref["ball"], measured(scene.get("ball"))
        if visible is None:
            self.extra_balls += int(actual is not None)
        else:
            self.balls[visible["kind"]].sample(visible.get("positionM"), actual)

    def event_people(self, interval, tracks):
        people = []
        for track in tracks:
            history = self.identities[track]
            lo = bisect_left(history, interval[0], key=lambda pair: pair[0])
            hi = bisect_right(history, interval[1], key=lambda pair: pair[0])
            ids = {who for _, who in history[lo:hi]}
            if not ids:
                if lo and interval[0] - history[lo - 1][0] <= 1_000_000:
                    ids.add(history[lo - 1][1])
                if hi < len(history) and history[hi][0] - interval[1] <= 1_000_000:
                    ids.add(history[hi][1])
            if len(ids) != 1:
                return None
            people.append(ids.pop())
        return tuple(people)


def event_scores(refs, candidates, scores):
    # 同一发力归为踢球/射门一类。类别、参与者顺序和时间窗都须相符。
    def category(kind):
        return "kick" if kind == "shot" else kind

    output, f1s = {}, []
    for kind in KINDS:
        targets = [(interval, people) for name, interval, people in refs if category(name) == kind]
        detected = [(interval, scores.event_people(interval, tracks)) for name, interval, tracks in candidates
                    if category(name) == kind]
        # 按参与者拆分，避免整场同时比较无关球员的所有动作。
        tp, overlap_ratios = 0, []
        for ids in {r[1] for r in targets}:
            expected = [interval for interval, people in targets if people == ids]
            relevant = [interval for interval, people in detected if people == ids]
            matches = assign([[abs(start - s) for s, _ in relevant] for start, _ in expected], 1_000_000)
            tp += len(matches)
            for i, j in matches.items():
                start, end = expected[i]
                if end > start:
                    s, e = relevant[j]
                    overlap_ratios.append(max(0, min(end, e) - max(start, s)) / (max(end, e) - min(start, s)))
        n, m = len(targets), len(detected)
        f1 = ratio(2 * tp, n + m)
        output[kind] = dict(reference=n, candidates=m, truePositive=tp, falsePositive=m - tp,
                            falseNegative=n - tp, precision=ratio(tp, m), recall=ratio(tp, n), f1=f1,
                            durationMatches=len(overlap_ratios),
                            meanIntervalIoU=ratio(sum(overlap_ratios), len(overlap_ratios)))
        if f1 is not None:
            f1s.append(f1)
    return dict(classes=output, macroF1=ratio(sum(f1s), len(f1s)), macroClasses=len(f1s))


def evaluate(database, match, epoch, path):
    if not is_identifier(match) or not is_identifier(epoch):
        raise ValueError("比赛或周期标识无效")
    header, refs, events, gate, window, reference_hash = read_reference(path, match)
    scores, candidates, matched = Scores(), [], set()
    db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("BEGIN")
        run = db.execute("SELECT provenance FROM edge_runs WHERE match_id=? AND epoch=?", (match, epoch)).fetchone()
        if not run or json.loads(run[0]).get("sampleSource") != header["sampleSource"]:
            raise ValueError("归档中缺少指定运行或真值样本来源不一致")
        previous = -1
        for payload, raw_evidence in db.execute(
                "SELECT payload,evidence FROM edge_scenes WHERE match_id=? AND epoch=? ORDER BY seq", (match, epoch)):
            scene = json.loads(payload)
            t = int(scene["captureUtcUs"])
            if t <= previous:
                raise ValueError("场景曝光必须严格递增")
            previous = t
            if t in refs:
                matched.add(t)
                scores.frame(t, refs[t], scene, json.loads(raw_evidence) if raw_evidence else {}, gate)
            if window:
                for event in scene.get("events", []):
                    interval = span(event)
                    if window[0] <= interval[0] <= window[1]:
                        kind, actors = event["kind"], event["trackIds"]
                        if kind not in (*KINDS, "shot"):
                            raise ValueError(f"归档含未知动作 {kind}")
                        candidates.append((kind, interval, actors))
        for t in refs.keys() - matched:
            scores.frame(t, refs[t], {}, {}, gate)
    finally:
        db.close()
    answered = [entry for entry in scores.numbers.values() if entry[1]]
    correct = sum(answers == {number} for number, answers in answered)
    duration = header.get("sampleDurationUs")
    minutes = scores.players.count * duration / 60e6 if duration else None
    ball = Positions()
    for result in scores.balls.values():
        ball.count += result.count
        ball.measured += result.measured
        ball.errors.extend(result.errors)
    return dict(matchId=match, streamEpoch=epoch, sampleSource=header["sampleSource"], truthSha256=reference_hash,
                referenceFrames=len(refs), matchedFrames=len(matched), personGateM=gate,
                players=dict(scores.players.report(), falsePositives=scores.extra_players,
                             idSwitches=scores.switches, playerMinutes=minutes,
                             switchesPerTenPlayerMinutes=ratio(10 * scores.switches, minutes)),
                ball=dict(ball.report(), **{k: v.report() for k, v in scores.balls.items()},
                          falsePositives=scores.extra_balls),
                joints=scores.joints.report(),
                numbers=dict(readableSegments=len(scores.numbers), confirmed=len(answered), correct=correct,
                             coverage=ratio(len(answered), len(scores.numbers)), accuracy=ratio(correct, len(answered))),
                events=event_scores(events, candidates, scores) if window else None)


if __name__ == "__main__":
    try:
        database, match, epoch, truth = sys.argv[1:]
        print(json.dumps(evaluate(Path(database), match, epoch, Path(truth)), ensure_ascii=False,
                         indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as error:
        sys.exit(f"离线评估失败：{error}")
