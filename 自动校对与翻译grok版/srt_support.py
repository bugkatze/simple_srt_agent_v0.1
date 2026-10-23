"""
LoopAgent Support (纯净版)

职责：
1. 管理 agent_idle/ 工作空间目录结构
2. 管理 file_database/ 及其版本快照与归档

已移除：
- CodeRunner: 不再动态执行大模型生成的 Python 代码，执行逻辑已移交 Core 的 _run_local_script。
- SafetyChecker: 大模型现只输出 JSON 数据，不存在执行危险系统调用的风险。
"""

import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional


# ============================================================
# RuntimeWorkspace
# ============================================================

class RuntimeWorkspace:
    """
    管理 agent 运行时的基础目录结构。

    Directory structure:
        agent_idle/
        └── file_database/
            ├── current/
            └── data_history/
    """

    def __init__(self, root: Optional[str | Path] = None):
        if root is None:
            root_path = Path(__file__).resolve().parent / "agent_idle"
        else:
            root_path = Path(root)
            if not root_path.is_absolute():
                root_path = Path(__file__).resolve().parent / root_path

        self.root = root_path.resolve()
        self.file_database = self.root / "file_database"
        self.current = self.file_database / "current"
        self.history = self.file_database / "data_history"

    def ensure(self) -> None:
        """确保运行时目录结构存在"""
        self.root.mkdir(parents=True, exist_ok=True)
        self.file_database.mkdir(parents=True, exist_ok=True)
        self.current.mkdir(parents=True, exist_ok=True)
        self.history.mkdir(parents=True, exist_ok=True)

    def path_in_root(self, path: str) -> Path:
        """确保路径不会逃逸出 workspace 根目录"""
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate

        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise ValueError(f"路径逃逸了运行时工作区: {candidate}")

        return candidate


# ============================================================
# FileDatabase
# ============================================================

class FileDatabase:
    """
    跟踪 current/ 中的文件变化，并负责将其快照打包归档至 data_history/
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.current = self.root / "current"
        self.history = self.root / "data_history"

        self.current.mkdir(parents=True, exist_ok=True)
        self.history.mkdir(parents=True, exist_ok=True)

        self._snapshot = self._scan()

    @staticmethod
    def _file_signature(path: Path):
        """
        生成稳定的文件签名 (size, mtime_ns, sha256)
        """
        stat = path.stat()
        sha256 = hashlib.sha256()

        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha256.update(chunk)

        return (
            stat.st_size,
            stat.st_mtime_ns,
            sha256.hexdigest(),
        )

    def _scan(self) -> Dict[str, tuple]:
        """扫描 current/ 返回当前所有文件的签名"""
        snapshot = {}
        if not self.current.exists():
            return snapshot

        for path in self.current.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.current).as_posix()
            snapshot[rel] = self._file_signature(path)

        return snapshot

    def detect_changes(self) -> List[Path]:
        """
        检测 current/ 中新增或修改的文件
        """
        latest = self._scan()
        changed = []

        for rel_path, signature in latest.items():
            if self._snapshot.get(rel_path) != signature:
                changed.append(self.current / rel_path)

        self._snapshot = latest
        return sorted(changed)

    def get_all_files(self) -> List[Path]:
        """返回 current/ 中的所有文件列表"""
        if not self.current.exists():
            return []
        return sorted(path for path in self.current.rglob("*") if path.is_file())

    def archive_current(self) -> List[Path]:
        """
        将 current/ 中的所有文件移动到 data_history/ 下以时间戳命名的新目录中
        """
        files = self.get_all_files()

        if not files:
            self._snapshot = self._scan()
            return []

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        archive_dir = self.history / timestamp

        counter = 1
        while archive_dir.exists():
            archive_dir = self.history / f"{timestamp}_{counter}"
            counter += 1

        archive_dir.mkdir(parents=True, exist_ok=True)
        archived = []

        for source in files:
            relative = source.relative_to(self.current)
            destination = archive_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)

            shutil.move(str(source), str(destination))
            archived.append(destination)

        # 顺手清理 current/ 里遗留的空文件夹
        for directory in sorted(
            (p for p in self.current.rglob("*") if p.is_dir()),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass

        self._snapshot = self._scan()
        return archived