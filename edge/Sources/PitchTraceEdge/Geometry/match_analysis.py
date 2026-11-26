"""合并跨帧身份、可观测骨骼和待复核的动作候选。"""

from collections import Counter, defaultdict, deque
import math

import numpy as np

from calibrate import is_identifier
from match_config import utc_us as parse_utc
from match_events import CandidateDetector
from pose import frame_ref, local_pose, measure_joints


def interval(row, label):
    start = parse_utc(row.get("fromUtcUs", "0"), label + ".fromUtcUs")
    end = parse_utc(row["untilUtcUs"], label + ".untilUtcUs") if "untilUtcUs" in row else math.inf
    if start >= end:
        raise ValueError(f"{label} 生效区间必须非空")
    return start, end


class Vote:
    """同一曝光的多机位一致读数只计一票。矛盾值出现后暂缓确认。"""

    def __init__(self):
        self.samples = deque(maxlen=7)
        self.confirmed = None

    def read(self, now, observations):
        values = set(value for value in observations if value is not None)
        if not values:
            return self.confirmed
        while self.samples and now - self.samples[0][0] > 2_000_000:
            self.samples.popleft()
        vote = next(iter(values)) if len(values) == 1 else None
        self.samples.append((now, vote))
        if vote != self.confirmed:
            self.confirmed = None
        counts = Counter(v for _, v in self.samples if v is not None)
        if vote is not None and counts[vote] >= max(3, math.ceil(.7 * len(self.samples))):
            self.confirmed = vote
        return self.confirmed


class MatchAnalysis:
    def __init__(self, roster, corrections=None, pitch_length_m=None, goal_width_m=None):
        self.entries = defaultdict(list)
        self.roster_by_id = {}
        for row in roster:
            start, end = interval(row, "roster")
            self.roster_by_id[row["rosterId"]] = row, start, end
            self.entries[row["teamId"], row.get("number")].append((start, end, row["rosterId"]))
        self.corrections = defaultdict(list)
        if corrections is not None and not isinstance(corrections, list):
            raise ValueError("identityCorrections 必须是数组")
        for row in corrections or []:
            if not isinstance(row, dict) or not is_identifier(row.get("trackId")):
                raise ValueError("身份修正需要有效的 trackId")
            team, number, roster_id = row.get("teamId"), row.get("number"), row.get("rosterId")
            if team is not None and not is_identifier(team):
                raise ValueError("身份修正含无效球队")
            if number is not None and (type(number) is not int or not 0 <= number <= 99):
                raise ValueError("身份修正含无效号码")
            start, end = interval(row, "identityCorrections")
            if roster_id is not None:
                if not is_identifier(roster_id) or roster_id not in self.roster_by_id:
                    raise ValueError("身份修正引用未知名单")
                entry, begin, finish = self.roster_by_id[roster_id]
                if team != entry["teamId"] or number != entry.get("number") or start < begin or end > finish:
                    raise ValueError("身份修正的球队、号码或时段与名单不一致")
            self.corrections[row["trackId"]].append((start, end, row))
        for spans in self.corrections.values():
            spans.sort(key=lambda item: item[0])
            if any(b[0] < a[1] for a, b in zip(spans, spans[1:])):
                raise ValueError("同一轨迹的身份修正时段不可重叠")
        self.votes = {}
        self.detector = CandidateDetector(pitch_length_m, goal_width_m)
        self.measurements = {}
        self.previous_time = None

    def reset_geometry(self):
        self.measurements.clear()
        self.detector.reset()

    def _identity(self, utc, track, rows):
        team_vote, number_vote = self.votes.setdefault(track, (Vote(), Vote()))
        team = team_vote.read(utc, (r["player"].get("teamId") for r in rows))
        number = number_vote.read(utc, (r["player"].get("number") for r in rows))
        manual = next((row for start, end, row in self.corrections.get(track, []) if start <= utc < end), None)
        roster_id = None
        if manual is not None:
            team, number, roster_id = manual.get("teamId"), manual.get("number"), manual.get("rosterId")
        if roster_id is None and team is not None and number is not None:
            matches = [rid for start, end, rid in self.entries[team, number] if start <= utc < end]
            if len(matches) == 1:
                roster_id = matches[0]
        return dict(teamId=team, number=number, rosterId=roster_id), manual is not None

    def update(self, utc_us, players, ball, cameras, evidence):
        if self.previous_time is not None and utc_us <= self.previous_time:
            raise ValueError("动作分析帧组的曝光时刻必须严格递增")
        previous, self.measurements = self.measurements, {}
        tracks, assignments, manual_tracks, result = {}, defaultdict(list), set(), []
        for original in players:
            player = dict(original)
            track = player["trackId"]
            rows = [r for r in evidence.get(track, []) if r["cameraId"] in cameras]
            identity, manual = self._identity(utc_us, track, rows)
            player.update(identity)
            if manual:
                manual_tracks.add(track)
            if player["rosterId"] is not None:
                assignments[player["rosterId"]].append(player)
            joints, audit = {}, {}
            if rows and player["state"] == "measured":
                joints, audit = measure_joints(rows, cameras, player["positionM"])
                smoothed = {}
                dt = utc_us - self.previous_time if self.previous_time is not None else None
                for name, point in joints.items():
                    old = previous.get(track, {}).get("joints", {}).get(name, {})
                    if old.get("status") == "measured" and dt is not None and dt <= 250_000:
                        alpha = 1 - math.exp(-dt / 45_000)
                        point = alpha * point + (1 - alpha) * np.asarray(old["smoothedM"])
                    smoothed[name] = point
                    audit[name]["smoothedM"] = point.tolist()
                yaw, bones = local_pose(smoothed)
                if yaw is not None:
                    player["yawRad"] = yaw
                if bones:
                    player["bones"] = bones
                tracks[track] = dict(player=player, joints=joints, refs=[frame_ref(r) for r in rows])
            self.measurements[track] = {"joints": audit, "frames": [frame_ref(r) for r in rows],
                                        "identitySource": "manual" if manual else "vote"}
            result.append(player)

        # 同场两个轨迹的同号辨识发生冲突时，只保留唯一的人工指定者。
        for claimants in assignments.values():
            if len(claimants) > 1:
                winners = [p for p in claimants if p["trackId"] in manual_tracks]
                for p in claimants:
                    if len(winners) != 1 or p is not winners[0]:
                        p["rosterId"] = None
        self.votes = {t: self.votes[t] for t in self.measurements}
        self.previous_time = utc_us
        ball_refs = [frame_ref(r) for r in evidence.get("_ball", []) if r["cameraId"] in (ball or {}).get("views", [])]
        return result, self.detector.update(utc_us, tracks, ball, ball_refs)
