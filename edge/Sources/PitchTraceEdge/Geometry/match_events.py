"""从连续实测的人、脚和球生成动作候选，保留起止曝光和原帧引用。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

import numpy as np


@dataclass
class Sample:
    utc: int
    tracks: dict
    ball: np.ndarray
    ball_refs: list
    owner: str | None
    ball_quality: float | None


def unique_owner(tracks, ball):
    distances = []
    for track, item in tracks.items():
        ankles = [point for name, point in item["joints"].items() if name in ("leftAnkle", "rightAnkle")]
        if ankles:
            distances.append((min(np.linalg.norm(p - ball) for p in ankles), track))
    distances.sort()
    if not distances or distances[0][0] > .65:
        return None
    if len(distances) >= 2 and distances[1][0] - distances[0][0] < .15:
        return None
    return distances[0][1]


def flat_position(sample, track):
    return np.asarray(sample.tracks[track]["player"]["positionM"])[[0, 2]]


def team(sample, track):
    return sample.tracks.get(track, {}).get("player", {}).get("teamId")


def event(kind, label, participants, samples, metrics):
    refs, quality = {}, []
    for sample in samples:
        source = list(sample.ball_refs)
        quality.append(sample.ball_quality)
        for track in participants:
            actor = sample.tracks.get(track)
            if actor is not None:
                source.extend(actor["refs"])
                quality.append(actor["player"].get("confidence"))
        for ref in source:
            key = tuple(ref.get(k) for k in ("cameraId", "mediaEpoch", "segment", "pts", "captureUtcUs"))
            refs[key] = ref
    return {
        "eventId": "-".join([kind, str(samples[0].utc), *participants]), "kind": kind,
        "label": label + "候选", "status": "candidate", "trackIds": participants,
        "startUtcUs": str(samples[0].utc), "endUtcUs": str(samples[-1].utc),
        "evidence": list(refs.values()), "metrics": metrics,
        "confidence": min(quality) if all(q is not None for q in quality) else None,
    }


class CandidateDetector:
    def __init__(self, pitch_length_m=None, goal_width_m=None):
        for key, value in (("pitchLengthM", pitch_length_m), ("goalWidthM", goal_width_m)):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{key} 必须为正数")
        if (pitch_length_m is None) != (goal_width_m is None):
            raise ValueError("射门方向判断需同时指定 pitchLengthM 和 goalWidthM")
        self.length = pitch_length_m
        self.goal = goal_width_m
        self.reset()

    def reset(self):
        self.history = deque(maxlen=256)
        self.pending_pass = None
        self.reported = set()
        self.slide_tracks = set()

    def toward_goal(self, origin, velocity):
        if self.length is None or abs(velocity[0]) < 1e-6:
            return False
        goal_x = math.copysign(self.length / 2, velocity[0])
        travel = (goal_x - origin[0]) / velocity[0]
        return (0 < travel <= 3 and abs(goal_x - origin[0]) <= 32
                and abs(origin[2] + velocity[2] * travel) <= self.goal / 2)

    def release(self, before, contact, after):
        actor = contact.owner
        if actor is None or actor not in before.tracks or actor not in after.tracks or actor == after.owner:
            return None
        dt = (contact.utc - before.utc) / 1e6
        previous_velocity = (contact.ball - before.ball) / dt
        velocity = (after.ball - contact.ball) / ((after.utc - contact.utc) / 1e6)
        speed = np.linalg.norm(velocity)
        if speed < 3 or speed - np.linalg.norm(previous_velocity) < 2 or np.linalg.norm(after.ball - contact.ball) < .25:
            return None
        for name in ("leftAnkle", "rightAnkle"):
            old = before.tracks[actor]["joints"].get(name)
            foot = contact.tracks[actor]["joints"].get(name)
            if old is None or foot is None or np.linalg.norm(foot - contact.ball) > .55:
                continue
            # 约束脚相对髋部（以轨迹平移近似）的摆动，排除整体随球平移。
            movement = foot - old
            movement[[0, 2]] -= flat_position(contact, actor) - flat_position(before, actor)
            forward_speed = float(movement @ velocity / (dt * speed))
            if forward_speed < 1.2:
                continue
            shot = self.toward_goal(contact.ball, velocity)
            self.pending_pass = (contact, after, actor)
            return event("shot" if shot else "kick", "射门" if shot else "踢球", [actor], [before, contact, after],
                         {"ballSpeedMps": float(speed), "swingAlongBallMps": forward_speed, "ankle": name})
        return None

    def receive(self, previous, current):
        if self.pending_pass is None:
            return None
        contact, departure, sender = self.pending_pass
        receiver = current.owner
        if current.utc - contact.utc > 3_000_000:
            self.pending_pass = None
        elif receiver is not None and receiver == previous.owner:
            self.pending_pass = None
            distance = np.linalg.norm(current.ball - contact.ball)
            if (receiver != sender and team(contact, sender) is not None
                    and team(contact, sender) == team(current, receiver) and distance >= 2):
                return event("pass", "传球", [sender, receiver], [contact, departure, previous, current],
                             {"travelM": float(distance)})
        elif receiver is not None and receiver != sender and team(current, receiver) != team(contact, sender):
            self.pending_pass = None
        return None

    def carry(self, current):
        owner = current.owner
        sequence = [current]
        for previous in reversed(self.history):
            if previous.owner != owner or current.utc - previous.utc > 2_000_000:
                break
            sequence.append(previous)
        start = sequence[-1]
        if owner is None or current.utc - start.utc < 600_000:
            return []
        travel = flat_position(current, owner) - flat_position(start, owner)
        distance = np.linalg.norm(travel)
        if distance < 1.6 or np.linalg.norm(current.ball - start.ball) < 1.6:
            return []
        events = []
        if "dribble" not in self.reported:
            self.reported.add("dribble")
            events.append(event("dribble", "带球", [owner], [start, current], {"travelM": float(distance)}))
        direction = travel / distance
        for other in sorted(start.tracks.keys() & current.tracks.keys()):
            if (other in self.reported or team(current, owner) is None or team(current, other) is None
                    or team(current, owner) == team(current, other)):
                continue
            ahead = flat_position(start, other) - flat_position(start, owner)
            behind = flat_position(current, other) - flat_position(current, owner)
            if float(ahead @ direction) <= .35 or float(behind @ direction) >= -.35:
                continue
            close = min((s for s in sequence if other in s.tracks),
                        key=lambda s: np.linalg.norm(flat_position(s, other) - flat_position(s, owner)))
            gap = np.linalg.norm(flat_position(close, other) - flat_position(close, owner))
            if gap < 1.5:
                self.reported.add(other)
                events.append(event("beat", "过人", [owner, other], [start, close, current], {"nearestM": float(gap)}))
        return events

    def slides(self, previous, current):
        carrier = previous.owner
        active, events = set(), []
        if carrier is not None and team(previous, carrier) is not None and carrier in current.tracks:
            for track, item in current.tracks.items():
                if (track not in previous.tracks or team(current, track) in (None, team(previous, carrier))
                        or np.linalg.norm(flat_position(current, track) - flat_position(current, carrier)) > 2):
                    continue
                speed = (np.linalg.norm(flat_position(current, track) - flat_position(previous, track))
                         / ((current.utc - previous.utc) / 1e6))
                if speed < 1.2:
                    continue
                for side in ("left", "right"):
                    hip = item["joints"].get(side + "Hip")
                    knee = item["joints"].get(side + "Knee")
                    ankle = item["joints"].get(side + "Ankle")
                    if hip is None or knee is None or ankle is None:
                        continue
                    leg = ankle - hip
                    path = np.linalg.norm(knee - hip) + np.linalg.norm(ankle - knee)
                    if (0 <= hip[1] < .65 and ankle[1] < .35 and np.linalg.norm(leg[[0, 2]]) > .6
                            and np.linalg.norm(leg) >= .9 * path and np.linalg.norm(ankle - current.ball) < .65):
                        active.add(track)
                        if track not in self.slide_tracks:
                            events.append(event("tackle", "铲球", [track, carrier], [previous, current],
                                                {"slideSpeedMps": float(speed), "hipHeightM": float(hip[1])}))
                        break
        self.slide_tracks = active
        return events

    def update(self, utc, tracks, ball, ball_refs):
        if (not ball or ball.get("state") != "measured" or not ball_refs
                or not tracks):
            self.reset()
            return []
        if self.history and utc - self.history[-1].utc > 250_000:
            self.reset()
        position = np.asarray(ball["positionM"])
        current = Sample(utc, tracks, position, ball_refs, unique_owner(tracks, position), ball.get("confidence"))
        events = []
        if self.history:
            previous = self.history[-1]
            if current.owner != previous.owner:
                self.reported.clear()
            if len(self.history) > 1:
                released = self.release(self.history[-2], previous, current)
                if released:
                    events.append(released)
            received = self.receive(previous, current)
            if received:
                events.append(received)
            events.extend(self.carry(current))
            events.extend(self.slides(previous, current))
        self.history.append(current)
        while self.history and utc - self.history[0].utc > 3_000_000:
            self.history.popleft()
        return events
