#!/bin/bash
set -euo pipefail

if [[ $# != 2 || ! $1 =~ ^edge-v[0-9][0-9A-Za-z.+-]*$ ]]; then
    echo "用法：$0 <edge-v 版本标签> <输出目录>" >&2
    exit 2
fi

edge_dir=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$2"
output=$(cd "$2" && pwd)
package="pitchtrace-$1-macos-arm64"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# 独立的构建目录便于在测试发布包前移走开发机上的资源副本。
scratch="$work/build"
build_options=(--package-path "$edge_dir" --scratch-path "$scratch" -c release --force-resolved-versions)
swift build "${build_options[@]}" --product pitchtrace-edge
bin_dir=$(swift build "${build_options[@]}" --show-bin-path)
cli="$bin_dir/pitchtrace-edge"
if [[ $(lipo -archs "$cli") != arm64 ]]; then
    echo "发布包需要 macOS arm64 可执行文件" >&2
    exit 1
fi

bundle="$work/$package"
mkdir -p "$bundle/samples" "$bundle/licenses"
cp "$cli" "$bundle/"
cp -R "$bin_dir/PitchTraceEdge_PitchTraceEdge.bundle" "$bundle/"
cp "$edge_dir/preview.md" "$bundle/README.md"
cp "$edge_dir/../LICENSE" "$bundle/"
cp "$scratch/checkouts/swifter/LICENSE" "$bundle/licenses/Swifter-LICENSE.txt"
cp "$edge_dir/samples/generate-match.py" "$bundle/samples/"
mv "$scratch" "$work/build-before-packaging"

cd "$work"
zip -qrX "$package.zip" "$package"
shasum -a 256 "$package.zip" > "$package.zip.sha256"
shasum -a 256 -c "$package.zip.sha256"
unzip -q "$package.zip" -d extracted

cd "extracted/$package"
./pitchtrace-edge --help
trial="$work/trial"
python3 -B samples/generate-match.py "$trial/input"
./pitchtrace-edge run-match "$trial/input/match.json" "$trial/run"
./pitchtrace-edge scene-evidence "$trial/run/scenes.sqlite" training video 5 > "$trial/evidence.json"
./pitchtrace-edge export-scenes "$trial/run/scenes.sqlite" training video > "$trial/scenes.ndjson"

python3 - "$trial" <<'PY'
import json
import sys
from pathlib import Path

trial = Path(sys.argv[1])
media = trial / "run" / "media"
assert len((media / "frames.ndjson").read_text().splitlines()) == 40, "预期两路合计 40 个录像帧"
assert (media / "gaps.txt").read_text() == "", "预期录像缺口为零"
assert len((trial / "scenes.ndjson").read_text().splitlines()) == 20, "预期归档 20 个场景快照"
frames = json.loads((trial / "evidence.json").read_text())["frames"]
assert {frame["cameraId"] for frame in frames} == {"A", "B"}, "场景 5 应能定位两路原片"
PY

cp "$work/$package.zip" "$work/$package.zip.sha256" "$output/"
