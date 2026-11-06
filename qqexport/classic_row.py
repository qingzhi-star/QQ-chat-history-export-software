"""0.1.0 的原始渲染规则，装入有界可视行；不另设计聊天外观。"""
from datetime import datetime
import tkinter as tk
from qqexport.gui import App
from qqexport.export.txt import segment_text


def media_key(media):
    return str(media.source) if media.source else media.data_uri


class _Renderer:
    # 与 0.1.0 共用日期、绿色己方气泡、引用、撤回、图片与圆形头像绘制。
    _tag_config = App._tag_config
    _append_message = App._append_message
    _insert_segments = App._insert_segments
    _emoji_photo = App._emoji_photo
    _row_text = App._row_text
    _build_selection_rows = App._build_selection_rows
    _apply_selection_visuals = App._apply_selection_visuals
    _draw_check = App._draw_check
    _bind_row = App._bind_row
    _sel_wheel = App._sel_wheel

    def __init__(self,row):
        self.row,self.text = row,row.normal
        self._avatars,self._emoji,self.msg_spans = {},{},{}
        self._img_seq,self.export_mode = 0,False
        self.sel_inner = row.selection_frame
        self.sel_canvas = row.view.canvas
        self.preview,self.sel_rows,self.selected_msgs = [],[],set()
        self._tag_config()

    @property
    def source(self):
        return getattr(self.row.view,"source",None)

    def _image_photo(self,media):
        return self.row.media_photo(media)

    def _avatar_photo(self,data_uri):
        if not data_uri and self.row.message and getattr(self.row.view,"avatar_uri",None):
            data_uri = self.row.view.avatar_uri(self.row.message)
        cache = self.row.view.avatar_photos
        if data_uri in cache:
            cache.move_to_end(data_uri)
            self._avatars[data_uri] = cache[data_uri]
            return cache[data_uri]
        photo = App._avatar_photo(self,data_uri)
        if photo is not None:
            cache[data_uri] = photo
            while len(cache)>128:
                cache.popitem(last=False)
        return photo

    def _image_click(self,index,media):
        if not self.row.view.selecting and self.row.view.on_open:
            self.row.view.on_open(media)

    def _toggle_check(self,index):
        self.row.view.on_toggle(self.row.index)


