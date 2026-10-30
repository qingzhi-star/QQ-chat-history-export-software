#!/usr/bin/env python3
"""发布门：校验 wheel 内容。标准库实现，CI 与本地同一套规则。

用法：
    python tools/check_wheel.py dist          # 校验目录下的 .whl
    python tools/check_wheel.py path/to/x.whl

必须：包含 qqexport 全部子模块；元数据 Requires-Python >=3.12。
禁止：tests/、.superpowers/、任何 *.db / keys.json / 明文数据。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REQUIRED = (
    "qqexport/__init__.py",
    "qqexport/__main__.py",
    "qqexport/cli.py",
    "qqexport/config.py",
    "qqexport/doctor.py",
    "qqexport/report.py",
    "qqexport/core/__init__.py",
    "qqexport/core/companions.py",
    "qqexport/core/decrypt.py",
    "qqexport/core/discover.py",
    "qqexport/core/keyscan.py",
    "qqexport/core/keystore.py",
    "qqexport/core/snapshot.py",
    "qqexport/core/sqlcipher.py",
    "qqexport/core/wal.py",
    "qqexport/qq/__init__.py",
    "qqexport/qq/contacts.py",
    "qqexport/qq/media.py",
    "qqexport/qq/messages.py",
    "qqexport/qq/msgmap.py",
    "qqexport/qq/proto.py",
    "qqexport/qq/tables.py",
    "qqexport/export/__init__.py",
    "qqexport/export/html.py",
    "qqexport/export/media_copy.py",
    "qqexport/export/model.py",
    "qqexport/export/orchestrator.py",
    "qqexport/export/pdf.py",
    "qqexport/export/report.py",
    "qqexport/export/session.py",
    "qqexport/export/txt.py",
)
FORBIDDEN_PREFIXES = ("tests/", ".superpowers/")
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".decrypted.db")
FORBIDDEN_NAMES = ("keys.json", "provenance.json")


def check_wheel(path: Path) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        for required in REQUIRED:
            if required not in names:
                problems.append(f"缺少模块：{required}")
        for name in names:
            if name.startswith(FORBIDDEN_PREFIXES) or name.endswith(FORBIDDEN_SUFFIXES):
                problems.append(f"包含禁止文件：{name}")
            elif Path(name).name in FORBIDDEN_NAMES:
                problems.append(f"包含禁止文件：{name}")
        metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
        if not metadata:
            problems.append("缺少 METADATA")
        else:
            text = zf.read(metadata[0]).decode("utf-8", "replace")
            if "Requires-Python: >=3.12" not in text:
                problems.append("METADATA 未声明 Requires-Python >=3.12")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0] if args else "dist")
    wheels = sorted(target.glob("*.whl")) if target.is_dir() else [target]
    if not wheels:
        print(f"[FAIL] 找不到 wheel：{target}", file=sys.stderr)
        return 1
    failed = False
    for wheel in wheels:
        problems = check_wheel(wheel)
        if problems:
            failed = True
            print(f"[FAIL] {wheel.name}", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
        else:
            print(f"[OK] {wheel.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
