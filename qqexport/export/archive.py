""".qqarchive v1：规范 JSONL、媒体、只读 HTML 和逐文件 SHA-256；可选 AES-GCM。"""
from __future__ import annotations
from array import array
from dataclasses import asdict,replace
from datetime import datetime
import hashlib
import html as html_escape
import json
import os
from pathlib import Path,PurePosixPath
import re
import stat
import struct
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from qqexport import __version__
from qqexport.errors import UserError
from qqexport.export import html as html_writer,orchestrator
from qqexport.export.cancel import check_cancel,ExportCancelled
from qqexport.export.jobs import account_hash
from qqexport.export.model import Chat
from qqexport.export.serde import MediaSnapshot,MAX_RECORD,decode_record,hash_file,records,write_record
from qqexport.export.serde import SnapshotError
from qqexport.export.session import ChatInfo
from qqexport.state import atomic_json
from qqexport.temporary import workspace

MAGIC = b"QQARC1E\x00"
MAX_TOTAL = 32*1024**3
MAX_FILES = 50000


class ArchiveError(UserError):
    pass


def encrypted(path):
    with Path(path).open("rb") as stream:
        return stream.read(len(MAGIC))==MAGIC


def _key(password,salt):
    if not isinstance(password,str) or not password:
        raise ArchiveError("需要归档密码，密码不会保存")
    return Scrypt(salt=salt,length=32,n=2**15,r=8,p=1).derive(password.encode("utf-8"))


