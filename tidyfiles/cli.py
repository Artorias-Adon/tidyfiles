# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line interface for TidyFiles."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .categories import CATEGORIES, resolve_category
from .organizer import (
    ConflictPolicy,
    Organizer,
    OrganizerError,
    Operation,
)
from .paths import human_size


def _configure_output() -> None:
    """Keep Chinese output readable on legacy Windows code pages and CI pipes."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        if (getattr(stream, "encoding", "") or "").casefold().replace("-", "") == "utf8":
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tidyfiles",
        description="按文件类型扫描并整理文件夹。默认复制文件，不会改动原件。",
    )
    parser.add_argument("-s", "--source", help="要整理的源文件夹")
    parser.add_argument(
        "-d",
        "--dest",
        help="输出文件夹（默认：源文件夹/整理结果）",
    )
    parser.add_argument(
        "--move",
        action="store_true",
        help="移动文件而不是复制（会删除源文件）",
    )
    parser.add_argument(
        "--flat",
        action="store_true",
        help="不按类型建子文件夹，全部放入输出根目录",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="只整理源文件夹第一层，不扫描子文件夹",
    )
    parser.add_argument(
        "--types",
        metavar="LIST",
        help="只整理指定类型，逗号分隔，如 images,文档,code",
    )
    parser.add_argument(
        "--conflict",
        choices=[policy.value for policy in ConflictPolicy],
        default=ConflictPolicy.RENAME.value,
        help=(
            "重复文件处理：rename=保留两者，skip=略过新文件，"
            "overwrite=覆盖旧文件（默认：rename）"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描并显示计划，不写入任何文件",
    )
    parser.add_argument(
        "--list-types",
        action="store_true",
        help="列出所有内置文件类型后退出",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _parse_types(value: str | None) -> set[str] | None:
    if not value:
        return None
    result: set[str] = set()
    try:
        for part in value.split(","):
            if part.strip():
                result.add(resolve_category(part).key)
    except KeyError as error:
        raise OrganizerError(f"未知的文件类型：{error.args[0]}") from error
    if not result:
        raise OrganizerError("--types 中没有有效的文件类型。")
    return result


def _print_types() -> None:
    print("可用类型：")
    for category in CATEGORIES:
        extensions = ", ".join(sorted(category.extensions)) or "无扩展名"
        print(f"  {category.key:<12} {category.folder:<6} {extensions}")


def _print_plan(plan, scan) -> None:
    print(f"源文件夹  {scan.source}")
    print(f"输出文件夹 {plan.destination}")
    print()
    if not plan.files:
        print("没有找到要整理的文件。")
        return
    counts = plan.counts()
    print(f"{'类型':<8}{'文件数':>8}{'大小':>12}")
    print("-" * 30)
    for category in CATEGORIES:
        count = counts[category.key]
        if not count:
            continue
        size = sum(
            item.entry.size
            for item in plan.files
            if item.entry.category.key == category.key
        )
        print(f"{category.folder:<8}{count:>8}{human_size(size):>12}")
    print("-" * 30)
    print(f"{'合计':<8}{len(plan.files):>8}{human_size(plan.total_size):>12}")
    if scan.issues:
        print(f"\n警告：有 {len(scan.issues)} 个项目无法读取或被跳过。")


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_types:
        _print_types()
        return 0
    if not args.source:
        parser.print_help()
        print("\n错误：请使用 --source 指定源文件夹。", file=sys.stderr)
        return 2

    try:
        selected = _parse_types(args.types)
        organizer = Organizer()
        scan = organizer.scan(args.source, recursive=not args.no_recursive)
        destination = args.dest or str(Path(scan.source) / "整理结果")
        plan = organizer.build_plan(
            scan,
            destination,
            categorized=not args.flat,
            selected_categories=selected,
        )
    except OrganizerError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2

    _print_plan(plan, scan)
    if args.dry_run:
        print("\n[dry-run] 未写入任何文件。")
        return 0
    if not plan.files:
        return 0

    operation = Operation.MOVE if args.move else Operation.COPY
    conflict = ConflictPolicy(args.conflict)
    print(f"\n重复文件：{conflict.label}（{conflict.description}）")
    print(f"正在{operation.label} {len(plan.files)} 个文件…")
    try:
        result = organizer.execute(
            plan,
            operation=operation,
            conflict=conflict,
        )
    except OrganizerError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2

    print(
        f"完成：成功 {result.completed}，略过 {result.skipped}，失败 {result.failed}。"
    )
    if result.report_path:
        print(f"记录：{result.report_path}")
    if result.failed:
        for outcome in result.outcomes:
            if outcome.status == "failed":
                print(f"  失败 {outcome.source}：{outcome.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