class MessageRow(tk.Frame):
    def __init__(self,parent,view):
        super().__init__(parent,bg="#ffffff")
        self.view,self.index,self.message,self.photo = view,-1,None,None
        self.photos,self.requested = {},set()
        self._state,self._mode = None,None
        self._measured_height = None
        self._text_width = None
        self.normal = tk.Text(self,wrap="word",state="disabled",height=1,width=1,
            font=("Microsoft YaHei UI",10),padx=6,pady=0,spacing1=1,spacing3=1,
            selectbackground="#2f7cf6",selectforeground="#ffffff",inactiveselectbackground="#b9d5ff",
            insertwidth=0,cursor="arrow",takefocus=0,bd=0,highlightthickness=0)
        self._image_bindings = []
        self._tag_bind = self.normal.tag_bind
        self.normal.tag_bind = self._bind_image_tag
        self.selection_frame = tk.Frame(self,bg="#ffffff")
        self.renderer = _Renderer(self)
        self._selection_key = None
        self._selection_shape = None
        self.loading = tk.PhotoImage(master=self,width=1,height=1)
        self.bind_widgets((self,self.normal,self.selection_frame))
        self.normal.bind("<Configure>",self._text_resized)

    def _text_resized(self,event):
        if event.width!=self._text_width:
            self._text_width = event.width
            self._measured_height = None
            self.view._geometry_dirty = True
            self.view.schedule()

    def bind_widgets(self,widgets):
        for widget in widgets:
            widget.bind("<Button-1>",self.click)
            widget.bind("<Double-Button-1>",self.open)
            widget.bind("<MouseWheel>",self.view.wheel)
            widget.bind("<Button-3>",self.menu)

    def _bind_image_tag(self,tag,sequence=None,func=None,add=None):
        binding = self._tag_bind(tag,sequence,func,add)
        if func and binding:
            self._image_bindings.append((tag,sequence,binding))
        return binding

    def _clear_text(self):
        self.normal.configure(state="normal")
        tags = set()
        for tag,sequence,binding in self._image_bindings:
            self.normal.tag_unbind(tag,sequence,binding)
            tags.add(tag)
        for tag in tags:
            self.normal.tag_delete(tag)
        self._image_bindings.clear()
        self.normal.delete("1.0","end")

    def media_photo(self,media):
        if media is None or media.missing:
            return None
        key = media_key(media)
        if key in self.photos:
            return self.photos[key]
        cache = self.view.image_photos
        if key in cache:
            cache.move_to_end(key)
            self.photos[key] = self.photo = cache[key]
            return cache[key]
        if key and self.view.image_loader:
            if key not in self.requested:
                self.requested.add(key)
                self.view.image_loader(self.index,media)
            return self.loading
        return None

    def show(self,index,message,width):
        previous = self.view.message(index-1) if index>0 and message and not self.view.selecting else None
        day = datetime.fromtimestamp(message.ts).date() if message else None
        show_day = index==0 or previous is not None and datetime.fromtimestamp(previous.ts).date()!=day
        selected = self.view.selecting and index in self.view.selected
        density = getattr(self.view,"density","normal")
        state = (index,id(message),width,self.view.selecting,selected,self.view.highlight==index,show_day,density)
        if self._state==state:
            return False
        self._state = state
        self._measured_height = None
        changed = self.index!=index or self.message is not message
        self.index,self.message = index,message
        if changed:
            self._clear_text()
            if not self.view.selecting or message is None:
                for child in self.selection_frame.winfo_children():
                    child.destroy()
                self.renderer.sel_rows = []
                self._selection_shape = None
            self.renderer._sel_photos = []
            self._selection_key = None
            self.photo = None
            self.photos.clear()
            self.requested.clear()
            self.renderer._avatars.clear()
            self.renderer._emoji.clear()
        if self._mode!=self.view.selecting:
            self.normal.pack_forget()
            self.selection_frame.pack_forget()
            if self.view.selecting:
                self.pack_propagate(True)
                self.selection_frame.pack(fill="x")
            else:
                self.pack_propagate(False)
                self.normal.pack(fill="both",expand=True,padx=(0,2))
            self._mode = self.view.selecting
        if self.view.selecting:
            self._show_selection(message,width,selected,density)
        else:
            self.normal.pack_configure(pady=(4 if index==0 else 0,0))
            self.normal.configure(state="normal",font=("Microsoft YaHei UI",9 if density=="compact" else 10))
            self._clear_text()
            self.renderer._render_day = None if show_day else str(day)
            if message:
                self.renderer._append_message(message)
            else:
                self.normal.insert("end","正在加载…","meta")
            if self.view.highlight==index:
                self.normal.tag_add("search_hit","1.0","end")
                self.normal.tag_configure("search_hit",background="#fff2ae")
            self.normal.configure(state="disabled")
            self.normal.yview_moveto(0)
        return True

    def _show_selection(self,message,width,selected,density):
        selection_changed = bool(self.renderer.selected_msgs)!=selected
        self.renderer.selected_msgs = {0} if selected else set()
        key = (id(message),tuple(self.photos))
        if self._selection_key!=key:
            self.renderer.preview = [message] if message else []
            text = self.renderer._row_text(message) if message else ""
            photos = []
            for segment in message.segments if message else ():
                if segment.kind=="image":
                    photo = self.media_photo(segment.media)
                    if photo is not None:
                        photos.append(photo)
            shape = (bool(text),len(photos))
            if not self.renderer.sel_rows or self._selection_shape!=shape or message is None:
                # 原函数创建原版控件，之后同结构消息复用这些控件。
                self.renderer._build_selection_rows()
                self._selection_shape = shape
            else:
                widgets = self.renderer.sel_rows[0]["bg"]
                avatar = self.renderer._avatar_photo(message.avatar_data)
                widgets[2].configure(image=avatar or "")
                widgets[4].configure(text=f"{message.sender_name}  {datetime.fromtimestamp(message.ts):%H:%M:%S}")
                image_start = 5
                if text:
                    widgets[5].configure(text=text)
                    image_start = 6
                for widget,photo in zip(widgets[image_start:],photos):
                    widget.configure(image=photo)
                self.renderer._sel_photos = photos
                if selection_changed:
                    self.renderer._apply_selection_visuals()
            self._selection_key = key
        elif selection_changed:
            self.renderer._apply_selection_visuals()

    def measure_height(self):
        if self._measured_height is not None:
            return self._measured_height
        if self.view.selecting:
            height = self.winfo_reqheight()
        else:
            pixels = self.normal.count("1.0","end-1c","update","ypixels")
            height = max(20,pixels if isinstance(pixels,int) else pixels[0] if pixels else 20)+(4 if self.index==0 else 0)
            self.configure(height=height)
        self._measured_height = height
        return height

    def image_ready(self,key,image):
        from PIL import ImageTk
        cache = self.view.image_photos
        self.photo = cache.get(key)
        if self.photo is None:
            self.photo = ImageTk.PhotoImage(image,master=self)
            cache[key] = self.photo
            while len(cache)>64:
                cache.popitem(last=False)
        else:
            cache.move_to_end(key)
        self.photos[key] = self.photo
        self._state = None
        self._measured_height = None

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

    def menu(self,event):
        if not self.message:
            return
        menu = tk.Menu(self,tearoff=False)
        def copy():
            self.clipboard_clear()
            self.clipboard_append("\n".join(segment_text(s) for s in self.message.segments))
        menu.add_command(label="复制消息",command=copy)
        menu.add_command(label="打开本机附件",command=lambda:self.open(None))
        if getattr(self.view,"tools_menu",None):
            menu.add_separator()
            menu.add_cascade(label="更多功能",menu=self.view.tools_menu)
        menu.tk_popup(event.x_root,event.y_root)
