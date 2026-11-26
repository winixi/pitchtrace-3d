"""根据比赛配置与原片曝光时刻选择场地标定、归并可用视角。"""

from bisect import bisect_right
from dataclasses import dataclass
from itertools import groupby
import json
from pathlib import Path
import re

import numpy as np

from calibrate import image_size, is_identifier, load_cameras, unique_object, vector


def read_object(path):
    with path.open(encoding="utf-8") as source:
        value = json.load(source, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError(f"{path} 需要一个 JSON 对象")
    return value


def utc_us(value, name):
    if (not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", value)
            or int(value) > 2**63 - 1):
        raise ValueError(f"{name} 必须是 UTC Unix 微秒十进制字符串")
    return int(value)


def identifier(data, key):
    if not isinstance(data, dict) or not is_identifier(data.get(key)):
        raise ValueError(f"{key} 必须是有效的 v2 标识")
    return data[key]


def load_report(path, sample_source):
    return validate_report(read_object(path), path, sample_source)


def validate_report(report, path, sample_source):
    report_id = identifier(report, "calibrationId")
    if report.get("sampleSource") != sample_source:
        raise ValueError(f"标定报告 {path} 的 sampleSource 与本场不一致")
    cameras = load_cameras(report.get("cameras"), sample_source, path.parent)
    for camera_id, camera in cameras.items():
        vector(camera.get("translationM"), 3, f"机位 {camera_id} 的平移")
        rotation = camera.get("rotationWorldToCamera")
        if not isinstance(rotation, list) or len(rotation) != 3:
            raise ValueError(f"机位 {camera_id} 的旋转矩阵必须有三行")
        matrix = np.array([vector(row, 3, f"机位 {camera_id} 的旋转矩阵") for row in rotation], dtype=float)
        if (np.abs(matrix).max() > 1.00001 or not np.allclose(matrix @ matrix.T, np.eye(3), rtol=0, atol=1e-5)
                or np.linalg.det(matrix) <= 0):
            raise ValueError(f"机位 {camera_id} 的旋转矩阵需要正交且行列式为正")
    return report_id, cameras


def read_roster(roster):
    if not isinstance(roster, list):
        raise ValueError("roster 必须是名单数组")
    ids = set()
    for player in roster:
        roster_id = identifier(player, "rosterId")
        identifier(player, "teamId")
        number = player.get("number")
        if number is not None and (type(number) is not int or not 0 <= number <= 99):
            raise ValueError(f"名单 {roster_id} 的 number 必须为 0–99 整数或 null")
        if roster_id in ids:
            raise ValueError(f"名单 ID 重复：{roster_id}")
        ids.add(roster_id)
    return roster


@dataclass
class Calibration:
    from_us: int
    calibration_id: str
    report_id: str
    cameras: dict
    enabled: dict


def read_timeline(entries, directory, sample_source):
    if not isinstance(entries, list) or not entries:
        raise ValueError("calibrations 需要至少一个标定生效区间")
    timeline = []
    ids = set()
    reports = {}
    parameters = {}
    for entry in entries:
        version = identifier(entry, "calibrationId")
        start = utc_us(entry.get("fromUtcUs"), "fromUtcUs")
        if version in ids or (timeline and start <= timeline[-1].from_us):
            raise ValueError("标定生效时刻必须严格递增，calibrationId 必须唯一")
        report = entry.get("report")
        if not isinstance(report, str) or not report.strip():
            raise ValueError(f"标定 {version} 需要报告路径 report")
        path = (directory / report).resolve()
        if path not in reports:
            reports[path] = load_report(path, sample_source)
        report_id, cameras = reports[path]
        if report_id in parameters and parameters[report_id] != cameras:
            raise ValueError(f"外参报告 {report_id} 对应多组机位参数")
        parameters[report_id] = cameras
        disabled = entry.get("disabledCameras", [])
        if (not isinstance(disabled, list) or any(not is_identifier(cam) or cam not in cameras for cam in disabled)
                or len(disabled) != len(set(disabled))):
            raise ValueError(f"标定 {version} 的 disabledCameras 含未知或重复机位")
        enabled = {cam: calibration for cam, calibration in cameras.items() if cam not in disabled}
        timeline.append(Calibration(start, version, report_id, cameras, enabled))
        ids.add(version)
    return timeline


class MatchConfig:
    @classmethod
    def load(cls, path):
        path = Path(path)
        return cls(read_object(path), path.parent)

    def __init__(self, data, directory):
        self.match_id = identifier(data, "matchId")
        self.stream_epoch = identifier(data, "streamEpoch")
        self.start_us = utc_us(data.get("startUtcUs"), "startUtcUs")
        self.sample_source = data.get("sampleSource")
        if self.sample_source not in ("synthetic", "field"):
            raise ValueError("sampleSource 必须是 synthetic 或 field")
        self.frame_interval_us = data.get("frameIntervalUs", 100_000)
        self.max_skew_us = data.get("maxSkewUs", 5_000)
        if type(self.frame_interval_us) is not int or self.frame_interval_us <= 0:
            raise ValueError("frameIntervalUs 必须是正整数")
        if type(self.max_skew_us) is not int or self.max_skew_us < 0:
            raise ValueError("maxSkewUs 必须是非负整数")
        self.roster = read_roster(data.get("roster", []))
        self.identity_corrections = data.get("identityCorrections", [])
        self.pitch_length_m = data.get("pitchLengthM")
        self.goal_width_m = data.get("goalWidthM")
        self.timeline = read_timeline(data.get("calibrations"), directory, self.sample_source)
        self._starts = [entry.from_us for entry in self.timeline]
        if self._starts[0] > self.start_us:
            raise ValueError("首个标定区间需覆盖比赛开始时刻，尚未标定的机位可先禁用")
        self.calibration_sources = {entry.calibration_id: entry.report_id for entry in self.timeline}

    def _timed_rows(self, rows, replay):
        previous = -1
        cameras_at_time = set()
        for row in rows:
            camera = identifier(row, "cameraId")
            utc = utc_us(row.get("captureUtcUs"), "captureUtcUs")
            if utc < previous or (utc == previous and camera in cameras_at_time):
                raise ValueError(f"观测行曝光倒退或重复：{camera} {utc}")
            if utc != previous:
                cameras_at_time.clear()
            cameras_at_time.add(camera)
            previous = utc
            if utc < self.start_us:
                continue
            period = bisect_right(self._starts, utc) - 1
            entry = self.timeline[period]
            if camera not in entry.cameras:
                raise ValueError(f"机位 {camera} 在标定版本 {entry.calibration_id} 中未配置")
            if not replay or "imageSize" in row:
                size = image_size(row, camera)
                if camera in entry.enabled and size != entry.cameras[camera]["imageSize"]:
                    raise ValueError(f"机位 {camera} 在 {utc} 的观测分辨率与标定不一致")
            yield period, utc, camera, row

    def groups(self, rows, *, replay=False):
        """每个采样时段选取一组同步的原帧；标定切换处另起一组。"""
        timed = self._timed_rows(rows, replay)
        for period, records in groupby(timed, key=lambda record: record[0]):
            entry = self.timeline[period]
            latest = {}
            slot = None
            best = None
            for utc, simultaneous in groupby(records, key=lambda record: record[1]):
                for _, _, camera, row in simultaneous:
                    latest[camera] = (utc, row)
                # 先形成同步组，再按组内最早曝光分配时段，允许跨过取样时段的分界。
                latest = {cam: item for cam, item in latest.items() if utc - item[0] <= self.max_skew_us}
                earliest = min(time for time, _ in latest.values())
                candidate_slot = (earliest - self.start_us) // self.frame_interval_us
                if best is not None and candidate_slot != slot:
                    score, first, last, selected = best
                    yield first, entry.calibration_id, entry.enabled, selected
                    # 相邻场景的证据曝光时段必须互不重叠。
                    latest = {cam: item for cam, item in latest.items() if item[0] > last}
                    earliest = min(time for time, _ in latest.values())
                    candidate_slot = (earliest - self.start_us) // self.frame_interval_us
                    best = None
                slot = candidate_slot
                score = (len(entry.enabled.keys() & latest.keys()), len(latest), earliest - utc)
                if best is None or score > best[0]:
                    selected = sorted(latest.values(), key=lambda item: (item[0], item[1]["cameraId"]))
                    best = score, earliest, utc, [row for _, row in selected]
            if best is not None:
                yield best[1], entry.calibration_id, entry.enabled, best[3]
