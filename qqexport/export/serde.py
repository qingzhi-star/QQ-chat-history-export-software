"""版本化消息快照：不保存 QQ 源绝对路径，媒体只引用快照内相对文件。"""
from __future__ import annotations
import base64
from dataclasses import fields,replace
from datetime import datetime
import hashlib
import json
from pathlib import Path

from qqexport.export.model import Message,Segment,MediaRef
from qqexport.export.cancel import check_cancel
from qqexport.errors import UserError

MAX_RECORD = 32*1024*1024


class SnapshotError(UserError):
    pass


def relative_path(value):
    if not isinstance(value,str) or not value:
        raise SnapshotError("快照媒体路径无效")
    path = Path(value)
    if path.is_absolute() or path.drive or ".." in path.parts or ":" in value:
        raise SnapshotError("快照不允许外部媒体路径")
    return path


def message_data(message,depth=0):
    if depth>8:
        raise SnapshotError("回复层级过深")
    data = {f.name:getattr(message,f.name) for f in fields(Message) if f.name!="segments"}
    data["avatar_rel"] = None
    data["segments"] = []
    for segment in message.segments:
        item = {f.name:getattr(segment,f.name) for f in fields(Segment) if f.name not in {"media","quoted"}}
        if segment.media is not None:
            media = {f.name:getattr(segment.media,f.name) for f in fields(MediaRef) if f.name not in {"source","remote_url"}}
            if media["filename"]:
                media["filename"] = Path(media["filename"]).name
            item["media"] = media
        else:
            item["media"] = None
        item["quoted"] = message_data(segment.quoted,depth+1) if segment.quoted else None
        data["segments"].append(item)
    return data


def logical_hash(message):
    data = message_data(message)
    def logical(item):
        item.pop("avatar_data",None)
        item.pop("avatar_rel",None)
        for segment in item["segments"]:
            if segment.get("media"):
                for key in ("source","missing","rel_path","data_uri","size"):
                    segment["media"].pop(key,None)
            if segment.get("quoted"):
                logical(segment["quoted"])
    logical(data)
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest()


def _data_uri(value):
    if value is None:
        return None
    if not isinstance(value,str) or not value.startswith(("data:image/png;base64,","data:image/jpeg;base64,",
            "data:image/gif;base64,","data:image/webp;base64,")):
        raise SnapshotError("快照图片编码无效")
    return value


def from_data(data,root,depth=0):
    if depth>8 or not isinstance(data,dict):
        raise SnapshotError("快照消息结构无效")
    if any(type(data.get(key)) is not int for key in ("msg_id","ts")):
        raise SnapshotError("快照消息标识无效")
    try:
        datetime.fromtimestamp(data["ts"])
    except (ValueError,OverflowError,OSError):
        raise SnapshotError("快照时间越界") from None
    if not isinstance(data.get("sender_name"),str) or type(data.get("is_self")) is not bool:
        raise SnapshotError("快照发送者无效")
    if any(data.get(key) is not None and not isinstance(data[key],str) for key in ("sender_uid","sender_uin")):
        raise SnapshotError("快照账号字段无效")
    if any(type(data.get(key,0)) is not int for key in ("outer_type","sub_type")) or type(data.get("is_recall",False)) is not bool:
        raise SnapshotError("快照消息类型无效")
    segments = data.get("segments")
    if not isinstance(segments,list) or len(segments)>4096:
        raise SnapshotError("快照元素无效")
    parsed = []
    for item in segments:
        if not isinstance(item,dict) or not isinstance(item.get("kind"),str) or len(item["kind"])>64:
            raise SnapshotError("快照元素无效")
        kwargs = {f.name:item[f.name] for f in fields(Segment) if f.name in item and f.name not in {"media","quoted"}}
        if kwargs.get("text") is not None and not isinstance(kwargs["text"],str):
            raise SnapshotError("快照正文无效")
        if kwargs.get("forward_meta") is not None and not isinstance(kwargs["forward_meta"],dict):
            raise SnapshotError("快照卡片无效")
        for key in ("face_id","unknown_field"):
            if kwargs.get(key) is not None and type(kwargs[key]) is not int:
                raise SnapshotError("快照元素字段无效")
        media = item.get("media")
        if media is not None:
            if not isinstance(media,dict) or not isinstance(media.get("kind"),str):
                raise SnapshotError("快照媒体无效")
            values = {f.name:media[f.name] for f in fields(MediaRef) if f.name in media and f.name not in {"source","remote_url"}}
            if type(values.get("missing",True)) is not bool or any(values.get(key) is not None and
                    (type(values[key]) is not int or values[key]<0) for key in ("width","height","size","duration_ms")):
                raise SnapshotError("快照媒体字段无效")
            if values.get("filename") is not None and not isinstance(values["filename"],str):
                raise SnapshotError("快照文件名无效")
            _data_uri(values.get("data_uri"))
            rel = values.get("rel_path")
            if rel:
                path = (Path(root)/relative_path(rel)).resolve()
                if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
                    raise SnapshotError("快照媒体缺失或越界")
                values["source"] = path
            kwargs["media"] = MediaRef(**values)
        quoted = item.get("quoted")
        kwargs["quoted"] = from_data(quoted,root,depth+1) if quoted else None
        previews = kwargs.get("forward_previews")
        if previews is not None and (not isinstance(previews,list) or len(previews)>4096 or
                any(not isinstance(pair,(list,tuple)) or len(pair)!=2 or any(not isinstance(v,str) for v in pair) for pair in previews)):
            raise SnapshotError("快照转发摘要无效")
        parsed.append(Segment(**kwargs))
    values = {f.name:data[f.name] for f in fields(Message) if f.name in data and f.name not in {"segments","avatar_rel"}}
    values["avatar_data"] = _data_uri(values.get("avatar_data"))
    return Message(**values,segments=parsed)


