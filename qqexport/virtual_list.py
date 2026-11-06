"""可变行高的 Canvas 可视区控件池。只保留有界消息页和可见图片。"""
from __future__ import annotations
from array import array
from collections import OrderedDict
from datetime import datetime
import tkinter as tk
from tkinter import ttk

from qqexport.canvas_row import MessageRow


class HeightIndex:
    def __init__(self, count, default=104):
        self.default = default
        self.count, self.values = count, array("q", [default]) * count
        self.tree = array("q", [0] + [default * (i & -i) for i in range(1, count+1)])

    def prefix(self, end):
        result = 0
        while end:
            result += self.tree[end]
            end -= end & -end
        return result

    def set(self, index, height):
        height = max(0, int(height))
        delta = height - self.values[index]
        if not delta:
            return
        self.values[index] = height
        pos = index + 1
        while pos <= self.count:
            self.tree[pos] += delta
            pos += pos & -pos

    def locate(self, pixel):
        if not self.count:
            return 0
        index,total = 0,0
        bit = 1 << (self.count.bit_length()-1)
        while bit:
            next_index = index+bit
            if next_index<=self.count and total+self.tree[next_index]<=pixel:
                index,total = next_index,total+self.tree[next_index]
            bit >>= 1
        return min(index,self.count-1)

    def resize(self,count):
        if count<self.count:
            self.__init__(count,self.default)
            return
        for position in range(self.count+1,count+1):
            start = position-(position & -position)
            self.tree.append(self.default+self.prefix(position-1)-self.prefix(start))
            self.values.append(self.default)
        self.count = count


