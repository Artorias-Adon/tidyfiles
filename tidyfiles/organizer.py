# SPDX-License-Identifier: GPL-3.0-or-later
"""Core scanning, planning and file-moving engine.

The engine is deliberately UI-free: the Tkinter app and the CLI both build on
top of the same tested functions.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from .categories import CATEGORIES, CATEGORY_BY_KEY, Category, category_for
from .paths import reports_dir, resolve_path

_MULTI_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")


class OrganizerError(Exception):
    """A user-facing problem that prevents an operation from starting."""


class Operation(str, Enum):
    COPY = "copy"
    MOVE = "move"

    @property
    def label(self) -> str:
        return "复制" if self is Operation.COPY else "移动"


class ConflictPolicy(str, Enum):
    RENAME = "rename"
    SKIP = "skip"
    OVERWRITE = "overwrite"

    @property
    def label(self) -> str:
        return {
            ConflictPolicy.RENAME: "自动重命名",
            ConflictPolicy.SKIP: "跳过",
            ConflictPolicy.OVERWRITE: "覆盖",
        }[self]


@dataclass(frozen=True, slots=True)
class ScanIssue:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class FileEntry:
    source: Path
    relative: Path
    size: int
    category: Category


@dataclass(frozen=True, slots=True)
class ScanResult:
    source: Path
    files: tuple[FileEntry, ...]
    issues: tuple[ScanIssue, ...] = ()
    cancelled: bool = False

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.files)

    def counts(self) -> dict[str, int]:
        counts = {category.key: 0 for category in CATEGORIES}
        for entry in self.files:
            counts[entry.category.key] += 1
        return counts


@dataclass(frozen=True, slots=True)
class PlannedFile:
    entry: FileEntry
    target_dir: Path


@dataclass(frozen=True, slots=True)
class Plan:
    source: Path
    destination: Path
    categorized: bool
    selected_categories: frozenset[str]
    files: tuple[PlannedFile, ...]

    @property
    def total_size(self) -> int:
        return sum(item.entry.size for item in self.files)

    def counts(self) -> dict[str, int]:
        counts = {category.key: 0 for category in CATEGORIES}
        for item in self.files:
            counts[item.entry.category.key] += 1
        return counts


@dataclass(frozen=True, slots=True)
class FileOutcome:
    source: Path
    target: Path
    category: Category
    size: int
    status: str
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"copied", "moved"}


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    done: int
    total: int
    outcome: FileOutcome


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    operation: Operation
    conflict: ConflictPolicy
    destination: Path
    outcomes: tuple[FileOutcome, ...]
    cancelled: bool = False
    report_path: Path | None = None
    report_error: str | None = None
    started_at: str = ""
    finished_at: str = ""

    @property
    def copied(self) -> int:
        return sum(1 for item in self.outcomes if item.status == "copied")

    @property
    def moved(self) -> int:
        return sum(1 for item in self.outcomes if item.status == "moved")

    @property
    def completed(self) -> int:
        return self.copied + self.moved

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.outcomes if item.status == "skipped")

    @property
    def failed(self) -> int:
        return sum(1 for item in self.outcomes if item.status == "failed")

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.outcomes if item.ok)


class Organizer:
    """Stateless facade around the file operations."""

    def scan(
        self,
        source: str | os.PathLike[str],
        *,
        recursive: bool = True,
        exclude: Iterable[str | os.PathLike[str]] = (),
        cancel_event: threading.Event | None = None,
    ) -> ScanResult:
        """Scan ``source`` and classify every regular file.

        The destination is passed via ``exclude`` so an output folder nested in
        the source tree is never scanned or re-sorted.
        """

        source_path = resolve_path(source)
        if not source_path.exists():
            raise OrganizerError(f"源文件夹不存在：{source_path}")
        if not source_path.is_dir():
            raise OrganizerError(f"源路径不是文件夹：{source_path}")

        excluded = {resolve_path(item) for item in exclude}
        files: list[FileEntry] = []
        issues: list[ScanIssue] = []
        cancelled = False

        def on_walk_error(error: OSError) -> None:
            problem_path = Path(error.filename) if error.filename else source_path
            issues.append(ScanIssue(problem_path, error.strerror or str(error)))

        if recursive:
            iterator = os.walk(source_path, topdown=True, onerror=on_walk_error, followlinks=False)
            for root, dirnames, filenames in iterator:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                root_path = Path(root)
                kept: list[str] = []
                for dirname in sorted(dirnames, key=str.casefold):
                    candidate = root_path / dirname
                    if resolve_path(candidate) in excluded:
                        continue
                    kept.append(dirname)
                dirnames[:] = kept
                for filename in sorted(filenames, key=str.casefold):
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        break
                    entry = _make_entry(root_path / filename, source_path, issues)
                    if entry is not None:
                        files.append(entry)
                if cancelled:
                    break
        else:
            try:
                children = sorted(source_path.iterdir(), key=lambda item: item.name.casefold())
            except OSError as error:
                raise OrganizerError(f"无法读取源文件夹：{error}") from error
            for child in children:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                if child.is_dir():
                    continue
                entry = _make_entry(child, source_path, issues)
                if entry is not None:
                    files.append(entry)

        files.sort(key=lambda item: str(item.relative).casefold())
        return ScanResult(source_path, tuple(files), tuple(issues), cancelled)

    def build_plan(
        self,
        scan: ScanResult,
        destination: str | os.PathLike[str],
        *,
        categorized: bool = True,
        selected_categories: Iterable[str] | None = None,
    ) -> Plan:
        """Turn a scan into a deterministic plan without touching the disk."""

        if not str(destination).strip():
            raise OrganizerError("请选择输出文件夹。")
        destination_path = resolve_path(destination)
        if destination_path == scan.source:
            raise OrganizerError("输出文件夹不能与源文件夹相同，请选择其他位置。")

        if selected_categories is None:
            selected = {category.key for category in CATEGORIES}
        else:
            selected = set(selected_categories)
            unknown = selected - set(CATEGORY_BY_KEY)
            if unknown:
                raise OrganizerError(f"未知的文件类型：{', '.join(sorted(unknown))}")

        planned: list[PlannedFile] = []
        for entry in scan.files:
            if categorized:
                if entry.category.key not in selected:
                    continue
                target_dir = destination_path / entry.category.folder
            else:
                target_dir = destination_path
            planned.append(PlannedFile(entry, target_dir))

        return Plan(
            source=scan.source,
            destination=destination_path,
            categorized=categorized,
            selected_categories=frozenset(selected),
            files=tuple(planned),
        )

    def execute(
        self,
        plan: Plan,
        *,
        operation: Operation | str = Operation.COPY,
        conflict: ConflictPolicy | str = ConflictPolicy.RENAME,
        progress: Callable[[ProgressEvent], None] | None = None,
        cancel_event: threading.Event | None = None,
        save_report: bool = True,
        reports_directory: str | os.PathLike[str] | None = None,
    ) -> ExecutionResult:
        """Execute a plan, collecting per-file failures instead of aborting."""

        try:
            operation = Operation(operation)
        except ValueError as error:
            raise OrganizerError(f"未知的操作方式：{operation}") from error
        try:
            conflict = ConflictPolicy(conflict)
        except ValueError as error:
            raise OrganizerError(f"未知的重名处理方式：{conflict}") from error

        if plan.destination == plan.source:
            raise OrganizerError("输出文件夹不能与源文件夹相同。")
        if plan.destination.exists() and not plan.destination.is_dir():
            raise OrganizerError(f"输出路径已被文件占用：{plan.destination}")

        started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        outcomes: list[FileOutcome] = []
        reserved: set[str] = set()
        cancelled = False

        try:
            plan.destination.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise OrganizerError(f"无法创建输出文件夹：{error}") from error

        total = len(plan.files)
        for index, item in enumerate(plan.files):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break

            source = item.entry.source
            category = item.entry.category
            fallback_target = item.target_dir / source.name
            try:
                item.target_dir.mkdir(parents=True, exist_ok=True)
                if not source.is_file():
                    outcome = FileOutcome(
                        source,
                        fallback_target,
                        category,
                        item.entry.size,
                        "failed",
                        "源文件已不存在或已被修改",
                    )
                    outcomes.append(outcome)
                    self._notify(progress, index + 1, total, outcome)
                    continue

                target, resolution = _choose_target(
                    fallback_target, conflict=conflict, reserved=reserved
                )
                if resolution == "skip":
                    outcome = FileOutcome(
                        source,
                        target,
                        category,
                        item.entry.size,
                        "skipped",
                        "输出位置已存在同名文件，按设置跳过",
                    )
                    outcomes.append(outcome)
                    self._notify(progress, index + 1, total, outcome)
                    continue
                if resolution == "overwrite" and target.is_dir():
                    raise IsADirectoryError(f"同名文件夹已存在：{target.name}")

                message = ""
                if target != fallback_target:
                    message = "目标已存在，已自动重命名"
                elif resolution == "overwrite":
                    message = "已覆盖同名文件"

                if operation is Operation.COPY:
                    _atomic_copy(source, target)
                    status = "copied"
                else:
                    if resolution == "overwrite" and target.exists():
                        target.unlink()
                    shutil.move(str(source), str(target))
                    status = "moved"

                reserved.add(_path_key(target))
                outcome = FileOutcome(
                    source,
                    target,
                    category,
                    item.entry.size,
                    status,
                    message,
                )
            except (OSError, shutil.Error) as error:
                outcome = FileOutcome(
                    source,
                    fallback_target,
                    category,
                    item.entry.size,
                    "failed",
                    _friendly_error(error),
                )
            outcomes.append(outcome)
            self._notify(progress, index + 1, total, outcome)

        finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        result = ExecutionResult(
            operation=operation,
            conflict=conflict,
            destination=plan.destination,
            outcomes=tuple(outcomes),
            cancelled=cancelled,
            started_at=started_at,
            finished_at=finished_at,
        )

        if save_report:
            report_path, report_error = self._write_report(
                plan,
                result,
                reports_directory=reports_directory,
            )
            result = ExecutionResult(
                operation=result.operation,
                conflict=result.conflict,
                destination=result.destination,
                outcomes=result.outcomes,
                cancelled=result.cancelled,
                report_path=report_path,
                report_error=report_error,
                started_at=result.started_at,
                finished_at=result.finished_at,
            )
        return result

    @staticmethod
    def _notify(
        callback: Callable[[ProgressEvent], None] | None,
        done: int,
        total: int,
        outcome: FileOutcome,
    ) -> None:
        if callback is None:
            return
        try:
            callback(ProgressEvent(done, total, outcome))
        except Exception:
            # A display callback must never be able to corrupt a file operation.
            pass

    @staticmethod
    def _write_report(
        plan: Plan,
        result: ExecutionResult,
        *,
        reports_directory: str | os.PathLike[str] | None,
    ) -> tuple[Path | None, str | None]:
        directory = Path(reports_directory) if reports_directory else reports_dir()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        report_path = directory / f"tidyfiles-report-{stamp}-{uuid.uuid4().hex[:4]}.json"
        payload = {
            "app": "TidyFiles",
            "report_version": 1,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "source": str(plan.source),
            "destination": str(plan.destination),
            "operation": result.operation.value,
            "conflict_policy": result.conflict.value,
            "categorized": plan.categorized,
            "selected_categories": sorted(plan.selected_categories),
            "cancelled": result.cancelled,
            "summary": {
                "planned": len(plan.files),
                "completed": result.completed,
                "skipped": result.skipped,
                "failed": result.failed,
                "bytes": result.total_size,
            },
            "files": [
                {
                    "source": str(item.source),
                    "target": str(item.target),
                    "category": item.category.key,
                    "size": item.size,
                    "status": item.status,
                    "message": item.message,
                }
                for item in result.outcomes
            ],
        }
        try:
            directory.mkdir(parents=True, exist_ok=True)
            temporary = report_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, report_path)
            return report_path, None
        except OSError as error:
            return None, str(error)


def _make_entry(path: Path, source: Path, issues: list[ScanIssue]) -> FileEntry | None:
    try:
        if path.is_symlink():
            issues.append(ScanIssue(path, "已跳过符号链接"))
            return None
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            return None
        relative = path.relative_to(source)
        return FileEntry(path, relative, file_stat.st_size, category_for(path))
    except OSError as error:
        issues.append(ScanIssue(path, _friendly_error(error)))
        return None


def _choose_target(
    target: Path,
    *,
    conflict: ConflictPolicy,
    reserved: set[str],
) -> tuple[Path, str]:
    """Return ``(target, resolution)`` where resolution is normal/skip/overwrite."""

    key = _path_key(target)
    exists = target.exists()
    if not exists and key not in reserved:
        return target, "normal"
    if conflict is ConflictPolicy.SKIP:
        return target, "skip"
    if conflict is ConflictPolicy.OVERWRITE and key not in reserved and not target.is_dir():
        return target, "overwrite"

    stem, suffix = _split_name(target.name)
    for number in range(1, 100_000):
        candidate = target.with_name(f"{stem} ({number}){suffix}")
        if not candidate.exists() and _path_key(candidate) not in reserved:
            return candidate, "renamed"
    raise OSError(f"无法为文件生成可用的新名称：{target.name}")


def _split_name(name: str) -> tuple[str, str]:
    lowered = name.casefold()
    for suffix in _MULTI_SUFFIXES:
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], name[-len(suffix) :]
    path = Path(name)
    return path.stem, path.suffix


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _atomic_copy(source: Path, target: Path) -> None:
    """Copy beside the target then atomically swap it into place."""

    partial = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        shutil.copy2(source, partial)
        os.replace(partial, target)
    finally:
        try:
            if partial.exists():
                partial.unlink()
        except OSError:
            pass


def _friendly_error(error: BaseException) -> str:
    if isinstance(error, PermissionError):
        return "权限不足，无法访问文件"
    if isinstance(error, FileNotFoundError):
        return "文件或文件夹不存在"
    if isinstance(error, IsADirectoryError):
        return str(error)
    text = str(error).strip()
    return text or error.__class__.__name__


__all__ = [
    "ConflictPolicy",
    "ExecutionResult",
    "FileEntry",
    "FileOutcome",
    "Operation",
    "Organizer",
    "OrganizerError",
    "Plan",
    "PlannedFile",
    "ProgressEvent",
    "ScanIssue",
    "ScanResult",
]