def write_record(stream,message):
    record = {"schema_version":1,"message":message_data(message),"fingerprint":logical_hash(message)}
    text = json.dumps(record,ensure_ascii=False,separators=(",",":"))
    if len(text.encode("utf-8"))>MAX_RECORD:
        raise SnapshotError("单条快照过大")
    stream.write(text+"\n")
    return record["fingerprint"]


def records(path,root=None):
    path = Path(path)
    root = Path(root or path.parent)
    with path.open("rb") as stream:
        while line := stream.readline(MAX_RECORD+1):
            yield decode_record(line,root)


def decode_record(line,root):
    if len(line)>MAX_RECORD or not line.endswith(b"\n"):
        raise SnapshotError("快照行过大或被截断")
    try:
        record = json.loads(line)
        if type(record["schema_version"]) is not int or record["schema_version"]!=1:
            raise ValueError
        message = from_data(record["message"],root)
        if logical_hash(message)!=record["fingerprint"]:
            raise ValueError
    except (ValueError,KeyError,TypeError):
        raise SnapshotError("快照消息校验失败") from None
    return message


def copy_file(source,target,cancel=None,*,count_lines=False):
    target.parent.mkdir(parents=True,exist_ok=True)
    lines = 0
    with source.open("rb") as src,target.open("wb") as dst:
        while block := src.read(1024*1024):
            check_cancel(cancel)
            dst.write(block)
            if count_lines:
                lines += block.count(b"\n")
    return lines


def hash_file(path,cancel=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024*1024):
            check_cancel(cancel)
            digest.update(block)
    return digest.hexdigest()


class MediaSnapshot:
    def __init__(self,root,cancel=None):
        self.root,self.cancel,self.cache = Path(root),cancel,{}

    def message(self,message,depth=0):
        if depth>8:
            raise SnapshotError("回复层级过深")
        segments = []
        for segment in message.segments:
            media = segment.media
            if media is not None:
                media = self.media(media)
            quoted = self.message(segment.quoted,depth+1) if segment.quoted else None
            segments.append(replace(segment,media=media,quoted=quoted))
        return replace(message,segments=segments)

    def media(self,media):
        key = str(media.source) if media.source else media.data_uri
        if not key:
            return replace(media,source=None,remote_url=None,rel_path=None,data_uri=None,missing=True)
        if key in self.cache:
            path,relative = self.cache[key]
            return replace(media,source=path,rel_path=relative,remote_url=None,data_uri=None,missing=False)
        created = False
        target = None
        try:
            check_cancel(self.cancel)
            if media.source and media.source.is_file():
                source = media.source
                digest = hash_file(source,self.cancel)
                relative = f"media/{digest}.bin"
                target = self.root/relative
                if not target.exists():
                    created = True
                    copy_file(source,target,self.cancel)
                    if hash_file(target,self.cancel)!=digest:
                        raise SnapshotError("媒体在复制过程中变化")
            elif media.data_uri:
                uri = _data_uri(media.data_uri)
                data = base64.b64decode(uri.split(",",1)[1],validate=True)
                digest = hashlib.sha256(data).hexdigest()
                relative = f"media/{digest}.bin"
                target = self.root/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(data)
            else:
                return replace(media,source=None,rel_path=None,remote_url=None,data_uri=None,missing=True)
            self.cache[key] = (target,relative)
            return replace(media,source=target,rel_path=relative,remote_url=None,data_uri=None,missing=False)
        except (OSError,ValueError):
            if created and target is not None:
                target.unlink(missing_ok=True)
            return replace(media,source=None,rel_path=None,remote_url=None,data_uri=None,missing=True)