class VirtualMessages(ttk.Frame):
    page_size = 128
    max_pages = 8

    def __init__(self, parent, request_page, on_toggle, on_open=None, image_loader=None):
        super().__init__(parent)
        self.request_page, self.on_toggle, self.on_open = request_page, on_toggle, on_open
        self.image_loader = image_loader
        self.count, self.heights, self.pages = 0, HeightIndex(0), OrderedDict()
        self.pending, self.rows, self.free = set(), {}, []
        self._stale_pages = set()
        self._wheel_remainder = 0
        self._fine_wheel_remainder,self._fine_wheel = 0,False
        self.avatar_photos,self.image_photos = OrderedDict(),OrderedDict()
        self.selecting, self.selected, self.highlight = False, set(), None
        self._draw_pending = False
        self._drawing, self._needs_redraw, self._last_scroll = False, False, None
        self.canvas = tk.Canvas(self, bg="white", highlightthickness=0, yscrollincrement=1)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.scroll)
        self.canvas.configure(yscrollcommand=self.scrolled)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda _: self.schedule())
        self.canvas.bind("<MouseWheel>", self.wheel)

    def reset(self, count, *, anchor=None, bottom=False):
        self.count, self.heights = count, HeightIndex(count)
        self.pages.clear()
        self.pending.clear()
        self._stale_pages.clear()
        for index, (row, item) in list(self.rows.items()):
            row.hide()
            row.index = -1
            self.free.append(row)
        self.rows.clear()
        self.canvas.configure(scrollregion=(0,0,1,max(1,self.heights.prefix(count),self.canvas.winfo_height())))
        self.canvas.yview_moveto(1 if bottom else 0)
        if anchor and count:
            self.see(min(anchor[0], count-1), offset=anchor[1])
        self.schedule()

    def refresh(self,count,*,anchor=None,bottom=False):
        """实时追加保留测量值和可见控件；旧内容在新页到达前继续显示。"""
        if not count:
            self.reset(0)
            return
        self.count = count
        self.heights.resize(count)
        self.pending.clear()
        for offset in list(self.pages):
            if offset>=count:
                self.pages.pop(offset)
        self._stale_pages = set(self.pages)
        self._geometry_dirty = True
        total = max(1,self.heights.prefix(count),self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0,0,max(1,self.canvas.winfo_width()),total))
        if bottom:
            self.canvas.yview_moveto(1)
        elif anchor:
            self.see(min(count-1,anchor[0]),offset=anchor[1])
        self.schedule()

    def loaded(self, offset, messages):
        self.pending.discard(offset)
        anchor,bottom = self.anchor(),self.at_bottom()
        previous = self.pages.get(offset,{})
        self.pages[offset] = {index:previous[index] if previous.get(index)==message else message
                              for index,message in messages}
        for index in range(offset,min(offset+self.page_size,self.count)):
            if index not in self.pages[offset]:
                self.heights.set(index,0)
            elif self.heights.values[index]==0:
                self.heights.set(index,self.heights.default)
        if not self._drawing:
            self._restore_view = anchor,bottom
        self._geometry_dirty = True
        self._stale_pages.discard(offset)
        self.pages.move_to_end(offset)
        while len(self.pages) > self.max_pages:
            self.pages.popitem(last=False)
        self.schedule()

    def message(self, index):
        offset = index // self.page_size * self.page_size
        if offset in self._stale_pages and offset not in self.pending:
            self.pending.add(offset)
            self.request_page(offset,self.page_size)
        if offset in self.pages:
            self.pages.move_to_end(offset)
            return self.pages[offset].get(index)
        if offset not in self.pending:
            self.pending.add(offset)
            self.request_page(offset, self.page_size)
        return None

    def anchor(self):
        pixel = max(0, self.canvas.canvasy(0))
        index = self.heights.locate(pixel)
        return index, int(pixel - self.heights.prefix(index))

    def at_bottom(self):
        total = max(1,self.heights.prefix(self.count),self.canvas.winfo_height())
        return total*(1-self.canvas.yview()[1])<=1

    def see(self, index, offset=0, highlight=False):
        if not self.count:
            return
        if highlight:
            self.highlight = index
        total = max(1,self.heights.prefix(self.count),self.canvas.winfo_height())
        self.canvas.yview_moveto((self.heights.prefix(index)+offset)/total)
        self.schedule()

    def selection(self, enabled, selected):
        self.selecting, self.selected = enabled, selected
        self.canvas.configure(highlightthickness=0 if enabled else 1)
        self.canvas.configure(yscrollincrement=1)
        self._fine_wheel = False
        self.schedule()

    def scroll(self, *args):
        self.canvas.yview(*args)
        self.schedule()

    def scrolled(self, first, last):
        self.scrollbar.set(first,last)
        if (first,last) != self._last_scroll:
            self._last_scroll = (first,last)
            if not self._drawing:
                self.schedule()

    def wheel(self, event):
        pixels = -event.delta*72/120+self._fine_wheel_remainder
        movement = int(pixels)
        self._fine_wheel_remainder = pixels-movement
        first,last = self.canvas.yview()
        if movement<0 and first>0 or movement>0 and last<1:
            self.canvas.yview_scroll(movement,"units")
            self.schedule()
        return "break"

    def schedule(self):
        if self._drawing:
            self._needs_redraw = True
            return
        if not self._draw_pending and not self._drawing:
            self._draw_pending = True
            self.after_idle(self.draw)

    def draw(self):
        self._draw_pending = False
        self._drawing = True
        self._needs_redraw = False
        try:
            self._render()
        finally:
            self._drawing = False
        if self._needs_redraw:
            self.schedule()

    def _take_row(self,message):
        return self.free.pop() if self.free else MessageRow(self.canvas,self)

    def _render(self):
        if not self.count or not self.winfo_exists():
            return
        (anchor,offset),bottom = getattr(self,"_restore_view",(self.anchor(),self.at_bottom()))
        if hasattr(self,"_restore_view"):
            del self._restore_view
        width = max(200,self.canvas.winfo_width())
        first = max(0,anchor-3)
        last = min(self.count, self.heights.locate(self.canvas.canvasy(self.canvas.winfo_height()))+5, first+60)
        wanted = {i for i in range(first,last) if self.heights.values[i]>0}
        dirty = getattr(self,"_geometry_dirty",False) or getattr(self,"_width",None)!=width
        self._geometry_dirty = False
        self._width = width
        for index in list(self.rows):
            if index not in wanted:
                row,item = self.rows.pop(index)
                row.hide()
                self.free.append(row)
                dirty = True
        for index in sorted(wanted):
            message = self.message(index)
            if self.heights.values[index]==0:
                if index in self.rows:
                    row,_ = self.rows.pop(index)
                    row.hide()
                    self.free.append(row)
                dirty = True
                continue
            if index not in self.rows:
                row = self._take_row(message)
                item = row.tag
                self.rows[index] = (row,item)
                dirty = True
            row,item = self.rows[index]
            if row.show(index,message,width):
                dirty = True

        if not dirty:
            return
        for index,(row,item) in self.rows.items():
            self.heights.set(index,row.measure_height())
        total = max(self.heights.prefix(self.count),self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0,0,width,total))
        for index,(row,item) in self.rows.items():
            row.move_to(self.heights.prefix(index))
        if bottom:
            self.canvas.yview_moveto(1)
        else:
            offset = min(offset,max(0,self.heights.values[anchor]-1))
            self.canvas.yview_moveto((self.heights.prefix(anchor)+offset)/max(1,total))
        next_first = max(0,self.anchor()[0]-3)
        next_last = min(self.count,self.heights.locate(self.canvas.canvasy(self.canvas.winfo_height()))+5,next_first+60)
        if set(self.rows)!={i for i in range(next_first,next_last) if self.heights.values[i]>0}:
            self._needs_redraw = True

    def image_ready(self, index, image, key=None):
        if index not in self.rows:
            return
        row,_ = self.rows[index]
        if key is not None and key not in row.requested:
            return
        if key is None:
            key = next(iter(row.requested),None)
        row.image_ready(key,image)
        for other,_ in self.rows.values():
            if key in other.requested:
                other._state = None
        self._geometry_dirty = True
        self.schedule()

    def avatar_ready(self,key):
        for row,_ in self.rows.values():
            if row.message and self.avatar_key(row.message)==key:
                row._state = None
                row.renderer._avatars.clear()
        self._geometry_dirty = True
        self.schedule()
