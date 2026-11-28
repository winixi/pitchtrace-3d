"""解析比赛使用的 Core ML 模型，绑定推理参数、权重和来源清单。"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from calibrate import unique_object
from camera_workers import validate_worker_count

# 与 Swift 工作进程的 --compute-units 取值一致。
COMPUTE_UNITS = ("all", "cpuAndNeuralEngine", "cpuAndGPU", "cpuOnly")


def ocr_refresh_us(settings):
    """号码确认后的同轨迹重读间隔；省略时每个入选帧都读号。"""
    value = settings.get("ocrRefreshMs")
    if value is None:
        return None
    if type(value) is not int or not 100 <= value <= 10_000:
        raise ValueError("detector.ocrRefreshMs 需要 100–10000 的整数毫秒")
    if settings.get("backend", "vision") != "vision":
        raise ValueError("detector.ocrRefreshMs 需要 vision 后端")
    return value * 1000


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def model_identity(path):
    if path.is_file():
        return digest(path)
    if not path.is_dir():
        raise ValueError(f"找不到模型：{path}")
    # .mlpackage 和 .mlmodelc 含多个文件，保留相对路径才能区分内部文件替换和改名。
    hashes = {}
    for item in sorted(path.rglob("*")):
        if item.is_dir() and not item.is_symlink():
            continue
        if not item.is_file():
            raise ValueError(f"模型包中存在无法核验的文件或目录链接：{item}")
        hashes[str(item.relative_to(path))] = digest(item)
    if not hashes:
        raise ValueError(f"模型目录为空：{path}")
    return hashes


@dataclass
class VisionModel:
    path: Path
    task: str
    provenance: dict

    def worker_arguments(self):
        args = [str(self.path)]
        if self.task == "person-and-ball":
            args.append("--person-and-ball")
        return args


def required_text(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须为非空字符串")
    return value


def load_vision_model(settings, folder):
    if not isinstance(settings, dict):
        raise ValueError("detector 必须是配置对象")
    validate_worker_count(settings.get("workers", 1))
    if settings.get("ocrMode", "batch") not in ("batch", "individual"):
        raise ValueError("detector.ocrMode 需要 batch 或 individual")
    ocr_refresh_us(settings)
    units = settings.get("computeUnits")
    if units is not None and (units not in COMPUTE_UNITS or settings.get("backend", "vision") != "vision" or
                              not ("model" in settings or "modelManifest" in settings)):
        raise ValueError("detector.computeUnits 需要 vision 后端、Core ML 模型和 " + "、".join(COMPUTE_UNITS) + " 之一")
    ball_search = settings.get("ballSearch", False)
    if type(ball_search) is not bool:
        raise ValueError("detector.ballSearch 必须是布尔值")
    if ball_search and (settings.get("backend", "vision") != "vision" or
                        not ("model" in settings or "modelManifest" in settings)):
        raise ValueError("detector.ballSearch 需要 vision 后端和 Core ML 模型")
    if "modelManifest" not in settings:
        if "model" not in settings or settings.get("backend", "vision") != "vision":
            return None
        path = folder / required_text(settings, "model")
        return VisionModel(path, "ball", {"modelSha256": model_identity(path)})

    if "model" in settings:
        raise ValueError("detector.modelManifest 与 detector.model 只能指定一个")
    if settings.get("backend", "vision") != "vision":
        raise ValueError("detector.modelManifest 仅适用于 vision 后端")
    manifest_path = folder / required_text(settings, "modelManifest")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    if not isinstance(manifest, dict) or manifest.get("format") != "pitchtrace-vision-model-v1":
        raise ValueError("模型清单 format 必须为 pitchtrace-vision-model-v1")
    for key in ("version", "source", "license"):
        required_text(manifest, key)
    task = manifest.get("task")
    if task not in ("person-and-ball", "ball"):
        raise ValueError("模型清单 task 必须为 person-and-ball 或 ball")
    if manifest.get("preprocessing") != "vision-scale-fit":
        raise ValueError("模型清单 preprocessing 必须为 vision-scale-fit")
    path = manifest_path.parent / required_text(manifest, "model")
    return VisionModel(path, task, {
        "modelManifest": manifest, "modelManifestSha256": hashlib.sha256(raw).hexdigest(),
        "modelSha256": model_identity(path),
    })
