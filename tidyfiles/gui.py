# SPDX-License-Identifier: GPL-3.0-or-later
"""Tkinter desktop interface for TidyFiles."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .categories import CATEGORIES
from .organizer import (
    ConflictPolicy,
    ExecutionResult,
    Operation,
    Organizer,
    OrganizerError,
    Plan,
    ProgressEvent,
    ScanResult,
)
from .paths import human_size, middle_ellipsis, resolve_path, tail_ellipsis
from .settings import load_settings, save_settings
from .widgets import (
    ACCENT,
    ACCENT_SOFT,
    BG,
    BORDER,
    CARD,
    DANGER,
    FAINT,
    MUTED,
    NAVY,
    NAVY_SOFT,
    SUCCESS,
    TEXT,
    WARNING,
    CategoryChip,
    RoundedButton,
    StepBadge,
    SwitchToggle,
    choose_fonts,
    rounded_rect,
)

REPO_URL = "https://github.com/Artorias-Adon/tidyfiles"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"

CONFLICT_LABELS: dict[str, str] = {
    ConflictPolicy.RENAME.value: "自动重命名",
    ConflictPolicy.SKIP.value: "跳过",
    ConflictPolicy.OVERWRITE.value: "覆盖",
}
CONFLICT_BY_LABEL = {label: key for key, label in CONFLICT_LABELS.items()}


class TidyFilesApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.fonts = choose_fonts(root)
        self.settings = load_settings()
        self.organizer = Organizer()

        self.source_var = tk.StringVar(value=self.settings["source"])
        self.destination_var = tk.StringVar(value=self.settings["destination"])
        self.recursive_var = tk.BooleanVar(value=self.settings["recursive"])
        self.categorize_var = tk.BooleanVar(value=self.settings["categorized"])
        self.operation_var = tk.StringVar(value=self.settings["operation"])
        self.conflict_var = tk.StringVar(value=self.settings["conflict"])
        self.conflict_label_var = tk.StringVar(
            value=CONFLICT_LABELS[self.settings["conflict"]]
        )
        selected = set(self.settings["selected_categories"])
        self.category_vars = {
            category.key: tk.BooleanVar(value=category.key in selected)
            for category in CATEGORIES
        }

        self.scan_result: ScanResult | None = None
        self.scan_token = 0
        self.scanning = False
        self._scan_started = 0.0
        self._last_scanned_source = ""
        self._category_sizes: dict[str, int] = {}
        self._preview_row_count = 0

        self.busy = False
        self.cancel_event: threading.Event | None = None
        self.worker_queue: queue.Queue = queue.Queue()
        self._last_output: Path | None = None
        self._last_report: Path | None = None
        self._closing = False

        self._configure_root()
        self._configure_styles()
        self._build_ui()
        self._restore_geometry()
        self._sync_operation_buttons()
        self._toggle_categorize()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-o>", lambda _event: self._browse_source())
        self.root.bind("<F5>", lambda _event: self._start_scan(force=True))
        self.root.bind("<Control-Return>", lambda _event: self._start_run())
        self.root.bind("<Escape>", self._on_escape)
        self.root.after(80, self._drain_queue)

        if self.source_var.get().strip():
            self.root.after(350, lambda: self._start_scan(force=True))
        else:
            self._set_status(
                "idle",
                "准备就绪",
                "选择源文件夹后，会立即扫描并生成预览",
            )

    # ------------------------------------------------------------------ setup

    def _configure_root(self) -> None:
        self.root.configure(bg=BG)
        self.root.title("TidyFiles · 文件整理助手")
        self.root.minsize(1000, 820)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = max(1000, min(1120, screen_w - 64))
        height = 820
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 12)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self._set_window_icon()

    def _set_window_icon(self) -> None:
        icon_png = ASSETS_DIR / "icon_64.png"
        icon_ico = ASSETS_DIR / "icon.ico"
        try:
            if icon_ico.exists():
                self.root.iconbitmap(default=str(icon_ico))
        except tk.TclError:
            pass
        try:
            if icon_png.exists():
                self._window_icon = tk.PhotoImage(file=str(icon_png))
                self.root.iconphoto(True, self._window_icon)
        except tk.TclError:
            pass

    def _restore_geometry(self) -> None:
        saved = self.settings.get("geometry", "")
        match = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", saved)
        if not match:
            return
        width, height, x, y = (int(value) for value in match.groups())
        if width < 1000 or height < 820:
            return
        if x < -width + 100 or y < -height + 100:
            return
        if x > self.root.winfo_screenwidth() - 100:
            return
        self.root.geometry(saved)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        ui = self.fonts["ui"][0]
        style.configure(
            "Tidy.TCombobox",
            fieldbackground=CARD,
            background=CARD,
            foreground=TEXT,
            arrowcolor=MUTED,
            bordercolor=BORDER,
            lightcolor=CARD,
            darkcolor=CARD,
            padding=(7, 4),
            font=(ui, 9),
        )
        style.map(
            "Tidy.TCombobox",
            fieldbackground=[("readonly", CARD)],
            selectbackground=[("readonly", CARD)],
            selectforeground=[("readonly", TEXT)],
            bordercolor=[("focus", ACCENT)],
        )
        style.configure(
            "Tidy.Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            borderwidth=0,
            relief="flat",
            rowheight=30,
            font=(ui, 9),
        )
        style.map(
            "Tidy.Treeview",
            background=[("selected", ACCENT_SOFT)],
            foreground=[("selected", TEXT)],
        )
        style.configure(
            "Tidy.Treeview.Heading",
            background="#F8FAFD",
            foreground=MUTED,
            relief="flat",
            borderwidth=0,
            padding=(3, 7),
            font=(ui, 8, "bold"),
        )
        style.map("Tidy.Treeview.Heading", background=[("active", "#F1F4FA")])
        style.layout(
            "Tidy.Treeview",
            [("Treeview.treearea", {"sticky": "nswe"})],
        )
        style.configure(
            "Tidy.Horizontal.TProgressbar",
            troughcolor="#E9EEF7",
            background=ACCENT,
            bordercolor="#E9EEF7",
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=4,
        )
        style.configure(
            "Tidy.Vertical.TScrollbar",
            background="#D9E1EE",
            troughcolor=CARD,
            bordercolor=CARD,
            lightcolor="#D9E1EE",
            darkcolor="#D9E1EE",
            arrowcolor=MUTED,
            width=8,
            borderwidth=0,
        )
        style.layout(
            "Tidy.Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [
                            (
                                "Vertical.Scrollbar.thumb",
                                {"expand": "1", "sticky": "nswe"},
                            )
                        ],
                    },
                )
            ],
        )

    # ------------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        self._build_header()
        # Pack the footer before the expanding body: Tk's packer allocates space
        # in call order, and an expanding body would otherwise hide the footer.
        self._build_footer()
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=16)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=3, minsize=600)
        body.grid_columnconfigure(1, weight=2, minsize=340)

        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")

        self._build_path_card(left)
        self._build_method_card(left)
        self._build_category_card(left)
        self._build_preview_card(right)

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=NAVY, height=84)
        header.pack(fill="x")
        header.pack_propagate(False)
        inner = tk.Frame(header, bg=NAVY)
        inner.pack(fill="both", expand=True, padx=26, pady=15)

        logo = self._load_logo(inner)
        if logo is not None:
            logo.pack(side="left", padx=(0, 13))
        else:
            fallback = tk.Canvas(
                inner, width=44, height=44, bg=NAVY, highlightthickness=0, bd=0
            )
            fallback.pack(side="left", padx=(0, 13))
            rounded_rect(fallback, 1, 1, 43, 43, 11, fill=ACCENT, outline=ACCENT)
            fallback.create_line(
                12, 29, 20, 21, 26, 27, 33, 16,
                fill="#FFFFFF", width=2.4, capstyle="round", joinstyle="round",
            )

        title_block = tk.Frame(inner, bg=NAVY)
        title_block.pack(side="left")
        tk.Label(
            title_block,
            text="TidyFiles",
            bg=NAVY,
            fg="#FFFFFF",
            font=self.fonts["title"],
        ).pack(anchor="w")
        tk.Label(
            title_block,
            text="按类型归档 · 先预览，再动手",
            bg=NAVY,
            fg="#9FB0D0",
            font=self.fonts["subtitle"],
        ).pack(anchor="w", pady=(1, 0))

        actions = tk.Frame(inner, bg=NAVY)
        actions.pack(side="right")
        github = tk.Label(
            actions,
            text="GitHub  ↗",
            bg=NAVY,
            fg="#C4D2EE",
            font=self.fonts["small"],
            cursor="hand2",
        )
        github.pack(side="right", padx=(14, 2))
        github.bind("<Button-1>", lambda _event: webbrowser.open(REPO_URL))
        github.bind("<Enter>", lambda _event: github.configure(fg="#FFFFFF"))
        github.bind("<Leave>", lambda _event: github.configure(fg="#C4D2EE"))

        version_pill = tk.Frame(actions, bg=NAVY_SOFT, padx=10, pady=5)
        version_pill.pack(side="right")
        tk.Label(
            version_pill,
            text=f"v{__version__} · GPLv3+",
            bg=NAVY_SOFT,
            fg="#AFC0DE",
            font=self.fonts["tiny"],
        ).pack()

    def _load_logo(self, parent: tk.Misc) -> tk.Label | None:
        for name in ("logo_48.png", "icon_64.png"):
            path = ASSETS_DIR / name
            if not path.exists():
                continue
            try:
                image = tk.PhotoImage(file=str(path))
            except tk.TclError:
                continue
            if image.width() > 48:
                factor = max(1, image.width() // 48)
                image = image.subsample(factor, factor)
            self._header_logo = image
            return tk.Label(parent, image=image, bg=NAVY, bd=0)
        return None

    def _card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )

    def _section_header(
        self,
        parent: tk.Misc,
        number: int,
        title: str,
    ) -> tk.Frame:
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x")
        StepBadge(row, number).pack(side="left")
        tk.Label(
            row,
            text=title,
            bg=CARD,
            fg=TEXT,
            font=self.fonts["section"],
        ).pack(side="left", padx=(8, 0))
        return row

    def _link_button(
        self,
        parent: tk.Misc,
        text: str,
        command,
        *,
        font: tuple | None = None,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=CARD,
            fg=ACCENT,
            activebackground=ACCENT_SOFT,
            activeforeground=ACCENT,
            disabledforeground="#B7C0D0",
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=font or self.fonts["small"],
            cursor="hand2",
            padx=5,
            pady=2,
        )

    def _build_path_card(self, parent: tk.Misc) -> None:
        card = self._card(parent)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=16, pady=10)

        header = self._section_header(inner, 1, "选择位置")
        self.rescan_btn = self._link_button(
            header, "↻  重新扫描", lambda: self._start_scan(force=True)
        )
        self.rescan_btn.pack(side="right")

        self.source_entry = self._path_row(
            inner,
            "源文件夹",
            self.source_var,
            self._browse_source,
            required=True,
        )
        self.source_entry.bind(
            "<Return>", lambda _event: self._start_scan(force=True)
        )

        self.destination_entry = self._path_row(
            inner,
            "输出文件夹",
            self.destination_var,
            self._browse_destination,
            required=False,
        )
        self.destination_entry.bind(
            "<KeyRelease>", lambda _event: self._destination_changed()
        )
        self.destination_entry.bind(
            "<Return>", lambda _event: self._destination_changed()
        )

        self.destination_hint = tk.Label(
            inner,
            text="留空时输出到「源文件夹 / 整理结果」",
            bg=CARD,
            fg=FAINT,
            font=self.fonts["tiny"],
            anchor="w",
        )
        self.destination_hint.pack(fill="x", padx=2, pady=(2, 0))

        options = tk.Frame(inner, bg=CARD)
        options.pack(fill="x", padx=2, pady=(5, 0))
        self.recursive_check = self._flat_checkbox(
            options,
            "包含子文件夹",
            self.recursive_var,
            self._on_recursive_changed,
        )
        self.recursive_check.pack(side="left")
        tk.Label(
            options,
            text="关闭后只整理第一层文件",
            bg=CARD,
            fg=FAINT,
            font=self.fonts["tiny"],
        ).pack(side="left", padx=(10, 0))

    def _path_row(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.StringVar,
        browse_command,
        *,
        required: bool,
    ) -> tk.Entry:
        top = tk.Frame(parent, bg=CARD)
        top.pack(fill="x", padx=2, pady=(6, 0))
        tk.Label(
            top,
            text=label,
            bg=CARD,
            fg=TEXT,
            font=self.fonts["label"],
        ).pack(side="left")
        tk.Label(
            top,
            text="必选" if required else "可选",
            bg=CARD,
            fg=ACCENT if required else FAINT,
            font=self.fonts["tiny"],
        ).pack(side="left", padx=(6, 0))

        shell = tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
            height=36,
        )
        shell.pack(fill="x", padx=2, pady=(3, 0))
        shell.pack_propagate(False)
        entry = tk.Entry(
            shell,
            textvariable=variable,
            bg=CARD,
            fg=TEXT,
            insertbackground=ACCENT,
            selectbackground=ACCENT_SOFT,
            selectforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=self.fonts["ui"],
        )
        entry.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=4)
        button = RoundedButton(
            shell,
            "浏览…",
            browse_command,
            font=self.fonts["button_small"],
            width=64,
            height=26,
            kind="secondary",
            radius=7,
        )
        button.pack(side="right", padx=5, pady=5)
        if required:
            self.source_browse = button
            placeholder = "点击「浏览…」或输入文件夹路径"
        else:
            self.destination_browse = button
            placeholder = "留空则使用「源文件夹 / 整理结果」"
        self._attach_placeholder(shell, entry, variable, placeholder)
        entry.bind("<FocusIn>", lambda _event: shell.configure(highlightbackground=ACCENT))
        entry.bind("<FocusOut>", lambda _event: shell.configure(highlightbackground=BORDER))
        return entry

    def _attach_placeholder(
        self,
        shell: tk.Frame,
        entry: tk.Entry,
        variable: tk.StringVar,
        text: str,
    ) -> None:
        label = tk.Label(
            shell,
            text=text,
            bg=CARD,
            fg="#A6B1C2",
            font=self.fonts["small"],
            cursor="xterm",
        )

        def sync(*_args) -> None:
            has_text = bool(variable.get().strip())
            focused = self.root.focus_get() is entry
            if has_text or focused:
                label.place_forget()
            else:
                label.place(x=11, rely=0.5, anchor="w")
                label.lift()

        label.bind("<Button-1>", lambda _event: entry.focus_set())
        entry.bind("<FocusIn>", lambda _event: sync(), add="+")
        entry.bind("<FocusOut>", lambda _event: sync(), add="+")
        variable.trace_add("write", sync)
        sync()

    def _flat_checkbox(
        self,
        parent: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        command,
    ) -> tk.Checkbutton:
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=command,
            bg=CARD,
            fg=MUTED,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor=CARD,
            font=self.fonts["small"],
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            padx=0,
        )

    def _build_method_card(self, parent: tk.Misc) -> None:
        card = self._card(parent)
        card.pack(fill="x", pady=(10, 0))
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=16, pady=9)
        header = self._section_header(inner, 2, "整理方式")
        self.categorize_toggle = SwitchToggle(
            header,
            "按类型分文件夹",
            self.categorize_var,
            self._toggle_categorize,
            font=self.fonts["small"],
            width=164,
            height=26,
        )
        self.categorize_toggle.pack(side="right")

        controls = tk.Frame(inner, bg=CARD)
        controls.pack(fill="x", pady=(9, 0))
        tk.Label(
            controls, text="操作", bg=CARD, fg=MUTED, font=self.fonts["small"]
        ).pack(side="left", padx=(4, 6))
        self.copy_btn = RoundedButton(
            controls,
            "复制 · 保留原件",
            lambda: self._set_operation(Operation.COPY.value),
            font=self.fonts["button_small"],
            width=116,
            height=31,
            kind="primary",
            radius=7,
        )
        self.copy_btn.pack(side="left")
        self.move_btn = RoundedButton(
            controls,
            "移动",
            lambda: self._set_operation(Operation.MOVE.value),
            font=self.fonts["button_small"],
            width=58,
            height=31,
            kind="secondary",
            radius=7,
        )
        self.move_btn.pack(side="left", padx=(5, 0))

        tk.Label(
            controls,
            text="重名文件",
            bg=CARD,
            fg=MUTED,
            font=self.fonts["small"],
        ).pack(side="left", padx=(18, 6))
        self.conflict_combo = ttk.Combobox(
            controls,
            textvariable=self.conflict_label_var,
            values=list(CONFLICT_LABELS.values()),
            state="readonly",
            width=10,
            style="Tidy.TCombobox",
            font=self.fonts["small"],
        )
        self.conflict_combo.pack(side="left")
        self.conflict_combo.bind("<<ComboboxSelected>>", self._on_conflict_changed)

    def _build_category_card(self, parent: tk.Misc) -> None:
        card = self._card(parent)
        card.pack(fill="both", expand=True, pady=(10, 0))
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=16, pady=9)

        header = self._section_header(inner, 3, "文件类型")
        self.clear_types_btn = self._link_button(
            header, "清空", lambda: self._set_all_categories(False)
        )
        self.clear_types_btn.pack(side="right")
        self.all_types_btn = self._link_button(
            header, "全选", lambda: self._set_all_categories(True)
        )
        self.all_types_btn.pack(side="right", padx=(0, 2))

        self.category_hint = tk.Label(
            inner,
            text="勾选要提取的类型；未勾选的类型会被忽略",
            bg=CARD,
            fg=FAINT,
            font=self.fonts["tiny"],
            anchor="w",
        )
        self.category_hint.pack(fill="x", padx=(2, 0), pady=(2, 5))

        note = tk.Frame(
            inner,
            bg="#F8FAFE",
            highlightbackground="#EDF1F8",
            highlightthickness=1,
        )
        note.pack(side="bottom", fill="x", pady=(8, 0))
        tk.Label(
            note,
            text="小提示",
            bg="#F8FAFE",
            fg=ACCENT,
            font=self.fonts["tiny"],
        ).pack(side="left", padx=(9, 7), pady=5)
        tk.Label(
            note,
            text="建议先用「复制」保留原件；整理记录可随时查看。",
            bg="#F8FAFE",
            fg=MUTED,
            font=self.fonts["tiny"],
        ).pack(side="left", pady=5)

        chips = tk.Frame(inner, bg=CARD)
        chips.pack(fill="both", expand=True)
        for column in range(3):
            chips.grid_columnconfigure(column, weight=1, uniform="chips")
        self.chips: dict[str, CategoryChip] = {}
        for index, category in enumerate(CATEGORIES):
            row, column = divmod(index, 3)
            chip = CategoryChip(
                chips,
                category,
                self.category_vars[category.key],
                self._on_category_changed,
                font=self.fonts["small"],
                count_font=self.fonts["tiny"],
                height=38,
            )
            chip.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 5, 0),
                pady=(0 if row == 0 else 5, 0),
            )
            self.chips[category.key] = chip

    def _build_preview_card(self, parent: tk.Misc) -> None:
        card = self._card(parent)
        card.pack(fill="both", expand=True)
        self.preview_card = card
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=16, pady=12)

        header = tk.Frame(inner, bg=CARD)
        header.pack(fill="x")
        tk.Label(
            header,
            text="整理预览",
            bg=CARD,
            fg=TEXT,
            font=self.fonts["section"],
        ).pack(side="left")
        live = tk.Frame(header, bg="#F3FBF7", padx=7, pady=2)
        live.pack(side="right")
        tk.Label(
            live,
            text="●  实时统计",
            bg="#F3FBF7",
            fg=SUCCESS,
            font=self.fonts["tiny"],
        ).pack()

        stats = tk.Frame(
            inner,
            bg="#FAFBFF",
            highlightbackground="#EDF1F8",
            highlightthickness=1,
        )
        stats.pack(fill="x", pady=(9, 10))
        stats.grid_columnconfigure(0, weight=2)
        stats.grid_columnconfigure(2, weight=1)
        stats.grid_columnconfigure(4, weight=1)

        left_stat = tk.Frame(stats, bg="#FAFBFF")
        left_stat.grid(row=0, column=0, sticky="nsew", padx=(12, 4), pady=7)
        self.total_label = tk.Label(
            left_stat,
            text="0",
            bg="#FAFBFF",
            fg=TEXT,
            font=self.fonts["stat"],
            anchor="w",
        )
        self.total_label.pack(side="left")
        self.total_caption = tk.Label(
            left_stat,
            text="个文件待整理",
            bg="#FAFBFF",
            fg=MUTED,
            font=self.fonts["tiny"],
        )
        self.total_caption.pack(side="left", padx=(5, 0), pady=(9, 0))

        self._stat_separator(stats, 1)
        size_stat = tk.Frame(stats, bg="#FAFBFF")
        size_stat.grid(row=0, column=2, sticky="nsew", padx=(8, 3), pady=7)
        tk.Label(
            size_stat, text="总大小", bg="#FAFBFF", fg=FAINT, font=self.fonts["tiny"]
        ).pack(anchor="w")
        self.size_stat = tk.Label(
            size_stat,
            text="0 B",
            bg="#FAFBFF",
            fg=TEXT,
            font=self.fonts["stat_small"],
            anchor="w",
        )
        self.size_stat.pack(anchor="w")

        self._stat_separator(stats, 3)
        type_stat = tk.Frame(stats, bg="#FAFBFF")
        type_stat.grid(row=0, column=4, sticky="nsew", padx=(8, 4), pady=7)
        tk.Label(
            type_stat, text="文件类型", bg="#FAFBFF", fg=FAINT, font=self.fonts["tiny"]
        ).pack(anchor="w")
        self.type_stat = tk.Label(
            type_stat,
            text="0 种",
            bg="#FAFBFF",
            fg=TEXT,
            font=self.fonts["stat_small"],
            anchor="w",
        )
        self.type_stat.pack(anchor="w")
        stats.grid_rowconfigure(0, weight=1)

        table = tk.Frame(inner, bg=CARD)
        table.pack(fill="both", expand=True)
        columns = ("category", "count", "size")
        self.tree = ttk.Treeview(
            table,
            columns=columns,
            show="headings",
            style="Tidy.Treeview",
            selectmode="none",
            height=8,
        )
        self.tree.heading("category", text="文件类型", anchor="w")
        self.tree.heading("count", text="数量", anchor="center")
        self.tree.heading("size", text="大小", anchor="e")
        self.tree.column("category", width=132, minwidth=90, anchor="w", stretch=True)
        self.tree.column("count", width=54, minwidth=44, anchor="center", stretch=False)
        self.tree.column("size", width=70, minwidth=58, anchor="e", stretch=False)
        self.tree.tag_configure("even", background="#FBFCFE")
        self.tree.tag_configure("odd", background=CARD)
        self.tree.bind("<Configure>", self._on_tree_resize)
        self.scrollbar = ttk.Scrollbar(
            table,
            orient="vertical",
            command=self.tree.yview,
            style="Tidy.Vertical.TScrollbar",
        )
        self.tree.configure(yscrollcommand=self.scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)

        self.empty = tk.Frame(table, bg=CARD)
        self.empty.place(relx=0.5, rely=0.5, anchor="center")
        icon = tk.Canvas(
            self.empty, width=56, height=48, bg=CARD, highlightthickness=0
        )
        icon.pack()
        icon.create_polygon(
            7, 16, 7, 10, 22, 10, 27, 16, 49, 16, 49, 39, 7, 39,
            fill=ACCENT_SOFT, outline="#C9D4FF", width=1,
        )
        icon.create_line(7, 21, 49, 21, fill="#C9D4FF")
        icon.create_line(23, 30, 33, 30, fill=ACCENT, width=2, capstyle="round")
        icon.create_line(28, 25, 28, 35, fill=ACCENT, width=2, capstyle="round")
        self.empty_title = tk.Label(
            self.empty,
            text="还没有预览",
            bg=CARD,
            fg=TEXT,
            font=self.fonts["body_bold"],
        )
        self.empty_title.pack(pady=(5, 1))
        self.empty_hint = tk.Label(
            self.empty,
            text="选择源文件夹后自动扫描",
            bg=CARD,
            fg=FAINT,
            font=self.fonts["tiny"],
        )
        self.empty_hint.pack()
        self.empty.lift()

        rule = tk.Frame(inner, bg="#EEF1F7", height=1)
        rule.pack(fill="x", pady=(9, 0))
        output_row = tk.Frame(inner, bg=CARD)
        output_row.pack(fill="x", pady=(7, 0))
        tk.Label(
            output_row,
            text="输出",
            bg=CARD,
            fg=FAINT,
            font=self.fonts["tiny"],
        ).pack(side="left", anchor="n", padx=(2, 8), pady=(1, 0))
        self.output_label = tk.Label(
            output_row,
            text="—",
            bg=CARD,
            fg=MUTED,
            font=self.fonts["tiny"],
            anchor="w",
            justify="left",
            wraplength=280,
        )
        self.output_label.pack(side="left", fill="x", expand=True)
        self.preview_card.bind("<Configure>", self._resize_output_label)
        self._refresh_preview()

    def _stat_separator(self, parent: tk.Misc, column: int) -> None:
        line = tk.Frame(parent, bg="#E8EDF6", width=1, height=34)
        line.grid(row=0, column=column, sticky="ns", pady=10)

    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=CARD, height=80)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x")
        self.progress = ttk.Progressbar(
            footer,
            mode="determinate",
            style="Tidy.Horizontal.TProgressbar",
            maximum=100,
            value=0,
        )
        self.progress.pack(fill="x")
        content = tk.Frame(footer, bg=CARD)
        content.pack(fill="both", expand=True, padx=24, pady=10)

        status_block = tk.Frame(content, bg=CARD)
        status_block.pack(side="left", fill="both", expand=True)
        status_line = tk.Frame(status_block, bg=CARD)
        status_line.pack(anchor="w", pady=(3, 0))
        self.status_dot = tk.Canvas(
            status_line, width=10, height=10, bg=CARD, highlightthickness=0
        )
        self.status_dot.pack(side="left", padx=(0, 7))
        self._status_dot = self.status_dot.create_oval(
            1, 1, 9, 9, fill=FAINT, outline=""
        )
        self.status_label = tk.Label(
            status_line,
            text="准备就绪",
            bg=CARD,
            fg=TEXT,
            font=self.fonts["body_bold"],
        )
        self.status_label.pack(side="left")
        self.detail_label = tk.Label(
            status_block,
            text="选择源文件夹后，会立即扫描并生成预览",
            bg=CARD,
            fg=MUTED,
            font=self.fonts["tiny"],
            anchor="w",
        )
        self.detail_label.pack(anchor="w", padx=(17, 0), pady=(1, 0))

        actions = tk.Frame(content, bg=CARD)
        actions.pack(side="right", padx=(12, 0), pady=3)
        self.start_btn = RoundedButton(
            actions,
            "开始整理  →",
            self._start_run,
            font=self.fonts["button"],
            width=148,
            height=40,
            kind="primary",
            radius=10,
        )
        self.start_btn.pack(side="right")
        self.start_btn.set_enabled(False)

        self.cancel_btn = RoundedButton(
            actions,
            "取消",
            self._cancel_run,
            font=self.fonts["button_small"],
            width=76,
            height=38,
            kind="secondary",
            radius=9,
        )
        self.report_btn = RoundedButton(
            actions,
            "查看记录",
            self._open_report,
            font=self.fonts["button_small"],
            width=88,
            height=38,
            kind="secondary",
            radius=9,
        )
        self.open_btn = RoundedButton(
            actions,
            "打开输出文件夹",
            self._open_output,
            font=self.fonts["button_small"],
            width=124,
            height=38,
            kind="secondary",
            radius=9,
        )

    # ------------------------------------------------------------- callbacks

    def _browse_source(self) -> None:
        if self.busy:
            return
        current = self.source_var.get().strip().strip('"')
        initial = current if current and Path(current).is_dir() else str(Path.home())
        selected = filedialog.askdirectory(
            parent=self.root,
            title="选择要整理的源文件夹",
            initialdir=initial,
            mustexist=True,
        )
        if not selected:
            return
        self.source_var.set(str(Path(selected)))
        self._start_scan(force=True)

    def _browse_destination(self) -> None:
        if self.busy:
            return
        source = self.source_var.get().strip().strip('"')
        current = self.destination_var.get().strip().strip('"')
        if current:
            initial = current
        elif source and Path(source).is_dir():
            initial = source
        else:
            initial = str(Path.home())
        selected = filedialog.askdirectory(
            parent=self.root,
            title="选择输出文件夹",
            initialdir=initial,
            mustexist=False,
        )
        if not selected:
            return
        self.destination_var.set(str(Path(selected)))
        self._destination_changed()

    def _destination_changed(self) -> None:
        self._refresh_preview()
        self._update_start_state()

    def _on_recursive_changed(self) -> None:
        if self.busy:
            return
        if self.scan_result is not None:
            self._start_scan(force=True)

    def _set_operation(self, value: str) -> None:
        if self.busy:
            return
        self.operation_var.set(value)
        self._sync_operation_buttons()

    def _sync_operation_buttons(self) -> None:
        copying = self.operation_var.get() == Operation.COPY.value
        self.copy_btn.set_kind("primary" if copying else "secondary")
        self.move_btn.set_kind("secondary" if copying else "primary")

    def _on_conflict_changed(self, _event=None) -> None:
        label = self.conflict_label_var.get()
        self.conflict_var.set(CONFLICT_BY_LABEL.get(label, ConflictPolicy.RENAME.value))

    def _toggle_categorize(self) -> None:
        if self.busy:
            self.categorize_var.set(not self.categorize_var.get())
            self.categorize_toggle.refresh()
            return
        enabled = self.categorize_var.get()
        counts = self.scan_result.counts() if self.scan_result else {}
        for key, chip in self.chips.items():
            chip.set_enabled(enabled and counts.get(key, 0) > 0)
        state = "normal" if enabled else "disabled"
        self.all_types_btn.configure(state=state)
        self.clear_types_btn.configure(state=state)
        if enabled:
            self.category_hint.configure(
                text="勾选要提取的类型；未勾选的类型会被忽略"
            )
        else:
            self.category_hint.configure(
                text="分类已关闭，所有文件会直接放入输出文件夹"
            )
        self._refresh_preview()
        self._update_start_state()

    def _on_category_changed(self) -> None:
        self._refresh_preview()
        self._update_start_state()

    def _set_all_categories(self, value: bool) -> None:
        if self.busy or not self.categorize_var.get():
            return
        counts = self.scan_result.counts() if self.scan_result else {}
        for key, variable in self.category_vars.items():
            if counts.get(key, 0) > 0:
                variable.set(value)
        for chip in self.chips.values():
            chip.refresh()
        self._on_category_changed()

    # --------------------------------------------------------------- scanning

    def _source_path(self) -> Path | None:
        text = self.source_var.get().strip().strip('"')
        if not text:
            return None
        return resolve_path(text)

    def _destination_path(self) -> Path | None:
        source = self._source_path()
        text = self.destination_var.get().strip().strip('"')
        if text:
            return resolve_path(text)
        if source is not None:
            return source / "整理结果"
        return None

    def _start_scan(self, *, force: bool = False, silent: bool = False) -> None:
        if self.busy:
            return
        source = self._source_path()
        if source is None:
            self._set_status("warning", "还没有选择源文件夹", "点击「浏览…」选择一个文件夹")
            return
        if not source.exists() or not source.is_dir():
            self.scan_result = None
            self._last_scanned_source = ""
            self._category_sizes = {}
            for chip in self.chips.values():
                chip.set_count(0)
                chip.set_enabled(False)
            self._refresh_preview()
            self._update_start_state()
            self._set_status("error", "源文件夹不可用", f"请检查路径：{source}")
            return
        if (
            not force
            and self.scan_result is not None
            and self._last_scanned_source == str(source)
            and not silent
        ):
            return

        self.scan_token += 1
        token = self.scan_token
        self.scanning = True
        self.scan_result = None
        self._last_scanned_source = ""
        self._category_sizes = {}
        self._scan_started = time.perf_counter()
        self.scan_token = token
        self._scanning_ui()
        destination = self._destination_path()
        exclude = []
        if destination is not None and destination != source:
            exclude.append(destination)
        recursive = self.recursive_var.get()

        def work() -> None:
            try:
                result = self.organizer.scan(
                    source,
                    recursive=recursive,
                    exclude=exclude,
                )
            except OrganizerError as error:
                self.worker_queue.put(("scan_error", token, str(error), silent))
            except Exception as error:  # defensive: keep the UI alive
                self.worker_queue.put(
                    ("scan_error", token, f"意外错误：{error}", silent)
                )
            else:
                self.worker_queue.put(("scan_done", token, result, silent))

        threading.Thread(target=work, daemon=True, name="tidyfiles-scan").start()

    def _scanning_ui(self) -> None:
        self.start_btn.set_enabled(False)
        self.rescan_btn.configure(state="disabled")
        self.progress.stop()
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        self._set_status("busy", "正在扫描文件夹…", "文件较多时可能需要几秒钟")
        self.empty_title.configure(text="正在扫描…")
        self.empty_hint.configure(text="正在读取文件类型与大小")
        self.empty.place(relx=0.5, rely=0.5, anchor="center")
        self.empty.lift()
        for chip in self.chips.values():
            chip.set_count(0)
            chip.set_enabled(False)
        self.total_label.configure(text="0")
        self.total_caption.configure(text="个文件待整理")
        self.size_stat.configure(text="0 B")
        self.type_stat.configure(text="0 种")

    def _on_scan_done(self, token: int, result: ScanResult, silent: bool) -> None:
        if token != self.scan_token:
            return
        self.scanning = False
        self._finish_progress()
        self.rescan_btn.configure(state="normal")
        self.scan_result = result
        self._last_scanned_source = str(result.source)
        self._category_sizes = {}
        for entry in result.files:
            self._category_sizes[entry.category.key] = (
                self._category_sizes.get(entry.category.key, 0) + entry.size
            )
        counts = result.counts()
        for key, chip in self.chips.items():
            chip.set_count(counts[key])
            chip.set_enabled(self.categorize_var.get() and counts[key] > 0)
        self._update_destination_hint()
        self._refresh_preview()
        self._update_start_state()

        elapsed = max(0.0, time.perf_counter() - self._scan_started)
        if silent and not self.busy:
            # Keep the completion message from a just-finished operation.
            return
        if not result.files:
            self._set_status(
                "warning",
                "没有找到可整理的文件",
                middle_ellipsis(str(result.source), 78),
            )
            return
        detail = (
            f"{len(result.files)} 个文件 · {human_size(result.total_size)} · "
            f"{elapsed:.1f} 秒"
        )
        if result.issues:
            detail += f" · {len(result.issues)} 个项目被跳过"
        self._set_status("success", "扫描完成", detail)

    def _on_scan_error(self, token: int, message: str, silent: bool) -> None:
        if token != self.scan_token:
            return
        self.scanning = False
        self._finish_progress()
        self.rescan_btn.configure(state="normal")
        self.scan_result = None
        self._refresh_preview()
        self._update_start_state()
        if not silent:
            self._set_status("error", "无法扫描文件夹", message)

    def _finish_progress(self) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)

    def _update_destination_hint(self) -> None:
        destination = self._destination_path()
        if destination is None:
            text = "留空时输出到「源文件夹 / 整理结果」"
        else:
            text = "输出文件夹不存在时会自动创建"
        self.destination_hint.configure(text=text)

    # ---------------------------------------------------------------- preview

    def _refresh_preview(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._update_destination_hint()
        destination = self._destination_path()
        self.output_label.configure(
            text=tail_ellipsis(str(destination), 68) if destination else "—"
        )

        if self.scan_result is None:
            self.total_label.configure(text="0")
            self.total_caption.configure(text="个文件待整理")
            self.size_stat.configure(text="0 B")
            self.type_stat.configure(text="0 种")
            self._preview_row_count = 0
            self._set_scrollbar_visible(False)
            self._show_empty("还没有预览", "选择源文件夹后自动扫描")
            self._update_start_state()
            return

        files = self.scan_result.files
        if self.categorize_var.get():
            rows = []
            for category in CATEGORIES:
                count = sum(
                    1 for entry in files if entry.category.key == category.key
                )
                if not count or not self.category_vars[category.key].get():
                    continue
                rows.append(
                    (
                        category.folder,
                        count,
                        self._category_sizes.get(category.key, 0),
                    )
                )
            total_count = sum(row[1] for row in rows)
            total_size = sum(row[2] for row in rows)
        else:
            total_count = len(files)
            total_size = self.scan_result.total_size
            rows = [("全部文件", total_count, total_size)] if files else []

        distinct_types = len({entry.category.key for entry in files})
        self.total_label.configure(text=f"{total_count:,}")
        self.total_caption.configure(text="个文件待整理")
        self.size_stat.configure(text=human_size(total_size))
        self.type_stat.configure(text=f"{distinct_types} 种")

        for index, (name, count, size) in enumerate(rows):
            tag = "even" if index % 2 == 0 else "odd"
            self.tree.insert(
                "",
                "end",
                values=(name, f"{count:,}", human_size(size)),
                tags=(tag,),
            )
        self._preview_row_count = len(rows)
        self.root.after_idle(self._update_scrollbar_visibility)

        if total_count == 0:
            if files:
                if self.categorize_var.get():
                    self._show_empty(
                        "尚未选择文件类型",
                        "在左侧勾选至少一种类型，或打开「全选」",
                    )
                else:
                    self._show_empty("没有可整理的文件", "换一个源文件夹试试")
            else:
                self._show_empty(
                    "没有找到可整理的文件",
                    "这个文件夹里没有普通文件，或文件都无法读取",
                )
        else:
            self.empty.place_forget()
        self._update_start_state()

    def _show_empty(self, title: str, hint: str) -> None:
        self.empty_title.configure(text=title)
        self.empty_hint.configure(text=hint)
        self.empty.place(relx=0.5, rely=0.5, anchor="center")
        self.empty.lift()

    def _set_scrollbar_visible(self, visible: bool) -> None:
        if visible:
            if not self.scrollbar.winfo_ismapped():
                self.scrollbar.pack(
                    side="right", fill="y", padx=(4, 0), before=self.tree
                )
        else:
            self.scrollbar.pack_forget()

    def _on_tree_resize(self, event) -> None:
        self._update_scrollbar_visibility(event.height)

    def _update_scrollbar_visibility(self, height: int | None = None) -> None:
        if not hasattr(self, "scrollbar"):
            return
        available = int(height if height is not None else self.tree.winfo_height())
        if available <= 1:
            return
        capacity = max(1, (available - 34) // 30)
        self._set_scrollbar_visible(self._preview_row_count > capacity)

    def _resize_output_label(self, event) -> None:
        self.output_label.configure(wraplength=max(180, event.width - 88))

    def _update_start_state(self) -> None:
        if not hasattr(self, "start_btn"):
            return
        if self.busy or self.scanning or self.scan_result is None:
            self.start_btn.set_enabled(False)
            return
        if self.categorize_var.get():
            ready = any(
                self.category_vars[key].get() and count > 0
                for key, count in self.scan_result.counts().items()
            )
        else:
            ready = bool(self.scan_result.files)
        self.start_btn.set_enabled(ready)

    # ------------------------------------------------------------------- run

    def _selected_categories(self) -> set[str]:
        return {key for key, variable in self.category_vars.items() if variable.get()}

    def _start_run(self) -> None:
        if self.busy or self.scanning:
            return
        source = self._source_path()
        if source is None or not source.is_dir():
            messagebox.showerror(
                "无法开始", "请先选择一个有效的源文件夹。", parent=self.root
            )
            return
        if self.scan_result is None or self._last_scanned_source != str(source):
            self._set_status(
                "warning", "源文件夹有变化", "正在重新扫描，请确认预览后再次开始"
            )
            self._start_scan(force=True)
            return
        destination = self._destination_path()
        if destination is None:
            messagebox.showerror(
                "无法开始", "无法确定输出文件夹。", parent=self.root
            )
            return
        if destination == source:
            messagebox.showerror(
                "无法开始",
                "输出文件夹不能与源文件夹相同，请选择其他位置。",
                parent=self.root,
            )
            return

        try:
            plan = self.organizer.build_plan(
                self.scan_result,
                destination,
                categorized=self.categorize_var.get(),
                selected_categories=(
                    self._selected_categories() if self.categorize_var.get() else None
                ),
            )
        except OrganizerError as error:
            messagebox.showerror("无法开始", str(error), parent=self.root)
            return

        if not plan.files:
            messagebox.showinfo(
                "没有可整理的文件",
                "当前筛选条件下没有文件。请勾选更多类型或更换源文件夹。",
                parent=self.root,
            )
            return

        operation = Operation(self.operation_var.get())
        conflict = ConflictPolicy(self.conflict_var.get())
        mode = "复制" if operation is Operation.COPY else "移动"
        note = ""
        if operation is Operation.MOVE:
            note = "\n\n注意：移动会把源文件从原位置移除，建议先确认预览。"
        message = (
            f"将{mode} {len(plan.files):,} 个文件"
            f"（{human_size(plan.total_size)}）到：\n"
            f"{plan.destination}\n\n"
            f"分类：{'按文件类型创建子文件夹' if plan.categorized else '全部放入同一层'}\n"
            f"重名文件：{conflict.label}{note}"
        )
        if not messagebox.askokcancel(
            "确认开始整理",
            message,
            parent=self.root,
            icon="warning" if operation is Operation.MOVE else "question",
            default="cancel",
        ):
            return

        self._begin_run(plan, operation, conflict)

    def _begin_run(
        self,
        plan: Plan,
        operation: Operation,
        conflict: ConflictPolicy,
    ) -> None:
        self.busy = True
        self.cancel_event = threading.Event()
        self._last_report = None
        self._last_output = plan.destination
        self.start_btn.set_enabled(False)
        self.start_btn.set_text("正在整理…")
        self.cancel_btn.set_text("取消")
        self.cancel_btn.set_enabled(True)
        self.cancel_btn.pack(side="right", padx=(0, 8))
        self.open_btn.pack_forget()
        self.report_btn.pack_forget()
        self.source_browse.set_enabled(False)
        self.destination_browse.set_enabled(False)
        self.source_entry.configure(state="disabled")
        self.destination_entry.configure(state="disabled")
        self.recursive_check.configure(state="disabled")
        self.rescan_btn.configure(state="disabled")
        self.all_types_btn.configure(state="disabled")
        self.clear_types_btn.configure(state="disabled")
        for chip in self.chips.values():
            chip.set_enabled(False)
        self.conflict_combo.configure(state="disabled")
        self.copy_btn.set_enabled(False)
        self.move_btn.set_enabled(False)
        self.progress.stop()
        self.progress.configure(
            mode="determinate", maximum=max(1, len(plan.files)), value=0
        )
        self._set_status(
            "busy",
            f"正在{operation.label}文件…",
            f"0 / {len(plan.files):,}",
        )

        total = len(plan.files)
        cancel_event = self.cancel_event

        def progress_callback(event: ProgressEvent) -> None:
            self.worker_queue.put(("run_progress", event, operation))

        def work() -> None:
            try:
                result = self.organizer.execute(
                    plan,
                    operation=operation,
                    conflict=conflict,
                    progress=progress_callback,
                    cancel_event=cancel_event,
                )
            except OrganizerError as error:
                self.worker_queue.put(("run_error", str(error)))
            except Exception as error:  # defensive: keep the UI alive
                self.worker_queue.put(("run_error", f"意外错误：{error}"))
            else:
                self.worker_queue.put(("run_done", result, total))

        threading.Thread(target=work, daemon=True, name="tidyfiles-run").start()

    def _on_run_progress(self, event: ProgressEvent, operation: Operation) -> None:
        if not self.busy:
            return
        self.progress.configure(value=event.done)
        outcome = event.outcome
        label = {
            "copied": "已复制",
            "moved": "已移动",
            "skipped": "已跳过",
            "failed": "失败",
        }.get(outcome.status, "")
        detail = (
            f"{event.done:,} / {event.total:,} · {label} "
            f"{middle_ellipsis(outcome.source.name, 34)}"
        )
        self._set_status("busy", f"正在{operation.label}文件…", detail)

    def _on_run_done(self, result: ExecutionResult, total: int) -> None:
        self.busy = False
        self.cancel_event = None
        self._restore_controls()
        self._last_output = result.destination
        self._last_report = result.report_path
        completed = result.completed
        if result.cancelled:
            self.progress.configure(value=min(total, result.completed))
        else:
            self.progress.configure(value=total)

        parts = []
        if result.copied:
            parts.append(f"已复制 {result.copied:,} 个")
        if result.moved:
            parts.append(f"已移动 {result.moved:,} 个")
        if result.skipped:
            parts.append(f"跳过 {result.skipped:,} 个")
        if result.failed:
            parts.append(f"失败 {result.failed:,} 个")
        summary = " · ".join(parts) or "没有文件被处理"

        if result.cancelled:
            kind, title = "warning", "已取消整理"
        elif result.failed:
            kind, title = "warning", "整理完成，但有文件失败"
        else:
            kind, title = "success", "整理完成"
        detail = summary
        if result.report_path:
            detail += " · 整理记录已保存"
        self._set_status(kind, title, detail)

        if result.destination.exists():
            self.open_btn.pack(side="right", padx=(0, 8))
        if result.report_path:
            self.report_btn.pack(side="right", padx=(0, 8))

        lines = [summary, "", f"输出文件夹：\n{result.destination}"]
        if result.report_path:
            lines.extend(["", f"整理记录：\n{result.report_path}"])
        if result.report_error:
            lines.extend(["", f"记录保存失败：{result.report_error}"])
        if result.failed:
            lines.extend(["", "部分文件未能处理，请检查对应文件是否被占用或权限不足。"])
        message = "\n".join(lines)
        if result.cancelled:
            messagebox.showwarning("已取消整理", message, parent=self.root)
        elif result.failed:
            messagebox.showwarning("整理完成", message, parent=self.root)
        else:
            messagebox.showinfo("整理完成", message, parent=self.root)

        if result.operation is Operation.MOVE:
            self.root.after(150, lambda: self._start_scan(force=True, silent=True))

    def _on_run_error(self, message: str) -> None:
        self.busy = False
        self.cancel_event = None
        self._restore_controls()
        self._finish_progress()
        self._set_status("error", "整理没有开始", message)
        messagebox.showerror("整理失败", message, parent=self.root)

    def _restore_controls(self) -> None:
        self.start_btn.set_text("开始整理  →")
        self.cancel_btn.pack_forget()
        self.copy_btn.set_enabled(True)
        self.move_btn.set_enabled(True)
        self.source_browse.set_enabled(True)
        self.destination_browse.set_enabled(True)
        self.source_entry.configure(state="normal")
        self.destination_entry.configure(state="normal")
        self.recursive_check.configure(state="normal")
        self.rescan_btn.configure(state="normal")
        self.conflict_combo.configure(state="readonly")
        filter_state = "normal" if self.categorize_var.get() else "disabled"
        self.all_types_btn.configure(state=filter_state)
        self.clear_types_btn.configure(state=filter_state)
        self.categorize_toggle.refresh()
        counts = self.scan_result.counts() if self.scan_result else {}
        for key, chip in self.chips.items():
            chip.set_enabled(self.categorize_var.get() and counts.get(key, 0) > 0)
        self._update_start_state()

    def _cancel_run(self) -> None:
        if not self.busy or self.cancel_event is None:
            return
        self.cancel_event.set()
        self.cancel_btn.set_text("取消中…")
        self.cancel_btn.set_enabled(False)
        self._set_status(
            "warning",
            "正在取消…",
            "当前文件处理完成后停止，已完成的文件不会回滚",
        )

    def _on_escape(self, _event=None) -> None:
        if self.busy:
            self._cancel_run()

    # ---------------------------------------------------------------- actions

    def _open_output(self) -> None:
        if self._last_output is not None and self._last_output.exists():
            self._open_path(self._last_output)

    def _open_report(self) -> None:
        if self._last_report is not None and self._last_report.exists():
            self._open_path(self._last_report)

    @staticmethod
    def _open_path(path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError:
            pass

    def _set_status(self, kind: str, text: str, detail: str = "") -> None:
        colors = {
            "idle": FAINT,
            "busy": ACCENT,
            "success": SUCCESS,
            "warning": WARNING,
            "error": DANGER,
        }
        self.status_dot.itemconfigure(self._status_dot, fill=colors.get(kind, FAINT))
        self.status_label.configure(text=text)
        self.detail_label.configure(text=detail)

    def _drain_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                message = self.worker_queue.get_nowait()
                kind = message[0]
                if kind == "scan_done":
                    _, token, result, silent = message
                    self._on_scan_done(token, result, silent)
                elif kind == "scan_error":
                    _, token, text, silent = message
                    self._on_scan_error(token, text, silent)
                elif kind == "run_progress":
                    _, event, operation = message
                    self._on_run_progress(event, operation)
                elif kind == "run_done":
                    _, result, total = message
                    self._on_run_done(result, total)
                elif kind == "run_error":
                    _, text = message
                    self._on_run_error(text)
        except queue.Empty:
            pass
        self.root.after(80, self._drain_queue)

    # ------------------------------------------------------------------ close

    def _on_close(self) -> None:
        if self._closing:
            return
        if self.busy:
            if not messagebox.askokcancel(
                "正在整理",
                "文件还在处理中。确定退出吗？当前文件完成后的操作可能会中断。",
                parent=self.root,
                icon="warning",
                default="cancel",
            ):
                return
            if self.cancel_event is not None:
                self.cancel_event.set()
        self._closing = True
        geometry = self.root.geometry() if self.root.state() == "normal" else ""
        settings = {
            "source": self.source_var.get().strip(),
            "destination": self.destination_var.get().strip(),
            "recursive": self.recursive_var.get(),
            "operation": self.operation_var.get(),
            "conflict": self.conflict_var.get(),
            "categorized": self.categorize_var.get(),
            "selected_categories": sorted(self._selected_categories()),
            "geometry": geometry,
        }
        save_settings(settings)
        self.root.destroy()


def main() -> int:
    try:
        root = tk.Tk()
    except tk.TclError as error:
        print(f"无法启动图形界面：{error}", file=sys.stderr)
        return 1
    TidyFilesApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
