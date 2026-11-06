"""T7：nt_data 媒体索引与解析（只读源目录）。"""
from __future__ import annotations

import re
import os
from bisect import bisect_left
from dataclasses import dataclass,field
from pathlib import Path

from qqexport.export.model import MediaRef

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_SUBDIRS = ("Pic", "Video", "File", "Ptt")


@dataclass(frozen=True)
class MediaIndex:
    by_md5: dict[str, tuple[Path, ...]]
    media_dir: Path | None = None
    by_name: dict[str,Path] = field(default_factory=dict)
    stems: tuple[str,...] = ()
    by_stem: dict[str,Path] = field(default_factory=dict)
    folders: dict = field(default_factory=dict,repr=False,compare=False)


def _md5_of(path: Path) -> str | None:
    stem = path.stem.lower()
    candidate = stem[:32]
    return candidate if _HEX32.match(candidate) else None


def build_index(media_dir: Path,*,previous: MediaIndex | None = None) -> MediaIndex:
    by_md5: dict[str, list[Path]] = {}
    if not media_dir.is_dir():
        return MediaIndex({}, media_dir)
    folders = {}
    old = previous.folders if previous and previous.media_dir==media_dir else {}
    def paths(root):
        try:
            stamp = root.stat().st_mtime_ns
        except OSError:
            return
        cached = old.get(root)
        if cached and cached[0]==stamp:
            files,children = cached[1:]
        else:
            files,children = [],[]
            try:
                with os.scandir(root) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            children.append(Path(entry.path))
                        elif entry.is_file():
                            files.append(Path(entry.path))
            except OSError:
                return
            files,children = tuple(files),tuple(children)
        folders[root] = (stamp,files,children)
        yield from files
        for child in children:
            yield from paths(child)
    for sub in _SUBDIRS:
        for path in paths(media_dir/sub):
            md5 = _md5_of(path)
            if md5:
                by_md5.setdefault(md5, []).append(path)

    def order(p: Path) -> tuple[int, str]:
        lowered = [part.lower() for part in p.parts]
        return (0 if "ori" in lowered else 1, str(p).lower())

    grouped = {k:tuple(sorted(v,key=order)) for k,v in by_md5.items()}
    by_name,by_stem = {},{}
    for candidates in grouped.values():
        for path in candidates:
            by_name.setdefault(path.name.lower(),path)
            by_stem.setdefault(path.stem.lower(),path)
    return MediaIndex(grouped,media_dir,by_name,tuple(sorted(by_stem)),by_stem,folders)


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
        path = index.by_name.get(wanted)
        prefix = wanted.rsplit(".",1)[0]
        position = bisect_left(index.stems,prefix)
        if path is None and position<len(index.stems) and index.stems[position].startswith(prefix):
            path = index.by_stem[index.stems[position]]
        if path is not None:
            return MediaRef(source=path,missing=False,**meta)
    return MediaRef(missing=True, **meta)
