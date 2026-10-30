"""T7：nt_data 媒体索引与解析（只读源目录）。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from qqexport.export.model import MediaRef

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_SUBDIRS = ("Pic", "Video", "File", "Ptt")


@dataclass(frozen=True)
class MediaIndex:
    by_md5: dict[str, tuple[Path, ...]]
    media_dir: Path | None = None


def _md5_of(path: Path) -> str | None:
    stem = path.stem.lower()
    candidate = stem[:32]
    return candidate if _HEX32.match(candidate) else None


def build_index(media_dir: Path) -> MediaIndex:
    by_md5: dict[str, list[Path]] = {}
    if not media_dir.is_dir():
        return MediaIndex({}, media_dir)
    for sub in _SUBDIRS:
        root = media_dir / sub
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            md5 = _md5_of(path)
            if md5:
                by_md5.setdefault(md5, []).append(path)

    def order(p: Path) -> tuple[int, str]:
        lowered = [part.lower() for part in p.parts]
        return (0 if "ori" in lowered else 1, str(p).lower())

    return MediaIndex({k: tuple(sorted(v, key=order)) for k, v in by_md5.items()},
                      media_dir)


def resolve(index: MediaIndex, kind: str, md5_hex: str | None,
            local_path: str | None, filename: str | None) -> MediaRef:
    meta = dict(kind=kind, md5=md5_hex, filename=filename)
    if local_path:
        p = Path(local_path)
        if p.is_file():
            return MediaRef(source=p, missing=False, **meta)
    if md5_hex and md5_hex in index.by_md5:
        return MediaRef(source=index.by_md5[md5_hex][0], missing=False, **meta)
    if filename:
        wanted = filename.lower()
        for paths in index.by_md5.values():
            for p in paths:
                if p.name.lower() == wanted or p.stem.lower().startswith(
                        wanted.rsplit(".", 1)[0].lower()):
                    return MediaRef(source=p, missing=False, **meta)
    return MediaRef(missing=True, **meta)
