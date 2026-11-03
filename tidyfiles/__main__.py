# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the GUI by default, or the CLI when arguments are supplied."""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import main as cli_main

        return cli_main()
    from .gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