def _encrypt(source,target,password,cancel=None):
    if len(password)<8:
        raise ArchiveError("归档密码至少 8 个字符，遗忘后无法恢复")
    salt,nonce = os.urandom(16),os.urandom(12)
    header = MAGIC+salt+nonce
    cipher = Cipher(algorithms.AES(_key(password,salt)),modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(header)
    with source.open("rb") as src,target.open("wb") as dst:
        dst.write(header)
        while block := src.read(1024*1024):
            check_cancel(cancel)
            dst.write(cipher.update(block))
        dst.write(cipher.finalize())
        dst.write(cipher.tag)


def _decrypt(source,target,password,cancel=None):
    size = source.stat().st_size
    if size<52:
        raise ArchiveError("加密归档被截断")
    with source.open("rb") as src,target.open("wb") as dst:
        header = src.read(36)
        if header[:8]!=MAGIC:
            raise ArchiveError("加密归档版本无效")
        src.seek(-16,os.SEEK_END)
        tag = src.read(16)
        cipher = Cipher(algorithms.AES(_key(password,header[8:24])),modes.GCM(header[24:36],tag)).decryptor()
        cipher.authenticate_additional_data(header)
        src.seek(36)
        remaining = size-52
        try:
            while remaining:
                check_cancel(cancel)
                block = src.read(min(1024*1024,remaining))
                if not block:
                    raise ArchiveError("加密归档被截断")
                remaining -= len(block)
                dst.write(cipher.update(block))
            dst.write(cipher.finalize())
        except InvalidTag:
            raise ArchiveError("密码错误或归档已被修改") from None


def _view_message(message):
    segments = []
    for segment in message.segments:
        media = segment.media
        if media and media.rel_path:
            media = replace(media,rel_path="../"+media.rel_path)
        segments.append(replace(segment,media=media,quoted=_view_message(segment.quoted) if segment.quoted else None))
    return replace(message,segments=segments)


def _write_view(root,info,message_file,output,offsets=None):
    class Messages:
        def __len__(self):
            return info.messages
        def __iter__(self):
            return (_view_message(message) for message in records(message_file,root))
        def __getitem__(self,index):
            # 页头只访问首尾时间，不加载正文。
            from types import SimpleNamespace
            return SimpleNamespace(ts=info.first_ts if index==0 else info.last_ts)
    output.parent.mkdir(parents=True,exist_ok=True)
    chat = Chat(info.kind,info.peer,info.title,info.subtitle,[])
    html_writer._write_doc(output,chat,Messages(),datetime.now(),"离线归档")


def create_archive(source,chat_ids,output,*,password=None,cancel=None,overwrite=False):
    output = Path(output).resolve()
    if output.suffix.lower()!=".qqarchive":
        raise ArchiveError("归档文件扩展名应为 .qqarchive")
    if password is not None and (not isinstance(password,str) or not 8<=len(password)<=4096):
        raise ArchiveError("归档密码需 8–4096 个字符，遗忘后无法恢复")
    orchestrator.validate_destination(source,output)
    if output.exists() and not overwrite:
        raise ArchiveError("归档已存在，请另选文件或明确允许覆盖")
    ids = list(dict.fromkeys(chat_ids))
    infos = {info.chat_id:info for info in source.chats()}
    if not ids or any(chat not in infos for chat in ids):
        raise ArchiveError("请选择有效会话")
    check_cancel(cancel)
    output.parent.mkdir(parents=True,exist_ok=True)
    staged = output.with_name(f".qqarchive-{os.getpid()}-{__import__('time').time_ns()}.tmp")
    try:
        with workspace("archive-build") as temp:
            root = Path(temp)/"data"
            root.mkdir()
            copier = MediaSnapshot(root,cancel)
            manifest = {"schema_version":1,"kind":"qqarchive","app_version":__version__,
                "account_sha256":source.meta.get("account_sha256") or account_hash(source.account_uin),
                "created_at":datetime.now().isoformat(timespec="seconds"),"chats":[]}
            for chat in ids:
                check_cancel(cancel)
                token = hashlib.sha256(chat.encode("utf-8")).hexdigest()
                relative = f"messages/{token}.jsonl"
                path = root/relative
                path.parent.mkdir(parents=True,exist_ok=True)
                count,first,last = 0,None,None
                with path.open("w",encoding="utf-8") as stream:
                    for message in source.iter_messages(chat):
                        check_cancel(cancel)
                        if message.is_system:
                            continue
                        message = copier.message(message)
                        write_record(stream,message)
                        count += 1
                        first = message.ts if first is None else first
                        last = message.ts
                info = replace(infos[chat],messages=count,first_ts=first,last_ts=last)
                view = f"view/{token}.html"
                _write_view(root,info,path,root/view)
                manifest["chats"].append({"info":asdict(info),"messages":relative,"view":view})
            atomic_json(root/"manifest.json",manifest)
            links = "".join('<li><a href="'+entry["view"]+'">'+html_escape.escape(entry["info"]["title"])+"</a></li>" for entry in manifest["chats"])
            (root/"index.html").write_text('<!doctype html><meta charset="utf-8"><title>离线归档</title><h1>离线归档</h1><ul>'+links+"</ul>",encoding="utf-8")
            paths = sorted(path for path in root.rglob("*") if path.is_file())
            if len(paths)+1>MAX_FILES or sum(path.stat().st_size for path in paths)>MAX_TOTAL:
                raise ArchiveError("归档超过 v1 文件数量或大小限制")
            (root/"checksums.sha256").write_text("".join(hash_file(path,cancel)+"  "+path.relative_to(root).as_posix()+"\n" for path in paths),encoding="utf-8")
            payload = Path(temp)/"payload.zip" if password is not None else staged
            with zipfile.ZipFile(payload,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        check_cancel(cancel)
                        with path.open("rb") as src,archive.open(path.relative_to(root).as_posix(),"w",force_zip64=True) as dst:
                            while block := src.read(1024*1024):
                                check_cancel(cancel)
                                dst.write(block)
            if password is not None:
                _encrypt(payload,staged,password,cancel)
            check_cancel(cancel)
            if output.exists() and not overwrite:
                raise ArchiveError("目标归档在写入期间已出现，未覆盖")
            os.replace(staged,output)
            return {"chats":len(manifest["chats"]),"messages":sum(e["info"]["messages"] for e in manifest["chats"]),
                    "encrypted":password is not None,"sha256":hash_file(output)}
    finally:
        staged.unlink(missing_ok=True)


def _safe_name(name):
    if not isinstance(name,str) or len(name)>1024 or any(c in name for c in ("\\","\x00","\n","\r",":")):
        raise ArchiveError("归档包含无效路径")
    path = PurePosixPath(name)
    reserved = {"CON","PRN","AUX","NUL"}|{f"COM{i}" for i in range(1,10)}|{f"LPT{i}" for i in range(1,10)}
    if path.is_absolute() or not path.parts or any(p in {"..","."} or p.rstrip(" .")!=p or p.split(".")[0].upper() in reserved for p in path.parts):
        raise ArchiveError("归档包含越界或保留路径")
    return path.as_posix()


def _preflight(path):
    """先限制中央目录规模，避免 ZipFile 在数量检查前分配无界元数据。"""
    with path.open("rb") as stream:
        size = path.stat().st_size
        stream.seek(max(0,size-65557))
        tail = stream.read()
        position = tail.rfind(b"PK\x05\x06")
        if position<0 or len(tail)-position<22:
            raise ArchiveError("归档 ZIP 目录被截断")
        entry = struct.unpack("<4s4H2LH",tail[position:position+22])
        if entry[1] or entry[2]:
            raise ArchiveError("不支持分卷归档")
        count,central = entry[4],entry[5]
        absolute = max(0,size-65557)+position
        if count==65535 or central==0xffffffff or entry[6]==0xffffffff:
            stream.seek(absolute-20)
            locator = struct.unpack("<4sLQL",stream.read(20))
            if locator[0]!=b"PK\x06\x07":
                raise ArchiveError("ZIP64 目录无效")
            stream.seek(locator[2])
            large = struct.unpack("<4sQ2H2L4Q",stream.read(56))
            if large[0]!=b"PK\x06\x06":
                raise ArchiveError("ZIP64 目录无效")
            count,central = large[7],large[8]
        if count>MAX_FILES or central>32*1024*1024:
            raise ArchiveError("归档文件数量或目录大小超过限制")


class ArchiveReader:
    def __init__(self,path,*,password=None,cancel=None,max_total=MAX_TOTAL):
        self.path,self.closed = Path(path).resolve(),False
        self._temp = workspace("archives")
        self.root = Path(self._temp.name)/"data"
        self.root.mkdir()
        self.offsets,self.times,self.ids = {},{},{}
        try:
            payload = self.path
            if payload.stat().st_size>max_total+32*1024*1024:
                raise ArchiveError("归档超过大小限制")
            if encrypted(payload):
                payload = Path(self._temp.name)/"payload.zip"
                _decrypt(self.path,payload,password,cancel)
            _preflight(payload)
            with zipfile.ZipFile(payload) as archive:
                entries = archive.infolist()
                names = set()
                total = 0
                for info in entries:
                    name = _safe_name(info.filename)
                    if name.casefold() in names or stat.S_ISLNK(info.external_attr>>16):
                        raise ArchiveError("归档包含重复路径或链接")
                    names.add(name.casefold())
                    total += info.file_size
                    if total>max_total or info.file_size>max(1024*1024,info.compress_size*1000):
                        raise ArchiveError("归档展开大小或压缩比超过限制")
                checksum = archive.getinfo("checksums.sha256")
                if checksum.file_size>10*1024*1024:
                    raise ArchiveError("归档校验清单过大")
                expected = {}
                for line in archive.read(checksum).decode("utf-8").splitlines():
                    match = re.fullmatch(r"([0-9a-f]{64})  (.+)",line)
                    if not match:
                        raise ArchiveError("归档校验清单无效")
                    name = _safe_name(match[2])
                    if name in expected:
                        raise ArchiveError("归档校验清单重复")
                    expected[name] = match[1]
                files = {info.filename for info in entries if not info.is_dir()}-{"checksums.sha256"}
                if files!=set(expected) or "manifest.json" not in files:
                    raise ArchiveError("归档文件与校验清单不匹配")
                for info in entries:
                    if info.is_dir() or info.filename=="checksums.sha256":
                        continue
                    check_cancel(cancel)
                    target = self.root/Path(info.filename)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    digest = hashlib.sha256()
                    with archive.open(info) as src,target.open("wb") as dst:
                        while block := src.read(1024*1024):
                            check_cancel(cancel)
                            digest.update(block)
                            dst.write(block)
                    if digest.hexdigest()!=expected[info.filename]:
                        raise ArchiveError("归档文件校验失败")
            manifest_path = self.root/"manifest.json"
            if manifest_path.stat().st_size>10*1024*1024:
                raise ArchiveError("归档清单过大")
            self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if self.manifest["schema_version"]!=1 or self.manifest["kind"]!="qqarchive":
                raise ArchiveError("不支持此归档版本")
            if not re.fullmatch(r"[0-9a-f]{64}",self.manifest["account_sha256"]):
                raise ArchiveError("归档账号标识无效")
            self.infos,self.message_files = [],{}
            if not isinstance(self.manifest["chats"],list) or len(self.manifest["chats"])>10000:
                raise ArchiveError("归档会话清单无效")
            for entry in self.manifest["chats"]:
                info = ChatInfo(**entry["info"])
                if (info.kind not in {"c2c","group"} or any(not isinstance(v,str) for v in
                        (info.chat_id,info.peer,info.account,info.title)) or type(info.messages) is not int
                        or info.messages<0 or info.chat_id!=info.kind+":"+info.peer
                        or info.subtitle is not None and not isinstance(info.subtitle,str)):
                    raise ArchiveError("归档会话字段无效")
                relative = _safe_name(entry["messages"])
                if info.chat_id in self.message_files or relative not in files or not relative.startswith("messages/"):
                    raise ArchiveError("归档会话引用无效")
                self.message_files[info.chat_id] = self.root/relative
                offsets,times,ids = array("Q"),array("q"),array("q")
                with self.message_files[info.chat_id].open("rb") as stream:
                    first,last,previous = None,None,None
                    while True:
                        offset = stream.tell()
                        line = stream.readline(MAX_RECORD+1)
                        if not line:
                            break
                        check_cancel(cancel)
                        message = decode_record(line,self.root)
                        key = (message.ts,message.msg_id)
                        if message.is_system or previous is not None and key<previous:
                            raise ArchiveError("归档消息顺序无效")
                        previous = key
                        first = message.ts if first is None else first
                        last = message.ts
                        offsets.append(offset)
                        times.append(message.ts)
                        ids.append(message.msg_id)
                if len(offsets)!=info.messages or (first,last)!=(info.first_ts,info.last_ts):
                    raise ArchiveError("归档消息数量或边界不符")
                self.offsets[info.chat_id],self.times[info.chat_id],self.ids[info.chat_id] = offsets,times,ids
                self.infos.append(info)
        except BaseException as exc:
            self.close()
            if not isinstance(exc,(UserError,ExportCancelled)) and isinstance(exc,
                    (OSError,ValueError,KeyError,TypeError,OverflowError,struct.error,zipfile.BadZipFile)):
                raise ArchiveError("归档损坏、不可读或暂存空间不足") from None
            raise

    def close(self):
        if not self.closed:
            self._temp.cleanup()
            self.closed = True
            self.offsets.clear()
            self.times.clear()
            self.ids.clear()
            if hasattr(self,"infos"):
                self.infos.clear()
                self.message_files.clear()

    retire = close

    def source(self):
        if self.closed:
            raise ArchiveError("归档已关闭")
        return ArchiveSource(self)


class ArchiveSource:
    def __init__(self,reader):
        self.reader = reader
        self.account_uin = reader.manifest["account_sha256"]
        self.msg_db = reader.root/"manifest.json"
        self.meta = {"synthetic":True,"archive":True,"account_sha256":self.account_uin,
                     "display_account":"离线归档","protected_dirs":[reader.root]}
        self.warnings = []

    def chats(self):
        return list(self.reader.infos)

    def count(self,chat):
        return len(self.reader.offsets.get(chat,()))

    def window(self,chat,offset,limit=128):
        if offset<0 or not 1<=limit<=1000:
            raise ArchiveError("归档分页参数无效")
        positions = self.reader.offsets.get(chat,())
        if offset>=len(positions):
            return []
        with self.reader.message_files[chat].open("rb") as stream:
            stream.seek(positions[offset])
            return [(i,decode_record(stream.readline(MAX_RECORD+1),self.reader.root))
                    for i in range(offset,min(offset+limit,len(positions)))]

    def iter_messages(self,chat):
        if chat in self.reader.message_files:
            yield from records(self.reader.message_files[chat],self.reader.root)

    def indices_in_range(self,chat,start=None,end=None):
        return {i for i,ts in enumerate(self.reader.times.get(chat,())) if
                (start is None or ts>=start) and (end is None or ts<=end)}

    def ids_for_indices(self,chat,selected):
        return {self.reader.ids[chat][index] for index in selected}

    def close(self):
        self.reader.close()

    retire = close


class ArchiveRepository:
    def __init__(self,source):
        self.source = source
    def __getattr__(self,name):
        return getattr(self.source,name)
    def close(self):
        pass
