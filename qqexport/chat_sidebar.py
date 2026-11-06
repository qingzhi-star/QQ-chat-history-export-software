"""只复用可见会话行；行控件由旧 gui.py 原函数创建。"""
from qqexport.virtual_list import HeightIndex


class ChatSidebar:
    def __init__(self,app):
        self.app,self.canvas = app,app.canvas
        self.heights,self.chats = HeightIndex(0),[]
        self.rows,self.free = {},[]
        self.pending,self.drawing,self.again = False,False,False
        self.remainder = 0
        self.dirty = True
        self.canvas.delete(app._win)
        app.inner.unbind("<Configure>")
        self.canvas.configure(yscrollcommand=self.scrolled,yscrollincrement=1)
        app.chat_scrollbar.configure(command=self.scroll)
        self.canvas.bind("<Configure>",lambda _:self.schedule())
        self.canvas.bind("<MouseWheel>",self.wheel)

    def anchor(self):
        pixel = max(0,self.canvas.canvasy(0))
        index = self.heights.locate(pixel)
        return index,int(pixel-self.heights.prefix(index))

    def reset(self,preserve):
        self.dirty = True
        index,offset = self.anchor()
        chat = self.chats[index] if self.chats else None
        top = self.canvas.canvasy(0)<=0
        old = dict(zip(self.chats,self.heights.values)) if preserve else {}
        for row,item in self.rows.values():
            self.canvas.delete(item)
            self.free.append(row)
        self.rows.clear()
        self.chats = [info.chat_id for info in self.app.infos]
        self.heights = HeightIndex(len(self.chats))
        for position,chat_id in enumerate(self.chats):
            if chat_id in old:
                self.heights.set(position,old[chat_id])
        self.app.row_widgets = [None]*len(self.chats)
        if not preserve:
            self.app._row_photos.clear()
            for row in self.free:
                row["chat_id"] = None
        self.region()
        if not preserve or top or chat not in self.chats:
            self.canvas.yview_moveto(0)
        else:
            index = self.chats.index(chat)
            self.canvas.yview_moveto((self.heights.prefix(index)+offset)/self.total())
        self.schedule()

    def total(self):
        return max(1,self.heights.prefix(len(self.chats)),self.canvas.winfo_height())

    def region(self):
        region = (0,0,max(1,self.canvas.winfo_width()),self.total())
        if getattr(self,"scrollregion",None)!=region:
            self.scrollregion = region
            self.canvas.configure(scrollregion=region)

    def schedule(self):
        if self.drawing:
            self.again = True
        elif not self.pending:
            self.pending = True
            self.canvas.after_idle(self.draw)

    def scrolled(self,first,last):
        self.app.chat_scrollbar.set(first,last)
        # yscrollcommand 在绘制更改 scrollregion 后也会调用，只有位置变化才需重画。
        if getattr(self,"last_view",None)!=(first,last):
            self.last_view = first,last
            self.schedule()

    def scroll(self,*args):
        if len(args)==3 and args[0]=="scroll" and args[2]=="units":
            self.canvas.yview_scroll(int(args[1])*24,"units")
        else:
            self.canvas.yview(*args)
        self.schedule()

    def wheel(self,event):
        pixels = -event.delta*72/120+self.remainder
        movement = int(pixels)
        self.remainder = pixels-movement
        first,last = self.canvas.yview()
        if movement<0 and first>0 or movement>0 and last<1:
            self.canvas.yview_scroll(movement,"units")
            self.schedule()
        return "break"

    def new_row(self,index,info):
        # 临时把原行构造器挂在 Canvas 下；其字体、头像、间距和标签完全沿用旧源码。
        inner,rows = self.app.inner,self.app.row_widgets
        key = ("group" if info.kind=="group" else "user",str(info.account))
        cached = self.app.gui_avatars.cache.get(key)
        if cached:
            self.app.avatar_data[info.chat_id] = cached[0]
        self.app.inner,self.app.row_widgets = self.canvas,[]
        try:
            self.app._make_row(index,info)
            row = self.app.row_widgets[0]
            row["frame"].pack_forget()
        finally:
            self.app.inner,self.app.row_widgets = inner,rows
        row["chat_id"] = info.chat_id
        row["photo"] = self.app._row_photos[info.chat_id]
        row["frame"].bind("<Configure>",lambda _,row=row:self.row_sized(row))
        return row

    def row_sized(self,row):
        height = row["frame"].winfo_reqheight()
        if row.get("requested_height")!=height:
            row["requested_height"] = height
            self.dirty = True
            self.schedule()

    def show(self,row,index,info):
        key = ("group" if info.kind=="group" else "user",str(info.account))
        changed = row.get("chat_id")!=info.chat_id
        if changed:
            previous = row.get("chat_id")
            self.app._row_photos.pop(previous,None)
            self.app.avatar_data.pop(previous,None)
            row["chat_id"] = info.chat_id
            cached = self.app.gui_avatars.cache.get(key)
            if cached:
                self.app.avatar_data[info.chat_id] = cached[0]
            row["photo"] = self.app._row_avatar(info)
            row["labels"][0].configure(image=row["photo"])
        self.app.gui_avatars.request(key)
        row["index"] = index
        preview,ts = self.app.chat_meta.get(info.chat_id,("",info.last_ts))
        content = info.chat_id,info.title,preview,ts
        if row.get("content")!=content:
            for label,value in zip(row["labels"][1:],(info.title,self.app._fmt_time(ts),preview or "（无消息）")):
                label.configure(text=value)
            row["content"] = content

    def draw(self):
        self.pending,self.drawing,self.again = False,True,False
        try:
            self.render()
        finally:
            self.drawing = False
        if self.again:
            self.schedule()

    def render(self):
        if not self.chats:
            return
        anchor,offset = self.anchor()
        bottom = self.canvas.yview()[1]>=1 and self.canvas.canvasy(0)>0
        first = max(0,anchor-2)
        last = min(len(self.chats),self.heights.locate(self.canvas.canvasy(self.canvas.winfo_height()))+4,first+40)
        wanted = set(range(first,last))
        width = max(20,self.canvas.winfo_width()-10)
        if wanted==set(self.rows) and not self.dirty and getattr(self,"width",None)==width:
            return
        self.width,self.dirty = width,False
        for index in list(self.rows):
            if index not in wanted:
                row,item = self.rows.pop(index)
                self.canvas.delete(item)
                self.free.append(row)
                self.app.row_widgets[index] = None
        for index in sorted(wanted):
            info = self.app.infos[index]
            if index not in self.rows:
                position = next((p for p,row in enumerate(self.free) if row.get("chat_id")==info.chat_id),None)
                row = self.free.pop(position if position is not None else -1) if self.free else self.new_row(index,info)
                self.show(row,index,info)
                item = self.canvas.create_window(5,self.heights.prefix(index)+2,window=row["frame"],anchor="nw",width=width)
                self.rows[index] = row,item
                row["item_width"] = width
                row["item_y"] = self.heights.prefix(index)+2
                self.app.row_widgets[index] = row
            row,item = self.rows[index]
            self.show(row,index,info)
            if row.get("item_width")!=width:
                self.canvas.itemconfigure(item,width=width)
                row["item_width"] = width
            self.heights.set(index,row["frame"].winfo_reqheight()+4)
        self.region()
        for index,(row,item) in self.rows.items():
            y = self.heights.prefix(index)+2
            if row.get("item_y")!=y:
                self.canvas.coords(item,5,y)
                row["item_y"] = y
        position = self.heights.prefix(anchor)+offset
        if bottom:
            self.canvas.yview_moveto(1)
        elif abs(self.canvas.canvasy(0)-position)>=1:
            self.canvas.yview_moveto(position/self.total())
        self.app._paint_rows()
        next_anchor = self.anchor()[0]
        next_first = max(0,next_anchor-2)
        next_last = min(len(self.chats),self.heights.locate(self.canvas.canvasy(self.canvas.winfo_height()))+4,next_first+40)
        if wanted!=set(range(next_first,next_last)):
            self.again = True
