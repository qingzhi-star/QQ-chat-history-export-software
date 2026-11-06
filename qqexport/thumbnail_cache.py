"""仅保存按内容寻址的缩略图；独占短租约保护跨进程清理和 LRU。"""
from contextlib import contextmanager
import base64
import hashlib
import io
import os
from pathlib import Path
import threading
import time
from qqexport import config
from qqexport.errors import UserError
from qqexport.filelock import FileLease
from qqexport.export.serde import hash_file

_lock = threading.RLock()
DEFAULT_LIMIT = 2*1024**3


def image_mime(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12]==b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"


class ThumbnailCache:
    def __init__(self,limit=DEFAULT_LIMIT):
        self.root = config.app_dir()/"cache"/"thumbs"
        self.limit = max(0,int(limit))

    @contextmanager
    def lease(self):
        with _lock:
            self.root.mkdir(parents=True,exist_ok=True)
            with FileLease(self.root/".usage.lock",exclusive=True):
                yield

    def _trim(self):
        files = sorted(self.root.glob("*.png"),key=lambda p:p.stat().st_mtime_ns)
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total<=self.limit:
                break
            total -= path.stat().st_size
            path.unlink(missing_ok=True)

    def clear(self):
        with self.lease():
            for path in self.root.glob("*.png"):
                path.unlink(missing_ok=True)

    def load(self,media):
        from PIL import Image
        if media.source and media.source.is_file():
            key = hash_file(media.source)
            source = media.source
        elif media.data_uri:
            data = base64.b64decode(media.data_uri.split(",",1)[1],validate=True)
            key,source = hashlib.sha256(data).hexdigest(),io.BytesIO(data)
        else:
            return None
        target = self.root/(key+"-240-v1.png")
        if self.limit:
            try:
                with self.lease():
                    if target.is_file():
                        try:
                            with Image.open(target) as original:
                                image = original.copy()
                            os.utime(target,None)
                            self._trim()
                            return image
                        except (OSError,ValueError):
                            target.unlink(missing_ok=True)
            except (OSError,UserError):
                pass
        with Image.open(source) as original:
            original.thumbnail((240,240))
            image = original.convert("RGBA")
        if self.limit:
            try:
                with self.lease():
                    temporary = target.with_suffix(f".{os.getpid()}-{time.time_ns()}.tmp")
                    try:
                        image.save(temporary,format="PNG")
                        os.replace(temporary,target)
                    finally:
                        temporary.unlink(missing_ok=True)
                    self._trim()
            except (OSError,UserError):
                pass
        return image
