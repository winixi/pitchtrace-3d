"""从固定版本的官方发布包准备 Edge 人球检测模型。"""

import argparse
import hashlib
import io
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile


URL = "https://github.com/ultralytics/yolo-ios-app/releases/download/v8.3.0/yolo11n.mlpackage.zip"
ARCHIVE_SHA256 = "c7f0276ee436fea565a1626aae18865e4f683722248f4de6d39bd55939374649"


def prepare(destination, archive):
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"目标路径已存在，请指定新的目录：{destination}")

    # 固定包约 2.5 MB。先在内存内完成校验，再解压同一份字节。
    with (archive.open("rb") if archive else urlopen(URL, timeout=60)) as stream:
        payload = stream.read(4 * 1024 * 1024)
    if hashlib.sha256(payload).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("模型压缩包的 SHA-256 与官方样例所用版本不符")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pitchtrace-yolo-", dir=destination.parent) as temp:
        staged = Path(temp) / "models"
        with ZipFile(io.BytesIO(payload)) as package:
            package.extractall(staged / "yolo11n.mlpackage")
        shutil.copyfile(Path(__file__).with_name("yolo11n.json"), staged / "yolo11n.json")
        staged.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, metavar="输出目录", help="保存权重和模型清单的新目录")
    parser.add_argument("--archive", type=Path, metavar="本地ZIP", help="复用已下载的官方压缩包")
    args = parser.parse_args()
    try:
        prepare(args.destination, args.archive)
        print(args.destination / "yolo11n.json")
    except (ValueError, OSError, BadZipFile) as error:
        sys.exit(f"准备 YOLO11n 模型失败：{error}")
