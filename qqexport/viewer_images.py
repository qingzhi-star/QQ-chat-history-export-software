"""系统查看器使用独立图片副本；避免 QQ 原图或归档临时文件被外部编辑/占用。"""
import base64
import hashlib
import io
import os
import re
from pathlib import Path
import time
import threading
from PIL import Image,UnidentifiedImageError
from qqexport import config
from qqexport.export.serde import copy_file,hash_file
from qqexport.errors import UserError

LIMIT = 256*1024**2
_EXTENSIONS = {"PNG":".png","JPEG":".jpg","GIF":".gif","WEBP":".webp","BMP":".bmp","TIFF":".tif","ICO":".ico","AVIF":".avif"}
_lock = threading.RLock()


def cache_dir():
    root = config.app_dir()/"cache"/"opened-images"
    if root.is_symlink() or getattr(root,"is_junction",lambda:False)():
        raise UserError("图片缓存目录为链接，请先恢复正常目录")
    root.mkdir(parents=True,exist_ok=True)
    return root


def clear(keep=None,limit=0):
    with _lock:
        return _clear(keep,limit)


def _clear(keep,limit):
    root = cache_dir()
    files = sorted((p for p in root.iterdir() if p.is_file() and re.fullmatch(r"[0-9a-f]{64}\.(png|jpg|gif|webp|bmp|tif|ico|avif)",p.name)),key=lambda p:p.stat().st_mtime_ns)
    total = sum(p.stat().st_size for p in files)
    retained = 0
    for path in files:
        if total<=limit:
            break
        if path==keep:
            retained += 1
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            total -= size
        except OSError:
            retained += 1
    return retained


def prepare(media):
    with _lock:
        return _prepare(media)


def _prepare(media):
    data,source = None,None
    if media.source and media.source.is_file():
        source = media.source
        digest = hash_file(source)
    elif media.data_uri:
        try:
            data = base64.b64decode(media.data_uri.split(",",1)[1],validate=True)
        except (ValueError,IndexError):
            raise UserError("图片数据无效") from None
        digest = hashlib.sha256(data).hexdigest()
    else:
        raise UserError("本机没有此图片，请在 QQ 中下载后重新检测")
    try:
        with Image.open(source if source else io.BytesIO(data)) as image:
            extension = _EXTENSIONS.get(image.format,".png")
    except (OSError,UnidentifiedImageError):
        raise UserError("图片格式不可识别，请重新下载原图") from None
    target = cache_dir()/(digest+extension)
    if target.is_file() and hash_file(target)==digest:
        os.utime(target,None)
        return target
    temporary = target.with_name(f".tmp-{os.getpid()}-{time.time_ns()}")
    try:
        if source:
            copy_file(source,temporary)
        else:
            temporary.write_bytes(data)
        if hash_file(temporary)!=digest:
            raise UserError("原图在复制时发生变化，请重试")
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)
    clear(keep=target,limit=LIMIT)
    return target
