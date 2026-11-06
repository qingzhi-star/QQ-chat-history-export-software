"""P2 导出队列：失败隔离、协作取消、原子检查点与同账号恢复。"""
from __future__ import annotations
from dataclasses import asdict,dataclass,field,replace
from datetime import datetime
import hashlib
import json
import re
from pathlib import Path
import threading
import uuid

from qqexport.export import orchestrator
from qqexport.export.cancel import ExportCancelled,check_cancel
from qqexport.state import atomic_json
from qqexport.errors import UserError


class JobError(UserError):
    pass


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()


def account_hash(account):
    return hashlib.sha256(str(account).encode("utf-8")).hexdigest()


@dataclass
class ChatTask:
    chat_id: str
    status: str = "pending"
    attempts: int = 0
    messages: int = 0
    messages_added: int | None = None
    error: str | None = None
    files: list[dict] = field(default_factory=list)


class ExportJob:
    def __init__(self, account, chat_ids, options, *, checkpoint=None, write_manifest=False):
        self.id = uuid.uuid4().hex
        self.account = account_hash(account)
        self.tasks = [ChatTask(chat) for chat in dict.fromkeys(chat_ids)]
        self.cancel = threading.Event()
        self.options = replace(options, formats=set(options.formats or {options.format}),
            message_ids=set(options.message_ids) if options.message_ids is not None else None,
            out_base=options.out_base.resolve(), now=options.now or datetime.now(),cancel=self.cancel,
            require_all_formats=True)
        if self.options.formats-{"txt","html","pdf"} or not self.options.formats:
            raise JobError("导出格式无效")
        if options.message_ids is not None and len(self.tasks)!=1:
            raise JobError("消息 ID 选择仅限单会话")
        self.checkpoint = Path(checkpoint) if checkpoint else None
        self.write_manifest = write_manifest
        self.running = False
        self.last_report = None
        self.lock = threading.RLock()

    @property
    def root(self):
        return self.options.out_base/orchestrator.ROOT_FOLDER

    def state(self, *, details=True):
        with self.lock:
            tasks = [asdict(task) for task in self.tasks]
            if not details:
                for task in tasks:
                    task.pop("files")
            opts = self.options
            return {"schema_version":1,"id":self.id,"account_sha256":self.account,
                "write_manifest":self.write_manifest,"tasks":tasks,"options":{
                    "formats":sorted(opts.formats),"out_base":str(opts.out_base),"encoding":opts.encoding,
                    "start_ts":opts.start_ts,"end_ts":opts.end_ts,"now":opts.now.isoformat(),
                    "allow_large_pdf":opts.allow_large_pdf,
                    "incremental":opts.incremental,"rebuild":opts.rebuild,
                    "message_ids":sorted(opts.message_ids) if opts.message_ids is not None else None}}

    def _record(self,progress):
        if self.checkpoint:
            atomic_json(self.checkpoint,self.state())
        if progress:
            progress(self.state(details=False))

    def _files(self,directories):
        files = []
        for directory in directories:
            for path in sorted(Path(directory).rglob("*")):
                if path.is_file():
                    files.append({"path":path.relative_to(self.root).as_posix(),
                                  "sha256":file_hash(path),"size":path.stat().st_size})
        return files

    def verify_completed(self):
        for task in self.tasks:
            if task.status!="done":
                continue
            if not task.files:
                raise JobError("已完成任务缺少校验记录，请手动全量重建")
            for entry in task.files:
                relative = Path(entry["path"])
                path = (self.root/relative).resolve()
                if (relative.is_absolute() or relative.drive or ".." in relative.parts
                        or not path.is_relative_to(self.root.resolve()) or not path.is_file()
                        or path.stat().st_size!=entry["size"] or file_hash(path)!=entry["sha256"]):
                    raise JobError("已完成产物缺失或被修改，停止恢复；请另选目录全量重建")

    def retry_failed(self):
        if self.running:
            raise JobError("任务仍在运行")
        self.cancel.clear()
        for task in self.tasks:
            if task.status in {"failed","cancelled"}:
                task.status,task.error = "pending",None

    def run(self, source, progress=None):
        if account_hash(source.account_uin)!=self.account:
            raise JobError("队列属于另一账号，请先切换到原账号")
        try:
            orchestrator.validate_destination(source,self.root)
        except ValueError as exc:
            raise JobError(str(exc)) from None
        with self.lock:
            if self.running:
                raise JobError("队列已经运行")
            self.running = True
        try:
            self.verify_completed()
            infos = {info.chat_id:info for info in source.chats()}
            self._record(progress)
            for task in self.tasks:
                if self.cancel.is_set():
                    break
                if task.status!="pending":
                    continue
                task.status,task.error = "running",None
                task.attempts += 1
                self._record(progress)
                try:
                    if task.chat_id not in infos:
                        raise JobError("未找到会话")
                    rows = source.iter_messages(task.chat_id)
                    found = False
                    try:
                        for msg in rows:
                            check_cancel(self.cancel)
                            if (not msg.is_system and
                                (self.options.start_ts is None or msg.ts>=self.options.start_ts) and
                                (self.options.end_ts is None or msg.ts<=self.options.end_ts) and
                                (self.options.message_ids is None or msg.msg_id in self.options.message_ids)):
                                found = True
                                break
                    finally:
                        if hasattr(rows,"close"):
                            rows.close()
                    if not found and not self.options.incremental:
                        task.status = "skipped"
                    else:
                        report = orchestrator.export_chats(source,[task.chat_id],self.options)
                        self.last_report = report
                        task.messages = report.messages_total
                        task.messages_added = report.messages_added
                        if self.options.incremental and report.chats_exported==0 and report.messages_added==0 and "空会话，已跳过" in report.warnings:
                            task.status = "skipped"
                            self._record(progress)
                            continue
                        if report.chats_exported!=1 or report.pdf_error:
                            task.error = report.pdf_error or "导出失败，请检查输出目录和磁盘空间"
                            raise JobError("会话导出未完整成功")
                        task.files = self._files(report.exported_dirs)
                        task.status = "done"
                except ExportCancelled:
                    task.status,task.error = "cancelled","已取消"
                except Exception as exc:
                    task.status,task.error = "failed",task.error or (str(exc) if isinstance(exc,UserError) else type(exc).__name__)
                self._record(progress)
                if self.cancel.is_set():
                    break
            if self.write_manifest:
                atomic_json(self.root/"manifest.json",self.state())
            return self.state(details=False)
        finally:
            with self.lock:
                self.running = False

    @classmethod
    def load(cls,path,account):
        path = Path(path)
        try:
            if path.stat().st_size>10*1024*1024:
                raise ValueError
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["schema_version"]!=1 or data["account_sha256"]!=account_hash(account):
                raise JobError("队列版本或账号不符，原文件未修改")
            options = data["options"]
            if (not isinstance(options["formats"],list) or not isinstance(options["out_base"],str)
                    or not Path(options["out_base"]).is_absolute() or options["encoding"] not in {"utf-8","gbk"}
                    or type(options["allow_large_pdf"]) is not bool or type(data["write_manifest"]) is not bool
                    or not isinstance(data["id"],str) or not re.fullmatch(r"[0-9a-f]{32}",data["id"])):
                raise ValueError
            if any(v is not None and type(v) is not int for v in (options["start_ts"],options["end_ts"])):
                raise ValueError
            if options["message_ids"] is not None and (not isinstance(options["message_ids"],list)
                    or any(type(value) is not int for value in options["message_ids"])):
                raise ValueError
            opts = orchestrator.ExportOptions(formats=set(options["formats"]),out_base=Path(options["out_base"]),
                encoding=options["encoding"],start_ts=options["start_ts"],end_ts=options["end_ts"],
                message_ids=set(options["message_ids"]) if options["message_ids"] is not None else None,
                now=datetime.fromisoformat(options["now"]),allow_large_pdf=options["allow_large_pdf"])
            opts.incremental,opts.rebuild = options.get("incremental",False),options.get("rebuild",False)
            if type(opts.incremental) is not bool or type(opts.rebuild) is not bool:
                raise ValueError
            tasks = data["tasks"]
            if not isinstance(tasks,list) or len(tasks)>10000:
                raise ValueError
            job = cls(account,[t["chat_id"] for t in tasks],opts,checkpoint=path,
                      write_manifest=data["write_manifest"])
            if len(job.tasks)!=len(tasks):
                raise ValueError
            job.id = data["id"]
            for task,record in zip(job.tasks,tasks):
                status = record["status"]
                if status not in {"pending","running","done","failed","cancelled","skipped"}:
                    raise ValueError
                task.status = "pending" if status in {"running","cancelled"} else status
                task.attempts,task.messages,task.error = record["attempts"],record["messages"],record["error"]
                task.messages_added = record.get("messages_added")
                if task.messages_added is not None and (type(task.messages_added) is not int or task.messages_added<0):
                    raise ValueError
                task.files = record["files"]
                if not isinstance(task.files,list) or not isinstance(task.chat_id,str):
                    raise ValueError
                if any(type(v) is not int or v<0 for v in (task.attempts,task.messages)):
                    raise ValueError
                for entry in task.files:
                    if (not isinstance(entry,dict) or not isinstance(entry["path"],str)
                            or type(entry["size"]) is not int or entry["size"]<0
                            or not re.fullmatch(r"[0-9a-f]{64}",entry["sha256"])):
                        raise ValueError
            job.verify_completed()
            return job
        except JobError:
            raise
        except (ValueError,TypeError,KeyError,OSError):
            raise JobError("队列记录损坏或不可读，原文件未修改") from None
