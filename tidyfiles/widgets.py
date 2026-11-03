"""Small hand-drawn Tk widgets used by the desktop UI.

Tk's native widgets are functional but visually dated, so the app uses a few
lightweight canvas widgets for its primary controls.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont

from .categories import Category

BG = "#F3F5FA"
CARD = "#FFFFFF"
BORDER = "#E4E8F1"
TEXT = "#182235"
MUTED = "#6B7891"
FAINT = "#94A3B8"
ACCENT = "#4A63E7"
ACCENT_DARK = "#3B53CE"
ACCENT_SOFT = "#EEF2FF"
ACCENT_LINE = "#C9D4FF"
SUCCESS = "#0F9F6E"
WARNING = "#D97706"
DANGER = "#DC2626"
NAVY = "#111A2E"
NAVY_SOFT = "#1C2A48"
FIELD_BG = "#FAFBFE"


def rounded_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    **kwargs,
) -> int:
    """Draw an antialiased-looking rounded rectangle as a smooth polygon."""

    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class RoundedButton(tk.Canvas):
    """A flat, rounded button with hover and disabled states."""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        font: tuple,
        width: int = 120,
        height: int = 36,
        kind: str = "primary",
        radius: int = 9,
    ) -> None:
        self._parent_bg = master.cget("bg") if isinstance(master, tk.Widget) else CARD
        super().__init__(
            master,
            width=width,
            height=height,
            bg=self._parent_bg,
            highlightthickness=0,
            bd=0,
            takefocus=1,
        )
        self._text = text
        self._command = command
        self._font = font
        self._kind = kind
        self._radius = radius
        self._enabled = True
        self._hover = False
        self._focused = False
        self._width = width
        self._height = height
        self._shape: int | None = None
        self._label: int | None = None
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Return>", self._on_click)
        self.bind("<space>", self._on_click)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_blur)
        self._render()

    def _palette(self) -> tuple[str, str, str]:
        if not self._enabled:
            if self._kind == "primary":
                return "#E8EDF6", FAINT, ""
            return "#F5F7FB", "#B0BACB", BORDER
        if self._kind == "primary":
            return (ACCENT_DARK if self._hover else ACCENT), "#FFFFFF", ""
        if self._kind == "secondary":
            return ("#F4F7FF" if self._hover else CARD), TEXT, BORDER
        if self._kind == "danger":
            return ("#FEF2F2" if self._hover else CARD), DANGER, "#FECACA"
        # ghost
        return (ACCENT_SOFT if self._hover else self._parent_bg), ACCENT, ""

    def _render(self) -> None:
        fill, foreground, outline = self._palette()
        self.delete("all")
        self._shape = rounded_rect(
            self,
            1,
            1,
            self._width - 1,
            self._height - 1,
            self._radius,
            fill=fill,
            outline=outline or fill,
            width=1,
        )
        if self._focused and self._enabled:
            self.itemconfigure(self._shape, outline=ACCENT_LINE, width=2)
        self._label = self.create_text(
            self._width / 2,
            self._height / 2 + 0.5,
            text=self._text,
            fill=foreground,
            font=self._font,
        )
        self.configure(cursor="hand2" if self._enabled else "arrow")

    def _on_click(self, _event: tk.Event | None = None) -> None:
        if not self._enabled:
            return
        self.focus_set()
        self._command()

    def _on_enter(self, _event: tk.Event | None = None) -> None:
        if not self._enabled:
            return
        self._hover = True
        self._render()

    def _on_leave(self, _event: tk.Event | None = None) -> None:
        self._hover = False
        self._render()

    def _on_focus(self, _event: tk.Event | None = None) -> None:
        self._focused = True
        self._render()

    def _on_blur(self, _event: tk.Event | None = None) -> None:
        self._focused = False
        self._render()

    def set_enabled(self, enabled: bool) -> None:
        if self._enabled != enabled:
            self._enabled = enabled
            self._hover = False
            self._render()

    def set_text(self, text: str) -> None:
        if self._text != text:
            self._text = text
            self._render()

    def set_kind(self, kind: str) -> None:
        if self._kind != kind:
            self._kind = kind
            self._render()


class SwitchToggle(tk.Canvas):
    """A compact switch with an explanatory label."""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        command: Callable[[], None],
        *,
        font: tuple,
        width: int = 176,
        height: int = 30,
    ) -> None:
        self._parent_bg = master.cget("bg") if isinstance(master, tk.Widget) else CARD
        super().__init__(
            master,
            width=width,
            height=height,
            bg=self._parent_bg,
            highlightthickness=0,
            bd=0,
            takefocus=1,
            cursor="hand2",
        )
        self._text = text
        self._variable = variable
        self._command = command
        self._font = font
        self._width = width
        self._height = height
        self._hover = False
        self.bind("<Button-1>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<FocusIn>", lambda _event: self.refresh())
        self.bind("<FocusOut>", lambda _event: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        self.delete("all")
        on = self._variable.get()
        track_fill = ACCENT if on else "#CDD6E5"
        if self._hover:
            track_fill = ACCENT_DARK if on else "#B9C5D8"
        rounded_rect(self, 2, 7, 34, 25, 9, fill=track_fill, outline=track_fill)
        knob_x = 25 if on else 11
        self.create_oval(
            knob_x - 6,
            10,
            knob_x + 6,
            22,
            fill="#FFFFFF",
            outline="",
        )
        self.create_text(
            43,
            self._height / 2,
            text=self._text,
            anchor="w",
            font=self._font,
            fill=TEXT if on else MUTED,
        )
        if self.focus_get() is self:
            self.create_rectangle(
                1,
                1,
                self._width - 1,
                self._height - 1,
                outline=ACCENT_LINE,
                width=1,
            )

    def _toggle(self, _event: tk.Event | None = None) -> None:
        self.focus_set()
        self._variable.set(not self._variable.get())
        self.refresh()
        self._command()

    def _enter(self, _event: tk.Event | None = None) -> None:
        self._hover = True
        self.refresh()

    def _leave(self, _event: tk.Event | None = None) -> None:
        self._hover = False
        self.refresh()


class CategoryChip(tk.Frame):
    """A selectable category tile with live file count."""

    def __init__(
        self,
        master: tk.Misc,
        category: Category,
        variable: tk.BooleanVar,
        command: Callable[[], None],
        *,
        font: tuple,
        count_font: tuple,
        height: int = 42,
    ) -> None:
        super().__init__(
            master,
            bg=CARD,
            height=height,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT_LINE,
            takefocus=1,
            cursor="hand2",
        )
        self.grid_propagate(False)
        self.pack_propagate(False)
        self.category = category
        self.variable = variable
        self._command = command
        self._enabled = True
        self._hover = False
        self._count = 0

        self.accent = tk.Frame(self, bg=category.color, width=3)
        self.accent.pack(side="left", fill="y")
        self.accent.pack_propagate(False)

        self.check = tk.Canvas(
            self, width=18, height=18, bg=CARD, highlightthickness=0, bd=0
        )
        self.check.pack(side="left", padx=(9, 0), pady=10)

        self.name_label = tk.Label(
            self,
            text=category.folder,
            bg=CARD,
            fg=TEXT,
            font=font,
            anchor="w",
        )
        self.name_label.pack(side="left", padx=(7, 2))

        self.count_label = tk.Label(
            self,
            text="0",
            bg=CARD,
            fg=FAINT,
            font=count_font,
        )
        self.count_label.pack(side="right", padx=(2, 10))

        self._targets = [
            self,
            self.accent,
            self.check,
            self.name_label,
            self.count_label,
        ]
        for widget in self._targets:
            widget.bind("<Button-1>", self._toggle)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)
        self.bind("<Return>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<FocusIn>", lambda _event: self._paint())
        self.bind("<FocusOut>", lambda _event: self._paint())
        self._paint()

    def _toggle(self, _event: tk.Event | None = None) -> None:
        if not self._enabled:
            return
        self.focus_set()
        self.variable.set(not self.variable.get())
        self._paint()
        self._command()

    def _enter(self, _event: tk.Event | None = None) -> None:
        if self._enabled:
            self._hover = True
            self._paint()

    def _leave(self, _event: tk.Event | None = None) -> None:
        self._hover = False
        self._paint()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._paint()

    def set_count(self, count: int) -> None:
        self._count = count
        self.count_label.configure(text=str(count))
        self._paint()

    def refresh(self) -> None:
        self._paint()

    def _paint(self) -> None:
        selected = bool(self.variable.get()) and self._enabled
        if not self._enabled:
            background = "#FAFBFD"
        elif selected:
            background = "#F5F7FF"
        elif self._hover:
            background = "#F8FAFE"
        else:
            background = CARD

        border = ACCENT_LINE if selected else ("#D6DEEE" if self._hover else BORDER)
        if self.focus_get() is self and self._enabled:
            border = ACCENT
        self.configure(bg=background, highlightbackground=border)
        for widget in (self.check, self.name_label, self.count_label):
            widget.configure(bg=background)

        self.check.delete("all")
        outline = ACCENT if selected else ("#C5CFDF" if self._enabled else "#DDE4EE")
        fill = ACCENT if selected else background
        self.check.create_oval(1, 1, 17, 17, fill=fill, outline=outline, width=1.4)
        if selected:
            self.check.create_line(
                5,
                9,
                8,
                12,
                13,
                6,
                fill="#FFFFFF",
                width=2,
                capstyle="round",
                joinstyle="round",
            )
        self.accent.configure(bg=self.category.color if self._enabled else "#DDE4EE")
        self.name_label.configure(fg=TEXT if self._enabled else "#A4AEC0")
        count_color = self.category.color if self._count and self._enabled else FAINT
        self.count_label.configure(fg=count_color)


class StepBadge(tk.Canvas):
    def __init__(self, master: tk.Misc, number: int, *, background: str = CARD) -> None:
        super().__init__(
            master,
            width=24,
            height=24,
            bg=background,
            highlightthickness=0,
            bd=0,
        )
        self.create_oval(1, 1, 23, 23, fill=ACCENT_SOFT, outline="")
        self.create_text(
            12,
            12,
            text=str(number),
            fill=ACCENT,
            font=("Segoe UI", 9, "bold"),
        )


def choose_fonts(root: tk.Misc) -> dict[str, tuple]:
    """Pick attractive fonts that exist on the current platform."""

    families = set(tkfont.families(root))
    if "Microsoft YaHei UI" in families:
        ui = "Microsoft YaHei UI"
    elif "PingFang SC" in families:
        ui = "PingFang SC"
    elif "Noto Sans CJK SC" in families:
        ui = "Noto Sans CJK SC"
    else:
        ui = "Segoe UI" if "Segoe UI" in families else "TkDefaultFont"
    display = "Segoe UI" if "Segoe UI" in families else ui
    mono = "Cascadia Mono" if "Cascadia Mono" in families else "Consolas"
    return {
        "ui": (ui, 10),
        "small": (ui, 9),
        "tiny": (ui, 8),
        "label": (ui, 9, "bold"),
        "body_bold": (ui, 10, "bold"),
        "section": (ui, 12, "bold"),
        "title": (display, 18, "bold"),
        "subtitle": (ui, 9),
        "stat": (display, 24, "bold"),
        "stat_small": (display, 13, "bold"),
        "mono": (mono, 9),
        "button": (ui, 10, "bold"),
        "button_small": (ui, 9, "bold"),
        "display": (display, 11, "bold"),
    }
