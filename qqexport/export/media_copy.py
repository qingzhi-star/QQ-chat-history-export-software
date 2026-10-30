"""T7：只复制被引用媒体；硬链接优先、失败回退复制；绝不改源。"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from qqexport.export.model import MediaRef


@dataclass
class CopyResult:
    copied: int = 0
    bytes_copied: int = 0
    missing: int = 0
    rel_by_source: dict[Path, str] = field(default_factory=dict)


def _target_name(ref: MediaRef) -> str:
    ext = (ref.source.suffix if ref.source else "") or \
        (Path(ref.filename).suffix if ref.filename else "")
    token = (ref.md5 or (ref.source.stem if ref.source else "media"))[:8]
    return f"{ref.kind}_{token}{ext.lower()}"


def copy_media(refs: list[MediaRef], out_dir: Path) -> CopyResult:
    result = CopyResult()
    out_dir.mkdir(parents=True, exist_ok=True)
    for ref in refs:
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
            try:
                os.link(ref.source, dest)
            except OSError:
                shutil.copy2(ref.source, dest)
        except OSError:
            result.missing += 1
            continue
        result.copied += 1
        result.bytes_copied += ref.source.stat().st_size
        result.rel_by_source[ref.source] = name
    return result
