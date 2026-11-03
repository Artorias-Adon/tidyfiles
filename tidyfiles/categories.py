# SPDX-License-Identifier: GPL-3.0-or-later
"""Built-in file categories and extension matching.

The category ``key`` is stable API (used by the CLI and settings), while
``folder`` is the human-facing folder name created on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Category:
    """A selectable group of file extensions."""

    key: str
    folder: str
    description: str
    color: str
    extensions: frozenset[str]

    def matches(self, path: str | Path) -> bool:
        return extension_of(path) in self.extensions


CATEGORIES: tuple[Category, ...] = (
    Category(
        "images",
        "图片",
        "照片、截图与图形",
        "#F59E0B",
        frozenset(
            {
                "jpg", "jpeg", "jpe", "jfif", "png", "gif", "bmp", "webp",
                "svg", "ico", "tif", "tiff", "heic", "heif", "avif", "raw",
                "cr2", "cr3", "nef", "arw", "dng", "orf", "rw2",
            }
        ),
    ),
    Category(
        "videos",
        "视频",
        "影片、录屏与动画",
        "#EF4444",
        frozenset(
            {
                "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v",
                "mpg", "mpeg", "m2ts", "mts", "ts", "3gp", "ogv", "rmvb",
            }
        ),
    ),
    Category(
        "audio",
        "音频",
        "音乐、录音与音效",
        "#8B5CF6",
        frozenset(
            {
                "mp3", "wav", "flac", "aac", "ogg", "oga", "m4a", "wma",
                "opus", "aiff", "aif", "mid", "midi", "amr", "ape",
            }
        ),
    ),
    Category(
        "documents",
        "文档",
        "文字、演示与电子书",
        "#3B82F6",
        frozenset(
            {
                "pdf", "doc", "docx", "docm", "dot", "dotx", "ppt", "pptx",
                "pptm", "pot", "potx", "txt", "md", "markdown", "rtf", "odt",
                "ott", "pages", "numbers", "key", "epub", "mobi", "azw",
                "azw3", "fb2", "djvu", "tex",
            }
        ),
    ),
    Category(
        "data",
        "表格数据",
        "表格、数据与配置文件",
        "#10B981",
        frozenset(
            {
                "xls", "xlsx", "xlsm", "xlsb", "ods", "csv", "tsv", "json",
                "jsonl", "ndjson", "xml", "yaml", "yml", "toml", "ini",
                "cfg", "conf", "parquet", "feather", "arrow", "db", "sqlite",
                "sqlite3", "mdb", "accdb", "sav", "dta", "rdata", "rds",
            }
        ),
    ),
    Category(
        "archives",
        "压缩包",
        "压缩、打包与光盘映像",
        "#F97316",
        frozenset(
            {
                "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst", "tgz",
                "tbz2", "txz", "iso", "cab", "lz", "lzma", "zipx",
            }
        ),
    ),
    Category(
        "code",
        "代码",
        "源码、脚本与网页文件",
        "#6366F1",
        frozenset(
            {
                "py", "pyw", "ipynb", "js", "jsx", "ts", "tsx", "mjs",
                "cjs", "vue", "svelte", "html", "htm", "css", "scss",
                "sass", "less", "java", "c", "cc", "cpp", "cxx", "h",
                "hpp", "hxx", "cs", "go", "rs", "rb", "php", "swift", "kt",
                "kts", "scala", "sh", "bash", "zsh", "fish", "ps1", "psm1",
                "bat", "cmd", "sql", "lua", "r", "m", "mm", "pl", "pm",
                "dart", "ex", "exs", "erl", "clj", "cljs", "hs", "fs",
                "fsx", "vb", "asm", "s",
            }
        ),
    ),
    Category(
        "fonts",
        "字体",
        "系统字体与网页字体",
        "#EC4899",
        frozenset({"ttf", "otf", "woff", "woff2", "eot", "fon", "ttc"}),
    ),
    Category(
        "installers",
        "安装包",
        "应用安装程序与系统包",
        "#64748B",
        frozenset(
            {
                "exe", "msi", "msix", "appx", "apk", "aab", "dmg", "pkg",
                "deb", "rpm", "appimage", "snap", "flatpak",
            }
        ),
    ),
    Category(
        "design",
        "设计素材",
        "设计稿、3D 与 CAD 文件",
        "#14B8A6",
        frozenset(
            {
                "psd", "ai", "eps", "indd", "sketch", "fig", "xd",
                "afdesign", "afphoto", "blend", "stl", "obj", "fbx", "3ds",
                "dae", "step", "stp", "iges", "igs", "dwg", "dxf", "max",
                "ma", "mb", "c4d",
            }
        ),
    ),
    Category(
        "other",
        "其他",
        "未匹配到以上类型的文件",
        "#94A3B8",
        frozenset(),
    ),
)

CATEGORY_BY_KEY: dict[str, Category] = {category.key: category for category in CATEGORIES}
CATEGORY_BY_FOLDER: dict[str, Category] = {category.folder: category for category in CATEGORIES}
OTHER_CATEGORY = CATEGORY_BY_KEY["other"]

_EXTENSION_INDEX: dict[str, Category] = {
    extension: category
    for category in CATEGORIES
    for extension in category.extensions
}

# A few common extension-less source files worth recognising.
_FILENAME_INDEX: dict[str, Category] = {
    "dockerfile": CATEGORY_BY_KEY["code"],
    "makefile": CATEGORY_BY_KEY["code"],
    "cmakelists.txt": CATEGORY_BY_KEY["code"],
    "gemfile": CATEGORY_BY_KEY["code"],
    "procfile": CATEGORY_BY_KEY["code"],
}


def extension_of(path: str | Path) -> str:
    """Return a lowercase extension without the leading dot."""

    name = Path(path).name
    if "." not in name or name.endswith("."):
        return ""
    return name.rsplit(".", 1)[1].casefold()


def category_for(path: str | Path) -> Category:
    """Return the category a file belongs to (never ``None``)."""

    name = Path(path).name.casefold()
    if name in _FILENAME_INDEX:
        return _FILENAME_INDEX[name]
    return _EXTENSION_INDEX.get(extension_of(path), OTHER_CATEGORY)


def resolve_category(value: str) -> Category:
    """Resolve a category from its key, folder name or display name.

    Raises:
        KeyError: if the value does not identify a known category.
    """

    text = value.strip().casefold()
    for category in CATEGORIES:
        if text in {category.key.casefold(), category.folder.casefold()}:
            return category
    raise KeyError(value)


def selected_categories(values: list[str] | tuple[str, ...] | set[str] | None) -> set[str]:
    """Normalise a collection of category keys, rejecting unknown entries."""

    if values is None:
        return set(CATEGORY_BY_KEY)
    result: set[str] = set()
    for value in values:
        result.add(resolve_category(value).key)
    return result
