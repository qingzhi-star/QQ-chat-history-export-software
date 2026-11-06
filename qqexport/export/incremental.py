"""可校验的整会话增量备份；TXT/HTML 追加、PDF 重建、全目录原子发布。"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import json
import os
import re
from pathlib import Path
import shutil
import time
import uuid

from qqexport.errors import UserError
from qqexport.export import orchestrator,txt,html,pdf
from qqexport.export.cancel import check_cancel
from qqexport.export.model import Chat
from qqexport.export.report import ExportReport
from qqexport.export.serde import MediaSnapshot,copy_file,hash_file,logical_hash,relative_path,write_record
from qqexport.export.jobs import account_hash
from qqexport.repository import MessageRepository,Cursor
from qqexport.state import atomic_json


class IncrementalConflict(UserError):
    pass


@contextmanager
def target_lock(root,chat,account):
    key = __import__("hashlib").sha256((account+chat).encode("utf-8")).hexdigest()
    path = root/(".inc-lock-"+key+".json")
    descriptor = None
    for _ in range(3):
        try:
            descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            break
        except FileExistsError:
            try:
                if path.stat().st_size>4096:
                    raise ValueError
                previous = json.loads(path.read_text(encoding="utf-8"))
                pid = previous["pid"]
                if type(pid) is not int or pid<=0 or orchestrator._pid_alive(pid):
                    raise IncrementalConflict("已有增量任务，或遗留锁状态不明；请确认任务结束后重试")
                path.unlink()
            except FileNotFoundError:
                continue
            except IncrementalConflict:
                raise
            except (ValueError,KeyError):
                raise IncrementalConflict("增量锁损坏；请确认没有运行中的任务后处理遗留锁") from None
    if descriptor is None:
        raise IncrementalConflict("无法取得增量导出锁")
    try:
        os.write(descriptor,json.dumps({"pid":os.getpid(),"id":uuid.uuid4().hex}).encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def read_manifest(folder):
    path = folder/"manifest.json"
    try:
        if path.stat().st_size>10*1024*1024:
            raise ValueError
        data = json.loads(path.read_text(encoding="utf-8"))
        if type(data["schema_version"]) is not int or data["schema_version"]!=1 or data["kind"]!="qqexport-incremental":
            raise ValueError
        formats = data["formats"]
        if (not isinstance(formats,list) or not formats or set(formats)-{"txt","html","pdf"}
                or data["encoding"] not in {"utf-8","gbk"} or not isinstance(data["files"],list)
                or not isinstance(data["chat_id"],str) or not re.fullmatch(r"[0-9a-f]{64}",data["account_sha256"])
                or not re.fullmatch(r"[0-9a-f]{64}",data["last_fingerprint"])):
            raise ValueError
        if not re.fullmatch(r"[0-9a-f]{64}",data["first_fingerprint"]):
            raise ValueError
        stats = data["stats"]
        if (any(type(stats[k]) is not int or stats[k]<0 for k in ("total","ok","degraded"))
                or not stats["total"] or stats["total"]!=stats["ok"]+stats["degraded"]
                or type(data["source_count"]) is not int or data["source_count"]<stats["total"]
                or not isinstance(stats["kinds"],dict)):
            raise ValueError
        if any(not isinstance(key,str) or type(value) is not int or value<0 for key,value in stats["kinds"].items()):
            raise ValueError
        for key in ("first","last","source_first"):
            if not isinstance(data[key],list) or len(data[key])!=2 or any(type(v) is not int for v in data[key]):
                raise ValueError
        names = set()
        for entry in data["files"]:
            relative_path(entry["path"])
            if (entry["path"] in names or type(entry["size"]) is not int or entry["size"]<0
                    or not re.fullmatch(r"[0-9a-f]{64}",entry["sha256"])):
                raise ValueError
            names.add(entry["path"])
        required = {"messages.jsonl"}|{"chat."+fmt for fmt in formats}
        if "pdf" in formats and "html" not in formats:
            required.add("_state/chat.html")
        if not required.issubset(names):
            raise ValueError
        return data
    except (OSError,ValueError,TypeError,KeyError):
        raise IncrementalConflict("导出无有效增量清单，可能是旧格式或已损坏；请另选目录全量重建") from None


def verify_files(folder,manifest,cancel=None):
    try:
        for entry in manifest["files"]:
            path = (folder/relative_path(entry["path"])).resolve()
            if (not path.is_relative_to(folder.resolve()) or not path.is_file() or
                    path.stat().st_size!=entry["size"] or hash_file(path,cancel)!=entry["sha256"]):
                raise ValueError
    except (OSError,ValueError,TypeError,KeyError):
        raise IncrementalConflict("旧导出缺失、截断或校验不符；已停止增量，请另选目录全量重建") from None


def _files(folder,cancel):
    return [{"path":path.relative_to(folder).as_posix(),"size":path.stat().st_size,
             "sha256":hash_file(path,cancel)} for path in sorted(folder.rglob("*"))
            if path.is_file() and path.name!="manifest.json"]


def _stats(messages,previous=None):
    stats = dict(previous or {"total":0,"ok":0,"degraded":0,"kinds":{}})
    stats["kinds"] = dict(stats.get("kinds",{}))
    for message in messages:
        stats["total"] += 1
        degraded = not message.segments or any(s.kind=="unknown" for s in message.segments)
        stats["degraded" if degraded else "ok"] += 1
        for segment in message.segments:
            stats["kinds"][segment.kind] = stats["kinds"].get(segment.kind,0)+1
    return stats


def _report(source,final,stats,added,now):
    return ExportReport(started_at=now.isoformat(),finished_at=datetime.now().isoformat(),
        uin=source.account_uin,chats_requested=1,chats_exported=1,messages_total=stats["total"],
        messages_ok=stats["ok"],messages_degraded=stats["degraded"],messages_added=added,
        segments_by_kind=stats["kinds"],exported_dirs=[str(final)])


def export_chat(repo,chat_id,opts):
    if opts.message_ids is not None or opts.start_ts is not None or opts.end_ts is not None:
        raise IncrementalConflict("增量备份仅支持整会话，范围/选择导出请使用普通导出")
    formats = set(opts.formats or {opts.format})
    if not formats or formats-{"txt","html","pdf"}:
        raise IncrementalConflict("增量格式无效")
    info = next((info for info in repo.chats() if info.chat_id==chat_id),None)
    if info is None:
        raise IncrementalConflict("未找到会话")
    root = opts.out_base/orchestrator.ROOT_FOLDER
    orchestrator.validate_destination(repo,root)
    if repo.count(chat_id)==0 and not root.exists():
        return ExportReport(chats_requested=1,messages_added=0,warnings=["空会话，已跳过"])
    root.mkdir(parents=True,exist_ok=True)
    orchestrator._cleanup_stale(root)
    owner = account_hash(repo.account_uin)
    now = opts.now or datetime.now()
    with target_lock(root,chat_id,owner):
        final = root/orchestrator.folder_name(info)
        if not final.exists():
            candidates = []
            for folder in root.iterdir():
                if folder.is_dir() and not folder.is_symlink() and not folder.name.startswith(".") and (folder/"manifest.json").is_file():
                    try:
                        value = read_manifest(folder)
                        if value["chat_id"]==chat_id and value["account_sha256"]==owner:
                            candidates.append(folder)
                    except (UserError,KeyError):
                        continue
            if len(candidates)>1:
                raise IncrementalConflict("找到多个同会话增量目录，请使用独立输出目录")
            if candidates:
                final = candidates[0]
        if final.is_symlink() or not final.resolve().is_relative_to(root.resolve()):
            raise IncrementalConflict("增量目标不可为外部链接")
        orchestrator.validate_destination(repo,final)
        old = None
        cursor = None
        if final.exists() and not opts.rebuild:
            old = read_manifest(final)
            if (old.get("account_sha256")!=owner or old.get("chat_id")!=chat_id or
                    old.get("formats")!=sorted(formats) or old.get("encoding")!=opts.encoding or
                    old.get("timezone")!=list(time.tzname)):
                raise IncrementalConflict("账号、会话、格式、编码或时区与旧备份不符；请全量重建")
            verify_files(final,old,opts.cancel)
            try:
                ts,mid = old["last"]
                count,first,message = repo.prefix_info(chat_id,ts,mid)
                if (count!=old["source_count"] or first!=old["source_first"] or message is None
                        or logical_hash(message)!=old["last_fingerprint"]):
                    raise ValueError
                rows = repo.iter_messages(chat_id)
                try:
                    first_message = next(rows,None)
                finally:
                    rows.close()
                if (first_message is None or [first_message.ts,first_message.msg_id]!=old["first"]
                        or logical_hash(first_message)!=old["first_fingerprint"]):
                    raise ValueError
                cursor = Cursor(ts,mid,2**63-1)
            except (ValueError,TypeError,KeyError):
                raise IncrementalConflict("源消息数量或边界已变化，不能安全追加；请全量重建") from None
        new_messages = []
        for message in repo.iter_messages(chat_id,after=cursor):
            check_cancel(opts.cancel)
            new_messages.append(message)
        if old is not None and not new_messages:
            return _report(repo,final,old["stats"],0,now)
        if not new_messages:
            return ExportReport(chats_requested=1,messages_added=0,warnings=["空会话，已跳过"])
        work = root/f".tmp-{os.getpid()}-{time.time_ns()}"
        work.mkdir()
        try:
            if old:
                for entry in old["files"]:
                    relative = relative_path(entry["path"])
                    source,target = final/relative,work/relative
                    target.parent.mkdir(parents=True,exist_ok=True)
                    if relative.parts[0]=="media":
                        try:
                            os.link(source,target)  # 仅链接已有独立备份，从不链接 QQ 源。
                        except OSError:
                            copy_file(source,target,opts.cancel)
                    else:
                        lines = copy_file(source,target,opts.cancel,count_lines=relative.as_posix()=="messages.jsonl")
                        if relative.as_posix()=="messages.jsonl" and lines!=old["stats"]["total"]:
                            raise IncrementalConflict("旧消息快照条数不符，请全量重建")
            elif opts.rebuild and (final/"manifest.json").is_file():
                copy_file(final/"manifest.json",work/"previous-manifest.json",opts.cancel)
            copier = MediaSnapshot(work,opts.cancel)
            new_messages = [copier.message(message) for message in new_messages]
            stats = _stats(new_messages,old["stats"] if old else None)
            first = old["first"] if old else [new_messages[0].ts,new_messages[0].msg_id]
            last = [new_messages[-1].ts,new_messages[-1].msg_id]
            source_count,source_first,_ = repo.prefix_info(chat_id,*last)
            with (work/"messages.jsonl").open("a",encoding="utf-8") as stream:
                for message in new_messages:
                    check_cancel(opts.cancel)
                    write_record(stream,message)
                stream.flush()
                os.fsync(stream.fileno())
            chat = Chat(info.kind,info.peer,info.title,info.subtitle,new_messages)
            if "txt" in formats:
                if old:
                    delta = work/".delta.txt"
                    txt.write_txt(chat,delta,txt.TxtOptions(encoding=opts.encoding,bom=False,cancel=opts.cancel))
                    with (work/"chat.txt").open("ab") as dst,delta.open("rb") as src:
                        while block := src.read(1024*1024):
                            check_cancel(opts.cancel)
                            dst.write(block)
                    delta.unlink()
                else:
                    txt.write_txt(chat,work/"chat.txt",txt.TxtOptions(encoding=opts.encoding,cancel=opts.cancel))
            if formats&{"html","pdf"}:
                rendered = orchestrator._inline_images(new_messages,cancel=opts.cancel)
                chat = Chat(info.kind,info.peer,info.title,info.subtitle,rendered)
                relative = Path("chat.html") if "html" in formats else Path("_state/chat.html")
                destination = work/relative
                destination.parent.mkdir(parents=True,exist_ok=True)
                options = html.HtmlOptions(now=now,account_uin=repo.account_uin,shard_size=10**9,
                    link_files="html" in formats,cancel=opts.cancel)
                if old:
                    html.append_html(final/relative,destination,chat,rendered,count=stats["total"],
                        first_ts=first[0],last_ts=last[0],previous_ts=old["last"][0],opts=options)
                else:
                    html.write_html(chat,destination.parent,options)
                if "pdf" in formats:
                    if stats["total"]>30000 and not opts.allow_large_pdf:
                        raise IncrementalConflict("PDF 超过 30000 条；请明确允许大 PDF 或改用其他格式")
                    result = pdf.write_pdf(destination,work/"chat.pdf",edge=opts.edge,cancel=opts.cancel)
                    if not result.ok:
                        raise IncrementalConflict(result.error or "PDF 生成失败")
            manifest = {"schema_version":1,"kind":"qqexport-incremental","chat_id":chat_id,
                "chat":asdict(info),"account_sha256":owner,"formats":sorted(formats),"encoding":opts.encoding,
                "timezone":list(time.tzname),"first":first,"last":last,"source_count":source_count,
                "source_first":source_first,"last_fingerprint":logical_hash(new_messages[-1]),"stats":stats,
                "first_fingerprint":old["first_fingerprint"] if old else logical_hash(new_messages[0]),
                "files":_files(work,opts.cancel)}
            atomic_json(work/"manifest.json",manifest)
            if old:
                verify_files(final,old,opts.cancel)  # 发布前再查旧产物，避免并发编辑被悄悄覆盖。
            check_cancel(opts.cancel)
            orchestrator._commit_dir(work,final)
            return _report(repo,final,stats,len(new_messages),now)
        finally:
            if work.exists():
                shutil.rmtree(work)


def export_incremental(source,chat_ids,opts):
    owned = not isinstance(source,MessageRepository)
    if owned and not hasattr(source,"ctx"):
        raise IncrementalConflict("增量只支持本机整会话数据源")
    repo = MessageRepository(source) if owned else source
    try:
        reports = [export_chat(repo,chat,opts) for chat in chat_ids]
        result = ExportReport(uin=source.account_uin,chats_requested=len(chat_ids),messages_added=0)
        for report in reports:
            for key in ("chats_exported","messages_total","messages_ok","messages_degraded","messages_added"):
                setattr(result,key,getattr(result,key)+getattr(report,key))
            result.exported_dirs.extend(report.exported_dirs)
            result.warnings.extend(report.warnings)
            for kind,count in report.segments_by_kind.items():
                result.segments_by_kind[kind] = result.segments_by_kind.get(kind,0)+count
        return result
    finally:
        if owned:
            repo.close()
