"""支持 `python -m qqexport`（不依赖 Scripts 目录在 PATH 中）。

等价于 console script 的 `qqexport`：命令行逻辑全部在 `qqexport.cli`。
"""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
