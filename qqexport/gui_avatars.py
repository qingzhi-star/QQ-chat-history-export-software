"""原版头像获取入口的异步适配及圆角裁剪；不获取聊天图片。"""
import base64
from collections import OrderedDict
from functools import lru_cache
import io
import os
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageChops
from qqexport.qq import avatars
from qqexport.tasks import TaskRunner


def rounded(data,size):
    with Image.open(io.BytesIO(data)) as original:
        image = original.convert("RGBA")
        side = min(image.size)
        left,top = (image.width-side)//2,(image.height-side)//2
        image = image.crop((left,top,left+side,top+side)).resize((size,size),Image.Resampling.LANCZOS)
    mask = Image.new("L",(size,size),0)
    ImageDraw.Draw(mask).rounded_rectangle((0,0,size-1,size-1),radius=max(1,size//5),fill=255)
    image.putalpha(ImageChops.multiply(image.getchannel("A"),mask))
    return image


@lru_cache(maxsize=128)
def placeholder(name,size=44):
    colors = ["#5b8ff9","#61c9a8","#f6bd16","#e8684a","#9270ca","#6dc8ec"]
    image = Image.new("RGBA",(size,size),(0,0,0,0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0,0,size-1,size-1),radius=max(1,size//5),fill=colors[sum(map(ord,name))%len(colors)])
    try:
        font = ImageFont.truetype(str(Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"/"msyh.ttc"),size//2)
    except OSError:
        font = ImageFont.load_default()
    text = (name or "?")[:1]
    box = draw.textbbox((0,0),text,font=font)
    draw.text(((size-(box[2]-box[0]))/2-box[0],(size-(box[3]-box[1]))/2-box[1]),text,font=font,fill="white")
    stream = io.BytesIO()
    image.save(stream,format="PNG")
    return stream.getvalue()


def data_uri(data):
    mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    return "data:"+mime+";base64,"+base64.b64encode(data).decode("ascii")


class GuiAvatars:
    def __init__(self,app):
        self.app = app
        self.tasks = TaskRunner(app.root,workers=4)
        self.cache,self.pending = OrderedDict(),set()
        self.sidebar = {}
        self.online = False

    def reset(self,source,infos):
        self.online = bool(source.meta.get("account") and not source.meta.get("synthetic"))
        self.sidebar = {}
        for index,info in enumerate(infos):
            key = ("group" if info.kind=="group" else "user",str(info.account))
            self.sidebar.setdefault(key,[]).append(index)
            row = self.app.row_widgets[index] if index<len(self.app.row_widgets) else None
            if row is None:
                continue
            if self.cache.get(key):
                if self.app.avatar_data.get(info.chat_id)!=self.cache[key][0]:
                    self._show_sidebar(key,self.cache[key][0])
            self.request(key)

    def message_key(self,message):
        uin = message.sender_uin
        if not uin and message.is_self and self.app.source and self.app.source.meta.get("account"):
            uin = self.app.source.meta["account"].uin
        return ("user",str(uin or ""))

    def message_uri(self,message):
        if message.avatar_data:
            return message.avatar_data
        key = self.message_key(message)
        self.request(key)
        cached = self.cache.get(key)
        if cached:
            self.cache.move_to_end(key)
            return cached[1]
        return data_uri(placeholder(message.sender_name,34))

    def request(self,key):
        if not self.online or not key[1].isdigit():
            return
        gen = self.app._source_gen
        if key in self.cache or (gen,key) in self.pending or self.tasks.closed:
            return
        self.pending.add((gen,key))
        def work():
            data = avatars.fetch_group_avatar(key[1]) if key[0]=="group" else avatars.fetch_avatar(key[1])
            if data:
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
            return data
        def ready(data):
            self.pending.discard((gen,key))
            self.cache[key] = (data,data_uri(data)) if data else None
            self.cache.move_to_end(key)
            while len(self.cache)>256:
                self.cache.popitem(last=False)
            if not self.app._closed and gen==self.app._source_gen:
                if data:
                    self._show_sidebar(key,data)
                self.app.virtual.avatar_ready(key)
        self.tasks.submit(work,ready,lambda _:ready(None))

    def _show_sidebar(self,key,data):
        for index in self.sidebar.get(key,()):
            if index>=len(self.app.infos) or index>=len(self.app.row_widgets):
                continue
            info = self.app.infos[index]
            row = self.app.row_widgets[index]
            if row is None:
                continue
            self.app.avatar_data[info.chat_id] = data
            photo = self.app._row_avatar(info)
            row["photo"] = photo
            row["labels"][0].configure(image=photo)
