"""同一套 Canvas 图文排版用于预览和勾选，不创建嵌套的原生行窗口。"""
from collections import OrderedDict
from datetime import datetime
import base64
import io
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
import tkinter.font as tkfont
from PIL import Image, ImageTk

from qqexport.classic_row import media_key
from qqexport.export.txt import segment_text
from qqexport.gui_avatars import rounded
from qqexport.qq import avatars

MISSING_IMAGE = "[图片](本机未缓存)"


class MessageRow:
    def __init__(self,canvas,view):
        self.canvas,self.view = canvas,view
        self.tag = f"message-row-{id(self)}"
        self.index,self.message,self._state = -1,None,None
        self.y,self.height = 0,50
        self.photos,self.requested = {},set()
        self.renderer = SimpleNamespace(_avatars={},_sel_wheel=view.wheel)
        self.canvas.tag_bind(self.tag,"<Button-1>",self.click)
        self.canvas.tag_bind(self.tag,"<Button-3>",self.menu)
        self.canvas.tag_bind(self.tag,"<Double-Button-1>",self.open)
        if not hasattr(view,"_row_fonts"):
            view._row_fonts = {size:tkfont.Font(root=canvas,family="Microsoft YaHei UI",size=size)
                               for size in (9,10)}
            view.emoji_photos = OrderedDict()

    def hide(self):
        self.canvas.delete(self.tag)
        self._state = None
        self.photos.clear()
        self.requested.clear()
        self.renderer._avatars.clear()

    def move_to(self,y):
        if y!=self.y:
            self.canvas.move(self.tag,0,y-self.y)
            self.y = y

    def rect(self,x,y,w,h,color,radius=8,outline=""):
        r = min(radius,w/2,h/2)
        return self.canvas.create_polygon(x+r,y,x+w-r,y,x+w,y,x+w,y+r,
            x+w,y+h-r,x+w,y+h,x+w-r,y+h,x+r,y+h,x,y+h,x,y+h-r,x,y+r,x,y,
            smooth=True,splinesteps=20,fill=color,outline=outline,tags=self.tag)

    def text(self,x,y,text,*,width=None,fill="#111111",font=None,anchor="nw"):
        item = self.canvas.create_text(x,y,text=text,anchor=anchor,fill=fill,
            font=font or self.font,width=width or 0,tags=self.tag)
        return item,self.canvas.bbox(item)

    def avatar(self):
        uri = self.message.avatar_data
        if not uri and getattr(self.view,"avatar_uri",None):
            uri = self.view.avatar_uri(self.message)
        if not uri:
            return None
        cache = self.view.avatar_photos
        if uri not in cache:
            try:
                raw = base64.b64decode(uri.split(",",1)[1])
                cache[uri] = ImageTk.PhotoImage(rounded(raw,34),master=self.canvas)
            except (ValueError,OSError,tk.TclError):
                return None
            while len(cache)>128:
                cache.popitem(last=False)
        cache.move_to_end(uri)
        self.renderer._avatars[uri] = cache[uri]
        return cache[uri]

    def emoji(self,face_id):
        source = getattr(self.view,"source",None)
        nt_data = source.meta.get("nt_data") if source else None
        key = str(nt_data),face_id
        cache = self.view.emoji_photos
        if key not in cache:
            photo = None
            path = avatars.emoji_path(Path(nt_data),face_id) if nt_data else None
            if path:
                try:
                    with Image.open(path) as image:
                        image = image.convert("RGBA")
                        image.thumbnail((28,28),Image.Resampling.LANCZOS)
                        photo = ImageTk.PhotoImage(image,master=self.canvas)
                except (OSError,ValueError,tk.TclError):
                    pass
            cache[key] = photo
            while len(cache)>128:
                cache.popitem(last=False)
        cache.move_to_end(key)
        return cache[key]

    def media_photo(self,media):
        if media is None or media.missing:
            return None
        key = media_key(media)
        cache = self.view.image_photos
        if key in cache:
            cache.move_to_end(key)
            if cache[key] is not None:
                self.photos[key] = cache[key]
            return cache[key]
        if key and self.view.image_loader:
            if key not in self.requested:
                self.requested.add(key)
                self.view.image_loader(self.index,media)
            return False  # 明确区分解码中与解码失败
        return None

    def show(self,index,message,width):
        previous = self.view.message(index-1) if index>0 and message else None
        day = datetime.fromtimestamp(message.ts).strftime("%Y-%m-%d") if message else None
        show_day = index==0 or previous is not None and datetime.fromtimestamp(previous.ts).strftime("%Y-%m-%d")!=day
        selecting = self.view.selecting
        selected = selecting and index in self.view.selected
        density = getattr(self.view,"density","normal")
        state = (index,id(message),width,selecting,selected,self.view.highlight==index,show_day,density)
        if state==self._state:
            return False
        self.hide()
        self._state,self.index,self.message = state,index,message
        self.font = self.view._row_fonts[9 if density=="compact" else 10]
        self.line = self.font.metrics("linespace")+3
        self.y = 0
        bg = "#e6f0ff" if selected else "#fff2ae" if self.view.highlight==index else "#ffffff"
        background = self.canvas.create_rectangle(0,0,width,1,fill=bg,outline="",tags=self.tag)
        if message is None:
            self.text(12,10,"正在加载…",fill="#999999")
            self.height = 50
        elif message.is_recall:
            content = next((s.text for s in message.segments if s.text),"撤回了一条消息")
            _,box = self.text(width/2,10,content,width=width-50,fill="#999999",anchor="n")
            self.height = box[3]+12
        else:
            y = 8
            if show_day:
                _,box = self.text(width/2,y,day,anchor="n",fill="#aaaaaa",font=self.view._row_fonts[9])
                y = box[3]+10
            x = 34 if selecting else 8
            if selecting:
                self.canvas.create_oval(9,y+7,25,y+23,fill="#7860c6" if selected else bg,
                                       outline="#7860c6" if selected else "#b9b3c5",width=1.5,tags=self.tag)
                if selected:
                    self.canvas.create_line(13,y+15,16,y+18,22,y+12,fill="white",width=2,
                                            capstyle="round",joinstyle="round",tags=self.tag)
            photo = self.avatar()
            if photo:
                self.canvas.create_image(x,y,image=photo,anchor="nw",tags=self.tag)
            body_x = x+42 if photo else x+6
            _,head = self.text(body_x,y,f"{message.sender_name}  {datetime.fromtimestamp(message.ts):%H:%M:%S}",
                              width=max(100,width-body_x-20),fill="#999999",font=self.view._row_fonts[9])
            body_y = head[3]+5
            body_width = max(100,min(700,width-body_x-20))
            # 原版的绿色己方消息色保留；引用作为气泡里的独立卡片。
            bubble = self.rect(body_x-5,body_y-3,body_width,1,"#95ec69" if message.is_self else bg)
            end,used = self.body(message.segments,body_x+4,body_y+4,body_width-18)
            self.canvas.delete(bubble)
            bubble = self.rect(body_x-5,body_y-3,min(body_width,max(30,used+18)),end-body_y+11,
                               "#95ec69" if message.is_self else bg)
            self.canvas.tag_lower(bubble)
            self.canvas.tag_lower(background)
            self.height = max(y+34,end+14)
        self.canvas.coords(background,0,0,width,self.height)
        return True

    def body(self,segments,x,y,width):
        start_y,cursor,used,line_height = y,x,0,self.line
        def newline():
            nonlocal y,cursor,line_height
            y += line_height
            cursor,line_height = x,self.line
        def write(value,color="#111111"):
            nonlocal cursor,used
            parts = value.split("\n")
            for n,part in enumerate(parts):
                if n:
                    newline()
                while part:
                    available = width-(cursor-x)
                    lo,hi = 0,len(part)
                    while lo<hi:
                        mid = (lo+hi+1)//2
                        if self.font.measure(part[:mid])<=available:
                            lo = mid
                        else:
                            hi = mid-1
                    if not lo:
                        if cursor>x:
                            newline()
                            continue
                        lo = 1
                    chunk,part = part[:lo],part[lo:]
                    self.text(cursor,y,chunk,fill=color)
                    cursor += self.font.measure(chunk)
                    used = max(used,cursor-x)
                    if part:
                        newline()
        for segment in segments:
            if segment.kind=="reply":
                if cursor>x:
                    newline()
                quote = segment.quoted
                name = quote.sender_name if quote else "引用消息"
                content = " ".join(segment_text(s) for s in quote.segments).strip() if quote else ""
                content = content or "引用正文未保存在本机"
                # 引用最多三行，避免超长转发把消息行撑成不可见巨块。
                content = content[:300]+("…" if len(content)>300 else "")
                card_width = min(width,max(160,min(self.font.measure(name),width-24),min(self.font.measure(content),width-24))+24)
                card = self.rect(x,y,card_width,1,"#e9e6ed")
                self.text(x+10,y+6,name,width=card_width-32,fill="#77717e",font=self.view._row_fonts[9])
                self.text(x+card_width-15,y+6,"↑",fill="#99909f",font=self.view._row_fonts[9])
                # Canvas 文本宽度换行，截取三行在排版前完成，避免隐藏内容仍占高度。
                limit = max(8,int((card_width-20)/max(1,self.font.measure("中"))))*3
                content = content[:limit]+("…" if len(content)>limit else "")
                _,box = self.text(x+10,y+6+self.line,content,width=card_width-20,fill="#77717e")
                card_height = box[3]-y+8
                self.canvas.delete(card)
                card = self.rect(x,y,card_width,card_height,"#e9e6ed")
                self.canvas.tag_lower(card)
                used = max(used,card_width)
                y += card_height+8
                cursor = x
                if segment.text:
                    write(segment.text)
                continue
            if segment.kind=="face" and segment.face_id is not None:
                photo = self.emoji(segment.face_id)
                if photo is not None:
                    if cursor+photo.width()>x+width:
                        newline()
                    self.canvas.create_image(cursor,y,image=photo,anchor="nw",tags=self.tag)
                    cursor += photo.width()+3
                    line_height = max(line_height,photo.height()+3)
                    used = max(used,cursor-x)
                    continue
            if segment.kind=="image":
                photo = self.media_photo(segment.media)
                if photo is None:
                    write(MISSING_IMAGE,"#888888")
                else:
                    if cursor>x:
                        newline()
                    if photo is False:
                        w,h = min(180,width),80
                        self.rect(x,y,w,h,"#f5f3f8")
                        self.text(x+10,y+25,"图片加载中…",fill="#999999")
                    else:
                        w,h = photo.width(),photo.height()
                        if w>width:
                            # 解码缩略图上限外，窄窗口显示时另存一份缩放图。
                            raw = self.canvas.tk.call(photo,"data","-format","png")
                            with Image.open(io.BytesIO(raw)) as original:
                                scaled = original.resize((int(width),max(1,int(h*width/w))),Image.Resampling.LANCZOS)
                                photo = ImageTk.PhotoImage(scaled,master=self.canvas)
                            self.photos[(media_key(segment.media),width)] = photo
                            w,h = photo.width(),photo.height()
                        item = self.canvas.create_image(x,y,image=photo,anchor="nw",tags=self.tag)
                        self.canvas.tag_bind(item,"<Button-1>",lambda event,media=segment.media:self.image_click(media))
                    used = max(used,w)
                    y += h+6
                    cursor = x
                continue
            value = segment_text(segment)
            if value:
                write(value)
        return y+(line_height if cursor>x or y==start_y else 0),used

    def measure_height(self):
        return self.height

    def image_ready(self,key,image):
        cache = self.view.image_photos
        cache[key] = ImageTk.PhotoImage(image,master=self.canvas) if image is not None else None
        cache.move_to_end(key)
        while len(cache)>64:
            cache.popitem(last=False)
        self._state = None

    def image_click(self,media):
        if self.view.selecting:
            return self.click(None)
        if self.view.on_open:
            self.view.on_open(media)
        return "break"

    def click(self,_):
        if self.view.selecting and self.message:
            self.view.on_toggle(self.index)
        return "break"

    def open(self,_):
        if not self.view.selecting and self.message and self.view.on_open:
            for segment in self.message.segments:
                if segment.media:
                    self.view.on_open(segment.media)
                    break
        return "break"

    def menu(self,event):
        if not self.message:
            return
        menu = tk.Menu(self.canvas,tearoff=False)
        def copy():
            self.canvas.clipboard_clear()
            self.canvas.clipboard_append("\n".join(segment_text(s) for s in self.message.segments))
        menu.add_command(label="复制消息",command=copy)
        menu.add_command(label="打开本机附件",command=lambda:self.open(None))
        if getattr(self.view,"tools_menu",None):
            menu.add_separator()
            menu.add_cascade(label="更多功能",menu=self.view.tools_menu)
        menu.tk_popup(event.x_root,event.y_root)
