"""在预发布成功后同步 main 上的双语下载和离线样例。"""

import json
import re
import subprocess
import sys
from pathlib import Path


READMES = ("README.md", "README.zh-CN.md")
PREVIEW = re.compile(r"^## Edge CLI [^\n]+\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
TAG = re.compile(
    r"edge-v([0-9]+)\.([0-9]+)\.([0-9]+)-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def version_key(tag):
    match = TAG.fullmatch(tag)
    if match is None:
        raise ValueError(f"无法识别 Edge 预发布标签 {tag}")
    major, minor, patch, prerelease = match.groups()
    identifiers = tuple((0, int(part)) if part.isdigit() else (1, part)
                        for part in prerelease.split("."))
    return int(major), int(minor), int(patch), identifiers


def output(*command):
    return subprocess.check_output(command, text=True).strip()


def verify_release(tag):
    release = json.loads(output("gh", "release", "view", tag, "--json", "isDraft,isPrerelease,assets"))
    archive = f"pitchtrace-{tag}-macos-arm64.zip"
    uploaded = {asset["name"] for asset in release["assets"] if asset["state"] == "uploaded"}
    if release["isDraft"] or not release["isPrerelease"] or not {archive, f"{archive}.sha256"} <= uploaded:
        raise ValueError(f"{tag} 尚未公开发布 ZIP 和 SHA-256 校验文件")


def update_readmes(tag, version):
    updates = {}
    for name in READMES:
        path = Path(name)
        content = path.read_bytes().decode("utf-8")
        sections = list(PREVIEW.finditer(content))
        if len(sections) != 1:
            raise ValueError(f"{name} 应包含一处 Edge CLI 快速开始")
        section = sections[0]
        link = re.search(r"/releases/tag/(edge-v[^)\s]+)\)", section[0])
        if link is None:
            raise ValueError(f"{name} 缺少 Edge 预发布链接")
        current = link[1]
        if version_key(current) > version:
            print(f"{name} 已指向较新的 {current}，跳过 {tag}")
            return

        bundle = f"pitchtrace-{current}-macos-arm64"
        download = f"/releases/download/{current}/{bundle}.zip"
        links = (f"{download})", f"{download}.sha256)")
        commands = {
            f"shasum -a 256 -c {bundle}.zip.sha256", f"unzip -q {bundle}.zip", f"cd {bundle}",
        }
        if any(link not in section[0] for link in links) or not commands <= set(section[0].splitlines()):
            raise ValueError(f"{name} 的 Edge 下载链接或快速开始命令与 {current} 不一致")
        old_version = re.escape(current.removeprefix("edge-"))
        updated = re.sub(rf"{old_version}(?=-macos-arm64|[ /)（])", tag.removeprefix("edge-"), section[0])
        updates[path] = content[:section.start()] + updated + content[section.end():]

    # 两份文档都核对通过后再写入。
    for path, content in updates.items():
        path.write_bytes(content.encode("utf-8"))


def main(tag):
    version = version_key(tag)
    verify_release(tag)
    for _ in range(3):
        subprocess.run(["git", "fetch", "--no-tags", "origin", "refs/heads/main"], check=True)
        base = output("git", "rev-parse", "FETCH_HEAD")
        subprocess.run(["git", "switch", "--detach", base], check=True)
        update_readmes(tag, version)

        if not output("git", "diff", "--name-only", "--", *READMES):
            print("中英文 README 无需更新")
            return
        subprocess.run(["git", "diff", "--check", "--", *READMES], check=True)
        subprocess.run([
            "git", "-c", "user.name=github-actions[bot]",
            "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "--only", "-m", f"更新中英文 README 的 Edge {tag.removeprefix('edge-')} 下载与快速开始",
            "--", *READMES,
        ], check=True)
        push = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"])
        if push.returncode == 0:
            return
        # main 有新提交时基于新内容重试，保留其他人的改动。
        remote = output("git", "ls-remote", "origin", "refs/heads/main")
        if not remote or remote.split()[0] == base:
            push.check_returncode()
    raise SystemExit("main 在三次尝试中均出现新提交，README 更新未推送")


if __name__ == "__main__":
    main(sys.argv[1])
