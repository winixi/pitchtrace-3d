"""按本场成功运行记录清理到期的媒体导出副本与场景库。"""

import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile

from calibrate import is_identifier, unique_object

ID = r"[A-Za-z0-9_-]{1,64}"
MEDIA = re.compile(rf"media/(?:frames\.ndjson|gaps\.txt|{ID}/(?:epochs/{ID}/)?(?:init\.mp4|segments/[0-9]+\.(?:json|m4s)))")


def read_object(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"需要普通记录文件：{path}")
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError(f"记录内容无效：{path}")
    return value


def identity(state):
    if (state.get("format") != "pitchtrace-run-v1" or not is_identifier(state.get("matchId"))
            or not is_identifier(state.get("streamEpoch")) or not isinstance(state.get("inputSha256"), str)):
        raise ValueError("找不到有效的比赛运行身份")
    return {k: state[k] for k in ("matchId", "streamEpoch", "inputSha256")}


def stamp(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"生成物已被替换：{path}")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def record_owned_outputs(root):
    """由 run-match 在持有独占锁且验证生成物摘要后调用。"""
    root = Path(root)
    state = read_object(root / "run-state.json")
    manifest = root / "run-owned.json"
    if manifest.exists():
        if read_object(manifest).get("run") != identity(state):
            raise ValueError("生成物归属与本场运行身份不一致")
        return
    files = {}
    media = root / "media"
    if media.is_symlink() or not media.is_dir():
        raise ValueError("缺少本场普通媒体目录")
    for folder, _, names in os.walk(media, followlinks=False):
        for name in names:
            path = Path(folder) / name
            relative = path.relative_to(root).as_posix()
            if MEDIA.fullmatch(relative) and not path.is_symlink():
                files[relative] = stamp(path)
    files["scenes.sqlite"] = stamp(root / "scenes.sqlite")
    data = {"run": identity(state), "files": files}
    with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False, encoding="utf-8") as output:
        try:
            json.dump(data, output, separators=(",", ":"), sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
            os.replace(output.name, manifest)
        finally:
            Path(output.name).unlink(missing_ok=True)


def db_owned(path, state):
    from run_match import archive_signature

    # 回执可以变化，核对完整库的场次归属与所有表，避开额外用户数据。
    names = {"edge_runs", "edge_scenes", "edge_scene_receipts", "edge_scene_destinations", "edge_scene_pending"}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != names:
            return False
        run_key = (state["matchId"], state["streamEpoch"])
        if db.execute("SELECT match_id,epoch FROM edge_runs").fetchall() != [run_key]:
            return False
        for table in sorted(names):
            if db.execute(f"SELECT 1 FROM {table} WHERE match_id<>? OR epoch<>? LIMIT 1", run_key).fetchone():
                return False
    return archive_signature(path, *run_key) == state.get("sceneSha256")


def open_lock(stack, path, *, create=False):
    flags = os.O_RDWR | os.O_NOFOLLOW
    fd = os.open(path, flags | (os.O_CREAT if create else 0), 0o600)
    lock = stack.enter_context(os.fdopen(fd, "rb+"))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise ValueError(f"运行锁不是普通文件：{path}")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise ValueError("比赛运行目录或场景数据库正在使用") from error


def log(path, entry):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as audit:
        audit.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        audit.flush()
        os.fsync(audit.fileno())


def unlink_owned(root, name, original):
    # 把遍历固定在已打开的目录上，重新核对最后一个文件，避免跟随临时换入的链接。
    with ExitStack() as stack:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, directory)
        parts = Path(name).parts
        for part in parts[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            stack.callback(os.close, directory)
        stat_info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        snapshot = [stat_info.st_dev, stat_info.st_ino, stat_info.st_size, stat_info.st_mtime_ns]
        matched = snapshot[:2] == original[:2] if name == "scenes.sqlite" else snapshot == original
        if not stat.S_ISREG(stat_info.st_mode) or not matched:
            raise ValueError(f"清理时文件已发生变化：{name}")
        os.unlink(parts[-1], dir_fd=directory)


def retention(root, apply=False, now=None):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("请使用 run-match 创建的普通运行目录")
    root = root.resolve()
    now = now or datetime.now(timezone.utc)
    with ExitStack() as stack:
        open_lock(stack, root / "run.lock")
        state = read_object(root / "run-state.json")
        run = identity(state)
        report = {**run, "mode": "apply" if apply else "preview", "planned": [], "removed": [], "retained": []}
        days = state.get("retentionDays")
        if days is None:
            return {**report, "status": "noPolicy"}
        if type(days) is not int or days < 1:
            raise ValueError("retentionDays 须为正整数")
        start = datetime.fromisoformat(state["createdAtUtc"])
        if start.tzinfo is None:
            raise ValueError("运行记录缺少 UTC 时区")
        expiry = start + timedelta(days=days)
        report["expiresAtUtc"] = expiry.isoformat()
        if now < expiry:
            return {**report, "status": "notDue"}
        if state.get("status") != "completed":
            return {**report, "status": "incomplete"}
        ownership = read_object(root / "run-owned.json")
        if ownership.get("run") != run or not isinstance(ownership.get("files"), dict):
            raise ValueError("生成物归属与本场运行记录不符")
        open_lock(stack, root / "scenes-usage.lock", create=True)
        paths = {}
        for name, original in ownership["files"].items():
            if not (name == "scenes.sqlite" or MEDIA.fullmatch(name)):
                raise ValueError(f"清单含未经管理的路径：{name}")
            path = root / name
            if any(p.is_symlink() for p in path.parents if root in p.parents) or path.is_symlink():
                report["retained"].append(name)
                continue
            if not path.exists():
                continue
            if not path.is_file():
                report["retained"].append(name)
                continue
            current = stamp(path)
            if name == "scenes.sqlite":
                matches = current[:2] == original[:2] and db_owned(path, state)
            else:
                matches = current == original
            if matches:
                paths[name] = path
            else:
                report["retained"].append(name)
        report["planned"] = sorted(paths)
        report["status"] = "due"
        if apply and paths:
            audit = root / "run-retention.jsonl"
            # 先持久化清理清单，中断后仍可对照归属逐文件继续。
            log(audit, {"at": now.isoformat(), "stage": "planned", **report})
            for name, path in sorted(paths.items()):
                if name == "scenes.sqlite":
                    # 清空 WAL 并要求 SQLite 切换至独占日志模式，已有读者时保留原库。
                    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0)) as db:
                        mode = db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                    if mode != "delete" or any(Path(str(path) + s).exists() for s in ("-shm", "-wal", "-journal")):
                        raise ValueError("场景数据库仍有未合并的日志")
                unlink_owned(root, name, ownership["files"][name])
                report["removed"].append(name)
            report["status"] = "cleaned"
            log(audit, {"at": datetime.now(timezone.utc).isoformat(), "stage": "finished", **report})
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="比赛运行目录留存检查")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(retention(args.directory, args.apply), ensure_ascii=False))
    except (KeyError, TypeError, ValueError, OverflowError, OSError, sqlite3.Error) as error:
        sys.exit(f"比赛留存检查失败：{error}")
