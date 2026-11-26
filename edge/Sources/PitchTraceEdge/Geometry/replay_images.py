"""按 replay 提供的曝光与 PTS，从对应媒体周期逐帧解码原始图像。"""

import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile

import cv2

from calibrate import is_identifier, unique_object


def copy_verified(source, output, expected):
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError(f"原片摘要与索引不符：{source}")


class CameraDecoder:
    def __init__(self, root, row, work):
        camera = row["cameraId"]
        period = row.get("mediaEpoch")
        segment = row["segment"]
        if not is_identifier(camera) or (period is not None and not is_identifier(period)):
            raise ValueError("机位或媒体周期标识无效")
        if type(segment) is not int or segment < 0:
            raise ValueError("媒体段号无效")
        directory = root / camera
        if period is not None:
            directory = directory / "epochs" / period
        self.key = period, segment
        base = directory / "segments" / str(segment)
        with base.with_suffix(".json").open(encoding="utf-8") as stream:
            index = json.load(stream, object_pairs_hook=unique_object)
        self.frames = index["frames"]
        self.time_base = index["timeBase"]
        self.offset = 0
        # 每机位只保留当前压缩段，以同一封装供 ffprobe 和解码器核对 PTS。
        path = Path(work) / (camera + ".mp4")
        with path.open("wb") as stream:
            copy_verified(directory / "init.mp4", stream, index["initSha256"])
            copy_verified(base.with_suffix(".m4s"), stream, index["segmentSha256"])
        probe = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=width,height,time_base,codec_name,start_pts:packet=pts", "-of", "json", str(path),
        ]))
        info, = probe["streams"]
        self.width, self.height = info["width"], info["height"]
        # MP4 中一个视频样本对应一个 H.264 访问单元。B 帧按解码顺序读包，
        # 索引按展示顺序存储。只解封装核对 PTS，实际解码仅发生在 grab 中。
        if (info["codec_name"] != "h264"
                or info["time_base"] != f'{self.time_base["numerator"]}/{self.time_base["denominator"]}'
                or sorted(packet["pts"] for packet in probe["packets"]) != [f["pts"] for f in self.frames]):
            raise ValueError(f"机位 {camera} 段 {segment} 的编码、PTS 或时基与索引不符")
        self.start_pts = info["start_pts"]
        self.tick_ms = 1000 * self.time_base["numerator"] / self.time_base["denominator"]
        if not cv2.videoio_registry.hasBackend(cv2.CAP_FFMPEG):
            raise ValueError("录像重建需要启用 FFmpeg 视频后端的 Python OpenCV")
        self.decoder = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        if not self.decoder.isOpened():
            self.decoder.release()
            raise ValueError(f"OpenCV FFmpeg 后端无法解码机位 {camera} 段 {segment}")
        self.decoder.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
        if self.decoder.get(cv2.CAP_PROP_ORIENTATION_AUTO) != 0:
            self.decoder.release()
            raise ValueError("OpenCV FFmpeg 后端未能禁用视频自动旋转")

    def read(self, row):
        while self.offset < len(self.frames):
            indexed = self.frames[self.offset]
            if indexed["pts"] > row["pts"]:
                break
            if not self.decoder.grab():
                raise ValueError("视频解码中断，无法定位指定的曝光帧")
            # FFmpeg 后端报告相对于视频轨道 start_pts 的展示毫秒。核对实际展示时间，
            # 防止某个坏帧被解码器跳过后，后面的像素错绑到前一个曝光。
            actual_ms = self.decoder.get(cv2.CAP_PROP_POS_MSEC)
            expected_ms = (indexed["pts"] - self.start_pts) * self.tick_ms
            if not math.isclose(actual_ms, expected_ms, rel_tol=0., abs_tol=self.tick_ms * .5):
                raise ValueError("解码帧的展示时间与索引不符")
            self.offset += 1
            if indexed["pts"] == row["pts"]:
                if (self.time_base != row["timeBase"] or
                    any(indexed[k] != row[k] for k in ("captureUtcUs", "durationPts"))):
                    raise ValueError("原帧曝光或展示时序与清单不匹配")
                ok, image = self.decoder.retrieve()
                if not ok or image is None or image.shape != (self.height, self.width, 3):
                    raise ValueError("解码帧原始像素尺寸与视频轨道不符")
                return image
        raise ValueError(f'曝光帧在当前媒体段中缺失或倒退：{row["pts"]}')

    def close(self):
        self.decoder.release()


class ReplayImages:
    def __init__(self, root):
        self.root = root
        self.work = tempfile.TemporaryDirectory(prefix="pitchtrace-decode-")
        self.decoders = {}

    def read(self, row):
        camera = row["cameraId"]
        decoder = self.decoders.get(camera)
        key = row.get("mediaEpoch"), row["segment"]
        if decoder is None or decoder.key != key:
            if decoder is not None:
                self.decoders.pop(camera).close()
            decoder = CameraDecoder(self.root, row, self.work.name)
            self.decoders[camera] = decoder
        return decoder.read(row)

    def close(self):
        for decoder in self.decoders.values():
            decoder.close()
        self.work.cleanup()
