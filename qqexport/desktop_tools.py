"""Electron 内置工具的数据入口；复用既有 P2 实现，任务进度与取消独立于正文线程。"""
from dataclasses import asdict,replace
from pathlib import Path
import threading
import uuid

from qqexport import config
from qqexport.errors import UserError
from qqexport.export import orchestrator
from qqexport.export.archive import ArchiveReader,ArchiveRepository,create_archive
from qqexport.export.cancel import ExportCancelled
from qqexport.export.jobs import ExportJob
from qqexport.library import Library
from qqexport.search import SearchIndex,Query,ResultSource


class DesktopTools:
    def __init__(self,backend):
        self.backend = backend
        self.search = None
        self.jobs,self.active = {},None
        self.lock = threading.RLock()

    @property
    def busy(self):
        return self.active is not None and self.jobs[self.active]["running"]

    def clear_search(self):
        if self.search:
            self.search.close()
            self.search = None

    def index(self):
        if not self.backend.repo:
            raise UserError("请先选择账号或打开归档")
        if self.search is None:
            self.search = SearchIndex()
            self.search.build(self.backend.repo)
        return self.search

    def mount(self,source):
        backend = self.backend
        backend.close()
        backend.source,backend.repo = source,ArchiveRepository(source)
        backend.demo = False
        backend.account = None
        backend.infos = source.chats()
        backend.metas = {}
        from qqexport.export.txt import segment_text
        for info in backend.infos:
            rows = source.window(info.chat_id,max(0,info.messages-1),1)
            if rows:
                last = rows[-1][1]
                backend.metas[info.chat_id] = (" ".join(segment_text(s) for s in last.segments)[:48],last.ts)
        backend.generation += 1
        backend.resources.clear()
        return backend.state()

    def state(self,job=None):
        with self.lock:
            current = self.jobs.get(job or self.active)
            if current is None:
                return None
            return {key:value for key,value in current.items() if key not in {"cancel","export_job","thread"}}

    def start(self,title,work,export_job=None):
        if self.busy:
            raise UserError("已有任务正在运行，请等待或取消后重试")
        identifier = uuid.uuid4().hex
        event = export_job.cancel if export_job else threading.Event()
        record = dict(id=identifier,title=title,running=True,error=None,result=None,progress=None,
                      cancel=event,export_job=export_job)
        with self.lock:
            self.jobs[identifier] = record
            self.active = identifier
            for key in list(self.jobs)[:-8]:
                if not self.jobs[key]["running"]:
                    self.jobs.pop(key)
        generation = self.backend.generation
        def progress(value):
            with self.lock:
                record["progress"] = value
        def run():
            try:
                with self.backend.lock:
                    if generation!=self.backend.generation:
                        raise UserError("资料已更新，请重新开始任务")
                    result = work(event,progress)
                    if event.is_set() and isinstance(result,dict):
                        result = dict(result,message="任务已取消，可继续未完成会话")
                with self.lock:
                    record["result"] = result
            except Exception as exc:
                with self.lock:
                    record["error"] = "已取消" if isinstance(exc,ExportCancelled) else str(exc) if isinstance(exc,UserError) else f"操作失败：{type(exc).__name__}"
            finally:
                with self.lock:
                    record["running"] = False
        thread = threading.Thread(target=run,name="qilu-desktop-task",daemon=True)
        record["thread"] = thread
        thread.start()
        return self.state(identifier)

    def options(self,params):
        formats = params.get("formats",self.backend.preferences.formats)
        if not formats or not isinstance(formats,list) or set(formats)-{"txt","html","pdf"}:
            raise UserError("请至少选择一种有效格式")
        output = Path(params["out"])
        return orchestrator.ExportOptions(formats=set(formats),out_base=output,
            incremental=bool(params.get("incremental")),rebuild=bool(params.get("rebuild")))

    def chat_ids(self,params):
        ids = params.get("chats",[])
        if not ids:
            raise UserError("请至少选择一个会话")
        for chat in ids:
            self.backend.check_chat(chat)
        return list(dict.fromkeys(ids))

    def call(self,action,**params):
        backend = self.backend
        if action=="queueState":
            return self.state(params.get("job"))
        if action=="queueCancel":
            if self.active:
                self.jobs[self.active]["cancel"].set()
            return self.state()
        if self.busy:
            raise UserError("任务正在运行，可在任务进度中取消；完成后再操作")
        if action=="batch":
            if backend.demo:
                raise UserError("演示资料不执行真实导出")
            job = ExportJob(backend.repo.account_uin,self.chat_ids(params),self.options(params),
                            write_manifest=bool(params.get("manifest",True)))
            if params.get("remember"):
                job.checkpoint = config.app_dir()/"jobs"/(job.id+".json")
            return self.start("增量备份" if job.options.incremental else "批量导出",
                lambda cancel,progress:job.run(backend.repo,progress=progress),job)
        if action=="queueResume":
            if not backend.repo:
                raise UserError("请先打开原账号")
            job = ExportJob.load(Path(params["path"]),backend.repo.account_uin)
            job.retry_failed()
            return self.start("恢复导出队列",lambda cancel,progress:job.run(backend.repo,progress=progress),job)
        if action=="queueRetry":
            record = self.jobs.get(params.get("job"))
            job = record.get("export_job") if record else None
            if not job:
                raise UserError("该任务不能重试，请从原入口重新开始")
            job.retry_failed()
            return self.start("继续导出队列",lambda cancel,progress:job.run(backend.repo,progress=progress),job)
        if action=="archiveCreate":
            ids = self.chat_ids(params)
            password = params.get("password") or None
            if password and not 8<=len(password)<=4096:
                raise UserError("归档密码至少 8 个字符")
            def create(cancel,progress):
                result = create_archive(backend.repo,ids,Path(params["path"]),password=password,cancel=cancel)
                return dict(result,message="归档已生成",path=params["path"])
            return self.start("生成归档",create)
        if action in {"archiveOpen","archiveVerify","libraryImport"}:
            password = params.get("password") or None
            def read(cancel,progress):
                reader = ArchiveReader(Path(params["path"]),password=password,cancel=cancel)
                if action=="archiveOpen":
                    return {"state":self.mount(reader.source()),"message":"归档已打开并校验"}
                try:
                    if action=="archiveVerify":
                        return {"message":f"校验通过，共 {len(reader.infos)} 个会话"}
                    library = Library()
                    try:
                        result = library.import_archive(reader,cancel=cancel)
                        return dict(result,message=f"导入完成：新增 {result['added']} 条，重复 {result['duplicates']} 条")
                    finally:
                        library.close()
                finally:
                    reader.close()
            return self.start({"archiveOpen":"打开离线归档","archiveVerify":"校验归档","libraryImport":"导入资料库"}[action],read)
        if action=="libraryAccounts":
            library = Library()
            try:
                return library.accounts()
            finally:
                library.close()
        if action=="libraryOpen":
            library = Library()
            try:
                source = library.source(params["owner"])
            finally:
                library.close()
            return {"state":self.mount(source)}
        if action=="libraryClear":
            if backend.source and backend.source.meta.get("library"):
                raise UserError("请先切回 QQ 账号或关闭当前资料库，再清空")
            Library.clear()
            return {"message":"本机资料库已清空"}
        if action=="cacheClear":
            from qqexport.thumbnail_cache import ThumbnailCache
            from qqexport.viewer_images import clear
            ThumbnailCache().clear()
            retained = clear()
            return {"message":"图片缓存已清理"+("，正在使用的副本已保留" if retained else "")}
        if action=="missing":
            chat = params["chat"]
            backend.check_chat(chat)
            offset = max(0,int(params.get("offset",0)))
            rows,total = [],0
            for start in range(0,backend.repo.count(chat),256):
                for index,message in backend.repo.window(chat,start,256):
                    for segment in message.segments:
                        media = segment.media
                        if media and (media.missing or not media.data_uri and (not media.source or not media.source.is_file())):
                            if offset<=total<offset+200:
                                rows.append(dict(index=index,id=str(message.msg_id),ts=message.ts,kind=media.kind,name=media.filename or ""))
                            total += 1
                if total>=offset+200:
                    return dict(rows=rows,more=True)
            return dict(rows=rows,more=False)
        if action=="settingsGet":
            return asdict(backend.preferences)
        if action=="settingsSave":
            values = {key:params[key] for key in ("formats","density","thumbnail_mb","last_out") if key in params}
            value = replace(backend.preferences,**values)
            value.save()
            backend.preferences = value
            from qqexport.thumbnail_cache import ThumbnailCache
            cache = ThumbnailCache(value.thumbnail_mb*1024**2)
            with cache.lease():
                cache._trim()
            return asdict(value)
        if action=="searchClear":
            self.clear_search()
            return {"message":"搜索索引已清除"}
        if action=="searchHistory":
            import json
            from qqexport.state import atomic_json
            path = config.app_dir()/"search-history.json"
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
                rows = [item for item in rows if isinstance(item,str) and len(item)<=300][:30] if isinstance(rows,list) else []
            except (OSError,ValueError):
                rows = []
            if params.get("clear"):
                rows = []
                atomic_json(path,rows)
            text = params.get("text")
            if isinstance(text,str) and 0<len(text)<=300:
                rows = [text]+[item for item in rows if item!=text]
                rows = rows[:30]
                atomic_json(path,rows)
            return rows
        if action=="searchExport":
            query = Query(**params["query"])
            options = self.options(params)
            def export(cancel,progress):
                source = ResultSource(backend.repo,self.index().iter_hits(query))
                result = orchestrator.export_chats(source,[info.chat_id for info in source.chats()],replace(options,cancel=cancel))
                return {"message":f"已导出 {result.messages_total} 条匹配消息"}
            return self.start("导出全部搜索结果",export)
        if action=="schedule":
            from qqexport.scheduled import prepare
            chats = self.chat_ids(params)
            path = prepare(backend.repo,chats,self.options(params).formats,params["out"],params["path"],params.get("clock","20:00"))
            return {"message":"已生成默认禁用的任务 XML 和计划 JSON，请在 Windows 任务计划程序中核对后导入启用。","path":str(path)}
        raise UserError("不支持的工具操作")
