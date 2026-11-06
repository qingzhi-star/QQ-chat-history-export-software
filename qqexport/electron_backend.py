"""Electron 本机子进程协议：只经匿名管道传输 JSON，不监听网络端口。"""
from __future__ import annotations
import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
from datetime import datetime

from qqexport import __version__, config
from qqexport.account_access import account_key, AccountAccessError
from qqexport.account_panel import profile_name, read_identity
from qqexport.core import discover
from qqexport.core.decrypt import DatabaseBusyError, DecryptError
from qqexport.errors import UserError
from qqexport.export import orchestrator
from qqexport.export.model import Message, Segment, MediaRef
from qqexport.export.session import ExportSource, ChatInfo
from qqexport.export.txt import segment_text
from qqexport.qq import avatars
from qqexport.repository import MessageRepository


def image_uri(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64,"+base64.b64encode(stream.getvalue()).decode("ascii")


class DesktopBackend:
    def __init__(self, *, demo=False):
        self.lock = threading.RLock()
        self.source = self.repo = self.account = None
        self.infos, self.metas, self.accounts = [], {}, {}
        self.resources, self.portraits = OrderedDict(), OrderedDict()
        self.generation, self.stamp = 0, None
        self._refreshing = False
        self._refresh_retry_at, self._refresh_failures = 0, 0
        self.demo = demo
        self.preferences = config.Config.load()
        from qqexport.desktop_tools import DesktopTools
        self.tools = DesktopTools(self)

    def close(self):
        with self.lock:
            self.tools.clear_search()
            if self.repo:
                self.repo.close()
                self.repo = None
            if self.source:
                self.source.close()
                self.source = None

    def register(self, kind, value, identity):
        scope = 0 if kind=="account-avatar" else self.generation
        token = hashlib.sha256(f"{scope}:{kind}:{identity}".encode()).hexdigest()
        self.resources[token] = kind, value
        self.resources.move_to_end(token)
        while len(self.resources)>8192:
            self.resources.popitem(last=False)
        return token

    def portrait(self, uin, *, group=False):
        if not uin or not str(uin).isdigit():
            return None
        return self.register("group" if group else "avatar",str(uin),str(uin))

    def boot(self):
        if self.demo:
            self._demo()
            return dict(self.state(),accounts=[dict(uin="12345",name="绮录演示",avatar=None),
                dict(uin="11111111",name="另一个演示账号",avatar=None)],version=__version__)
        self.accounts = {account.uin:account for account in discover.discover_accounts(
            explicit=Path(self.preferences.data_dir) if self.preferences.data_dir else None)}
        identities = []
        for account in self.accounts.values():
            identity = read_identity(account,include_avatar=False)
            avatar = None
            if identity.avatar:
                from PIL import Image
                with Image.open(io.BytesIO(identity.avatar)) as image:
                    image.thumbnail((80,80))
                    avatar = image_uri(image.convert("RGBA"))
            identities.append(dict(uin=account.uin,name=identity.name,avatar=avatar))
        current = self.preferences.last_account
        if current not in self.accounts and len(self.accounts)==1:
            current = next(iter(self.accounts))
        state = self.switch(current) if current in self.accounts else self.state()
        for identity in identities:
            identity["avatar"] = identity["avatar"] or self.register("account-avatar",identity["uin"],identity["uin"])
        return dict(state,accounts=identities,version=__version__)

    @staticmethod
    def account_stamp(account):
        values = []
        for name in ("nt_msg.db", "profile_info.db", "group_info.db"):
            for suffix in ("", "-wal"):
                path = account.db_dir/(name+suffix)
                try:
                    stat = path.stat()
                    values.append((stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns))
                except OSError:
                    values.append(None)
        return tuple(values)

    def switch(self, uin):
        if self.demo:
            if uin not in {"12345","11111111"}:
                raise UserError("账号不存在")
            self._demo(uin)
            return self.state()
        account = self.accounts.get(uin)
        if account is None:
            raise UserError("请先在本机 QQ 登录此账号，再重新检测")
        if self.account==account and self.stamp==self.account_stamp(account):
            return self.state()
        # 显式点击账号后自动获取、验证并保存口令；失败保留旧会话。
        key = account_key(account,allow_scan=True)
        try:
            self._open(account,key)
        except DatabaseBusyError:
            raise UserError("QQ 正在更新本机记录，自动重试后仍暂时无法读取，请稍后再选此账号；当前会话已保留。") from None
        except DecryptError:
            raise UserError("未能读取所选账号的本机记录，请确认数据库完整，必要时登录 QQ 后重试；当前会话已保留。") from None
        self.preferences.last_account = uin
        self.preferences.save()
        return self.state()

    def _open(self,account,key):
        for attempt in range(3):
            stamp = self.account_stamp(account)
            try:
                candidate = self._prepare(account,key,self.source.media_index if self.source and self.account==account else None)
                break
            except DatabaseBusyError:
                if attempt==2:
                    raise
                time.sleep(.2*(attempt+1))
        self._install(account,candidate,stamp)

    @staticmethod
    def _prepare(account,key,media_index):
        source = ExportSource.open(account,key,media_index=media_index)
        repo = None
        try:
            repo = MessageRepository(source)
            infos,metas = repo.sidebar()
            return source,repo,infos,metas
        except BaseException:
            if repo is not None:
                repo.close()
            source.close()
            raise

    def _install(self,account,candidate,stamp):
        source,repo,infos,metas = candidate
        self.close()
        self.source,self.repo,self.account = source,repo,account
        self.infos,self.metas,self.stamp = infos,metas,stamp
        self.generation += 1
        self._refresh_retry_at, self._refresh_failures = 0, 0
        for token,(kind,_) in list(self.resources.items()):
            if kind!="account-avatar":
                del self.resources[token]

    def state(self):
        if self.demo:
            uin,name = self.demo_uin,"绮录演示"
        elif self.source:
            uin = None if self.source.meta.get("display_account") else self.source.account_uin
            name = (self.source.meta.get("display_account") or
                    profile_name(self.source.contacts,uin))
        else:
            uin,name = None,"选择账号"
        chats = []
        for info in self.infos:
            preview,ts = self.metas.get(info.chat_id,("",info.last_ts))
            chats.append(dict(id=info.chat_id,title=info.title,preview=preview,ts=ts or 0,count=info.messages,
                avatar=self.portrait(info.account,group=info.kind=="group")))
        chats.sort(key=lambda item:(-item["ts"],item["id"]))
        return dict(generation=self.generation,account=dict(uin=uin,name=name,avatar=self.portrait(uin)),
                    chats=chats,formats=self.preferences.formats,density=self.preferences.density,out=self.preferences.last_out or str(Path.home()/"Documents"))

    def refresh(self):
        # 耗时的复制/解密/索引不占用活动会话锁，打开聊天不再排在刷新后面。
        with self.lock:
            if (not self.account or self._refreshing or self.tools.busy
                    or time.monotonic()<self._refresh_retry_at):
                return None
            account,generation = self.account,self.generation
            stamp = self.account_stamp(account)
            if stamp==self.stamp:
                return None
            index = self.source.media_index
            self._refreshing = True
        try:
            candidate = self._prepare(account,account_key(account),index)
            with self.lock:
                if self.account!=account or self.generation!=generation or self.tools.busy:
                    candidate[1].close()
                    candidate[0].close()
                    return None
                self._install(account,candidate,stamp)
                return self.state()
        except DatabaseBusyError:
            with self.lock:
                if self.account==account and self.generation==generation:
                    self._refresh_failures = min(self._refresh_failures+1,4)
                    self._refresh_retry_at = time.monotonic()+min(2**self._refresh_failures,30)
            return None
        except DecryptError:
            with self.lock:
                if self.account!=account or self.generation!=generation:
                    return None
                self._refresh_retry_at = time.monotonic()+30
            raise UserError("本机记录暂时无法读取，当前聊天仍可浏览和导出；请重新选择账号重试。") from None
        finally:
            with self.lock:
                self._refreshing = False

    def check_chat(self,chat):
        if chat not in {info.chat_id for info in self.infos}:
            raise UserError("会话已更新，请重新选择")

    def message(self,index,message,depth=0):
        segments = []
        for segment in message.segments:
            item = dict(kind=segment.kind,text=segment.text or "")
            if segment.kind=="reply":
                item["quoted"] = self.message(-1,segment.quoted,depth+1) if segment.quoted and depth<4 else None
            elif segment.kind=="face":
                item["resource"] = self.register("emoji",segment.face_id,str(segment.face_id)) if segment.face_id is not None else None
            elif segment.media:
                media = segment.media
                item["filename"] = media.filename or (media.source.name if media.source else "附件")
                item["missing"] = media.missing
                item["resource"] = None if media.missing else self.register("media",media,str(media.source or media.data_uri))
                item["width"],item["height"] = media.width,media.height
            else:
                item["text"] = segment_text(segment)
            segments.append(item)
        return dict(index=index,id=str(message.msg_id),ts=message.ts,name=message.sender_name,
            self=message.is_self,recall=message.is_recall,avatar=message.avatar_data or self.portrait(message.sender_uin),segments=segments)

    def messages(self,chat,offset=0,size=128):
        self.check_chat(chat)
        if type(offset) is not int or offset<0 or type(size) is not int or not 1<=size<=256:
            raise UserError("消息分页参数无效")
        if self.demo:
            count = 100000
            rows = [(i,self.demo_message(i)) for i in range(offset,min(offset+size,count))]
        else:
            count = self.repo.count(chat)
            rows = self.repo.window(chat,offset,size)
        output = []
        previous_day = None
        if offset and not self.demo:
            previous = self.repo.window(chat,max(0,offset-1),1)
            if previous:
                previous_day = datetime.fromtimestamp(previous[-1][1].ts).strftime("%Y-%m-%d")
        elif offset and self.demo:
            previous_day = datetime.fromtimestamp(99+offset).strftime("%Y-%m-%d")
        for index,message in rows:
            item = self.message(index,message)
            day = datetime.fromtimestamp(message.ts).strftime("%Y-%m-%d")
            item["day"] = day if day!=previous_day else None
            previous_day = day
            output.append(item)
        return dict(generation=self.generation,offset=offset,size=size,count=count,messages=output)

    def media(self,token):
        resource = self.resources.get(token)
        if not resource:
            return None
        kind,value = resource
        from PIL import Image
        try:
            if kind in {"avatar","account-avatar","group"}:
                key = kind,value
                if key not in self.portraits:
                    raw = None if self.demo else (avatars.fetch_group_avatar(value) if kind=="group" else avatars.fetch_avatar(value))
                    self.portraits[key] = raw
                    while len(self.portraits)>256:
                        self.portraits.popitem(last=False)
                raw = self.portraits[key]
                if raw:
                    with Image.open(io.BytesIO(raw)) as bitmap:
                        bitmap.thumbnail((80,80))
                        return image_uri(bitmap.convert("RGBA"))
            elif kind=="emoji":
                path = avatars.emoji_path(Path(self.source.meta["nt_data"]),value) if self.source and self.source.meta.get("nt_data") else None
                if self.demo:
                    return self.demo_emoji
                if path:
                    with Image.open(path) as bitmap:
                        bitmap.thumbnail((56,56))
                        return image_uri(bitmap.convert("RGBA"))
            elif kind=="media" and value.kind=="image":
                from qqexport.thumbnail_cache import ThumbnailCache
                bitmap = ThumbnailCache(self.preferences.thumbnail_mb*1024**2).load(value)
                return image_uri(bitmap) if bitmap else None
        except (OSError,ValueError):
            pass
        return None

    def open_media(self,token):
        resource = self.resources.get(token)
        if not resource or resource[0]!="media":
            raise UserError("附件未缓存，请在 QQ 中下载后重试")
        media = resource[1]
        if media.kind=="image":
            from qqexport.viewer_images import prepare
            os.startfile(prepare(media))
        elif media.source and media.source.is_file():
            os.startfile(media.source)
        else:
            raise UserError("附件未缓存，请在 QQ 中下载后重试")
        return True

    def export(self,chat,formats,out,selected=None,all_messages=False,excluded=None,start=None,end=None):
        self.check_chat(chat)
        if not isinstance(formats,list) or not formats or not set(formats)<={"txt","html","pdf"}:
            raise UserError("请选择导出格式")
        if self.demo:
            raise UserError("演示资料不执行真实导出")
        count = self.repo.count(chat)
        indices = set(self.repo.indices_in_range(chat,start,end)) if all_messages else set(selected or [])
        indices.difference_update(excluded or [])
        if any(type(i) is not int or not 0<=i<count for i in indices) or not indices:
            raise UserError("请至少选择一条消息")
        info = next(info for info in self.infos if info.chat_id==chat)
        options = orchestrator.ExportOptions(formats=set(formats),out_base=Path(out),
            message_ids=self.repo.ids_for_indices(chat,indices),start_ts=start,end_ts=end)
        report = orchestrator.export_chats(self.repo,[info.chat_id],options)
        self.preferences.formats,self.preferences.last_out = formats,out
        self.preferences.save()
        return dict(messages=report.messages_total,failed=report.messages_failed,pdf_error=report.pdf_error,
                    directories=report.exported_dirs)

    def range(self,chat,start=None,end=None):
        self.check_chat(chat)
        if self.demo:
            return [i for i in range(100000) if (start is None or 100+i>=start) and (end is None or 100+i<=end)]
        return sorted(self.repo.indices_in_range(chat,start,end))

    def search(self,query,chat=None,mode="contains",sender=None,kind=None,start=None,end=None,offset=0):
        if not isinstance(query,str) or len(query)>300 or not query and not any((sender,kind,start,end)):
            raise UserError("请输入搜索内容")
        if self.demo:
            return [dict(chat=self.infos[0].chat_id,index=42,name="绮录演示",text="找到演示消息",ts=100+42)]
        from qqexport.search import Query
        index = self.tools.index()
        return [dict(chat=hit.chat_id,index=hit.position,
            name=hit.sender,text=hit.summary,ts=hit.ts) for hit in index.find(
                Query(text=query,chat_id=chat,mode=mode,sender=sender,kind=kind,start=start,end=end),limit=100,offset=offset)]

    def _demo(self,uin="12345"):
        from PIL import Image,ImageDraw
        self.demo_uin = uin
        self.generation += 1
        self.infos = [ChatInfo(f"group:synthetic-{i}","group","12345","12345",
            "绮录演示会话" if i==0 else f"演示会话 {i}",None,100000,100,200000-i) for i in range(200)]
        self.metas = {info.chat_id:("文字、图片、表情与引用",info.last_ts) for info in self.infos}
        image = Image.new("RGB",(240,150),"#d8c9f6")
        ImageDraw.Draw(image).rounded_rectangle((35,35,205,115),radius=18,fill="#7860c6")
        self.demo_picture = image_uri(image)
        emoji = Image.new("RGBA",(56,56))
        draw = ImageDraw.Draw(emoji)
        draw.ellipse((2,2,54,54),fill="#ffcf5b")
        draw.ellipse((15,17,21,24),fill="#604622")
        draw.ellipse((35,17,41,24),fill="#604622")
        draw.arc((15,22,42,43),0,180,fill="#604622",width=3)
        self.demo_emoji = image_uri(emoji)

    def demo_message(self,index):
        segments = [Segment("text",f"这是第 {index+1} 条演示消息。绮录，把对话收好。")]
        if index%7==0:
            segments = [Segment("reply",quoted=Message(0,100,None,None,"引用者",False,[Segment("text","今晚几点见？")])),Segment("text","准备两点出发。")]
        if index%11==0:
            segments += [Segment("face",face_id=1),Segment("face",face_id=1)]
        if index%13==0:
            segments += [Segment("image",media=MediaRef("image",data_uri=self.demo_picture,missing=False,width=240,height=150))]
        if index%17==0:
            segments += [Segment("image",media=MediaRef("image",missing=True))]
        return Message(index,100+index,None,None,"绮录演示",index%3==0,segments)

    def dispatch(self,method,params):
        methods = {"boot":self.boot,"switch":self.switch,"refresh":self.refresh,"messages":self.messages,
                   "media":self.media,"openMedia":self.open_media,"export":self.export,"search":self.search,"range":self.range,
                   "tool":self.tools.call}
        if method not in methods or not isinstance(params,dict):
            raise UserError("不支持的操作")
        if method=="media":
            return self.media(**params)
        if method=="refresh":
            return self.refresh(**params)
        if method=="tool" and params.get("action") in {"queueState","queueCancel"}:
            return self.tools.call(**params)
        if self.tools.busy:
            if method=="refresh":
                return None
            if method in {"switch","boot"}:
                raise UserError("任务正在运行，请等待或取消后切换账号")
        with self.lock:
            return methods[method](**params)


def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if stream and hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8",errors="replace")
    backend = DesktopBackend(demo=os.environ.get("QILU_DEMO")=="1")
    write_lock = threading.Lock()
    def send(message):
        with write_lock:
            sys.stdout.write(json.dumps(message,ensure_ascii=False,separators=(",",":"))+"\n")
            sys.stdout.flush()
    from qqexport.electron_pdf import PdfBridge
    from qqexport.export import pdf
    printer = PdfBridge(send)
    if os.environ.get("QILU_ELECTRON_PDF")=="1":
        pdf.desktop_printer = printer
    def respond(request):
        try:
            value = backend.dispatch(request.get("method"),request.get("params",{}))
            response = dict(id=request.get("id"),result=value)
        except Exception as exc:
            text = exc.user_message if isinstance(exc,AccountAccessError) else str(exc) if isinstance(exc,(UserError,discover.DiscoveryError,config.ConfigError)) else f"操作失败：{type(exc).__name__}"
            response = dict(id=request.get("id"),error=text)
        send(response)
    try:
        with (ThreadPoolExecutor(max_workers=4,thread_name_prefix="qilu") as pool,
              ThreadPoolExecutor(max_workers=4,thread_name_prefix="qilu-media") as media_pool):
            for line in sys.stdin:
                if len(line)>8*1024*1024:
                    continue
                try:
                    request = json.loads(line)
                    if isinstance(request,dict):
                        if request.get("event")=="pdfResult":
                            printer.complete(request)
                            continue
                        if request.get("method")=="tool" and request.get("params",{}).get("action") in {"queueState","queueCancel"}:
                            respond(request)
                        else:
                            target = media_pool if request.get("method")=="media" else pool
                            target.submit(respond,request)
                except ValueError:
                    pass
    finally:
        if backend.tools.busy:
            backend.tools.call("queueCancel")
            backend.tools.jobs[backend.tools.active]["thread"].join(timeout=4)
        backend.close()


if __name__=="__main__":
    main()
