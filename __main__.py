#!/usr/bin/env python3
"""
TranscritorIA — entry point
Executar com:
    python -m transcritor
"""

import sys
import tkinter as tk
from tkinter import ttk

try:
    import assemblyai  # noqa: F401
except ImportError:
    print("[ERRO] Dependência ausente. Execute:\n  pip install assemblyai")
    sys.exit(1)

from .config import COLORS
from .gui import App


def main():
    root = App()

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        "TCombobox",
        fieldbackground=COLORS["surface"],
        background=COLORS["surface"],
        foreground=COLORS["text"],
        arrowcolor=COLORS["text"],
        bordercolor=COLORS["border"],
        relief="flat",
    )
    style.configure(
        "TProgressbar",
        troughcolor=COLORS["surface"],
        background=COLORS["accent"],
        bordercolor=COLORS["bg"],
        lightcolor=COLORS["accent"],
        darkcolor=COLORS["accent"],
    )

    root.mainloop()


if __name__ == "__main__":
    main()
