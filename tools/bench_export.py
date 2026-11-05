"""P1+ 性能基准：合成库 → 导出，记录耗时与 Python 峰值内存。

用法（仓库根目录）：
    python tools/bench_export.py --messages 10000 --formats txt
    python tools/bench_export.py --messages 5000 --formats txt,html
结果以 JSON 输出；不写入聊天正文，只用于本地验收记录。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qqexport.export import orchestrator  # noqa: E402
from qqexport.export.session import ExportSource  # noqa: E402
from qqexport.qq import contacts, media  # noqa: E402
from tests.helpers import synth_db as sd  # noqa: E402


def build_source(tmp: Path, count: int) -> ExportSource:
    rows = [
        sd.text_row(ts=1700000000 + i, text=f"message {i} " + "x" * 40,
                    uid="u_a", msg_id=i + 1)
        for i in range(count)
    ]
    db = sd.create_msg_db(tmp / "nt_msg.db", c2c=rows)
    c = contacts.Contacts.load(None, None)
    return ExportSource.from_parts(db, c, media.build_index(tmp / "none"),
                                   "123456789")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--messages", type=int, default=10000)
    ap.add_argument("--formats", default="txt")
    ap.add_argument("--out", default="")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    formats = {x.strip() for x in args.formats.split(",") if x.strip()}

    with tempfile.TemporaryDirectory(prefix="qqexport-bench-") as td:
        tmp = Path(td)
        out = Path(args.out) if args.out else tmp / "out"
        src = build_source(tmp, args.messages)
        opts = orchestrator.ExportOptions(
            formats=formats, out_base=out,
            now=datetime(2026, 11, 6, 12, 0, 0))
        tracemalloc.start()
        started = time.perf_counter()
        rep = orchestrator.export_chats(src, ["c2c:u_a"], opts)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        result = {
            "messages": args.messages,
            "formats": sorted(formats),
            "seconds": round(elapsed, 3),
            "peak_mb": round(peak / 1024 / 1024, 1),
            "exported": rep.chats_exported,
            "messages_ok": rep.messages_ok,
            "warnings": len(rep.warnings),
        }
        print(json.dumps(result, ensure_ascii=False))
        src.close()
        if not args.keep and not args.out:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
