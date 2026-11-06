"""可变行高的 Canvas 可视区控件池。只保留有界消息页和可见图片。"""
from __future__ import annotations
from array import array
from collections import OrderedDict
from datetime import datetime
import tkinter as tk
from tkinter import ttk

from qqexport.classic_row import MessageRow, media_key


class HeightIndex:
    def __init__(self, count, default=104):
        self.count, self.values = count, array("q", [default]) * count
        self.tree = array("q", [0] + [default * (i & -i) for i in range(1, count+1)])

    def prefix(self, end):
        result = 0
        while end:
            result += self.tree[end]
            end -= end & -end
        return result

    def set(self, index, height):
        height = max(30, int(height))
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
        low, high = 0, self.count
        while low < high:
            mid = (low + high + 1) // 2
            if self.prefix(mid) <= pixel:
                low = mid
            else:
                high = mid - 1
        return min(low, self.count-1)


class VirtualMessages(ttk.Frame):
    page_size = 128
    max_pages = 8

    def __init__(self, parent, request_page, on_toggle, on_open=None, image_loader=None):
        super().__init__(parent)
        self.request_page, self.on_toggle, self.on_open = request_page, on_toggle, on_open
        self.image_loader = image_loader
        self.count, self.heights, self.pages = 0, HeightIndex(0), OrderedDict()
        self.pending, self.rows, self.free = set(), {}, []
        self.selecting, self.selected, self.highlight = False, set(), None
        self._draw_pending = False
        self._drawing, self._needs_redraw, self._last_scroll = False, False, None
        self.canvas = tk.Canvas(self, bg="white", highlightthickness=0, yscrollincrement=24)
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
        for index, (row, item) in list(self.rows.items()):
            self.canvas.delete(item)
            row.place_forget()
            row.index = -1
            self.free.append(row)
        self.rows.clear()
        self.canvas.configure(scrollregion=(0,0,1,max(1,self.heights.prefix(count))))
        self.canvas.yview_moveto(1 if bottom else 0)
        if anchor and count:
            self.see(min(anchor[0], count-1), offset=anchor[1])
        self.schedule()

    def loaded(self, offset, messages):
        self.pending.discard(offset)
        self.pages[offset] = dict(messages)
        self.pages.move_to_end(offset)
        while len(self.pages) > self.max_pages:
            self.pages.popitem(last=False)
        self.schedule()

    def message(self, index):
        offset = index // self.page_size * self.page_size
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
        return self.canvas.yview()[1] >= 0.995

    def see(self, index, offset=0, highlight=False):
        if not self.count:
            return
        if highlight:
            self.highlight = index
        total = max(1,self.heights.prefix(self.count))
        self.canvas.yview_moveto((self.heights.prefix(index)+offset)/total)
        self.schedule()

    def selection(self, enabled, selected):
        self.selecting, self.selected = enabled, selected
        self.canvas.configure(highlightthickness=0 if enabled else 1)
        self.canvas.configure(yscrollincrement=0 if enabled else 24)
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
        self.canvas.yview_scroll(-int(event.delta/120)*3,"units")
        self.schedule()
        return "break"

    def schedule(self):
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

    def _render(self):
        if not self.count or not self.winfo_exists():
            return
        anchor, offset = self.anchor()
        bottom = self.at_bottom()
        width = max(200,self.canvas.winfo_width())
        first = max(0,anchor-3)
        last = min(self.count, self.heights.locate(self.canvas.canvasy(self.canvas.winfo_height()))+5, first+60)
        wanted = set(range(first,last))
        dirty = getattr(self,"_geometry_dirty",False) or getattr(self,"_width",None)!=width
        self._geometry_dirty = False
        self._width = width
        for index in list(self.rows):
            if index not in wanted:
                row,item = self.rows.pop(index)
                self.canvas.delete(item)
                self.free.append(row)
                dirty = True
        for index in sorted(wanted):
            if index not in self.rows:
                row = self.free.pop() if self.free else MessageRow(self.canvas,self)
                item = self.canvas.create_window(0,self.heights.prefix(index),window=row,anchor="nw",width=width)
                self.rows[index] = (row,item)
                dirty = True
            row,item = self.rows[index]
            if row.show(index,self.message(index),width):
                dirty = True
                self.canvas.itemconfigure(item,width=width)
        if not dirty:
            return
        self.update_idletasks()
        for index,(row,item) in self.rows.items():
            self.heights.set(index,row.measure_height())
        total = self.heights.prefix(self.count)
        self.canvas.configure(scrollregion=(0,0,width,total))
        for index,(row,item) in self.rows.items():
            self.canvas.coords(item,0,self.heights.prefix(index))
        if bottom:
            self.canvas.yview_moveto(1)
        else:
            self.canvas.yview_moveto((self.heights.prefix(anchor)+offset)/max(1,total))
        next_first = max(0,self.anchor()[0]-3)
        next_last = min(self.count,self.heights.locate(self.canvas.canvasy(self.canvas.winfo_height()))+5,next_first+60)
        if wanted!=set(range(next_first,next_last)):
            self._needs_redraw = True

    def image_ready(self, index, image, key=None):
        if index not in self.rows or image is None:
            return
        row,_ = self.rows[index]
        if key is not None and key not in row.requested:
            return
        if key is None:
            key = next(iter(row.requested),None)
        row.image_ready(key,image)
        self._geometry_dirty = True
        self.schedule()
