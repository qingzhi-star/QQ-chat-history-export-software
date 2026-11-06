"""T7：只复制被引用媒体；硬链接优先、失败回退复制；绝不改源。"""
from __future__ import annotations

import os
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from qqexport.export.model import MediaRef
from qqexport.export.cancel import check_cancel


@dataclass
class CopyResult:
    copied: int = 0
    bytes_copied: int = 0
    missing: int = 0
    rel_by_source: dict[Path, str] = field(default_factory=dict)


def _target_name(ref: MediaRef) -> str:
    ext = (ref.source.suffix if ref.source else "") or \
        (Path(ref.filename).suffix if ref.filename else "")
    token = ref.md5 or hashlib.sha256(str(ref.source).encode("utf-8")).hexdigest()
    return f"{ref.kind}_{token}{ext.lower()}"


def copy_media(refs: list[MediaRef], out_dir: Path, *, cancel=None) -> CopyResult:
    result = CopyResult()
    out_dir.mkdir(parents=True, exist_ok=True)
    for ref in refs:
        check_cancel(cancel)
        if ref.source is None or not ref.source.is_file():
            result.missing += 1
            continue
        if ref.source in result.rel_by_source:
            continue
        name = _target_name(ref)
        dest = out_dir / name
        try:
            if dest.exists():
                dest.unlink()
            # 独立副本：用户编辑导出附件也不会经硬链接改到 QQ 源文件。
            with ref.source.open("rb") as src, dest.open("wb") as dst:
                while block := src.read(1024*1024):
                    check_cancel(cancel)
                    dst.write(block)
            shutil.copystat(ref.source,dest)
        except OSError:
            result.missing += 1
            continue
        result.copied += 1
        result.bytes_copied += ref.source.stat().st_size
        result.rel_by_source[ref.source] = name
    return result
