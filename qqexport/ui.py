"""绮录的操作按钮与居中窗口；聊天绘制不依赖这些样式。"""
import tkinter as tk

ACCENT = "#7860c6"
INK = "#302c40"
MUTED = "#81798e"
SURFACE = "#f7f5fb"


class ChoiceCard(tk.Frame):
    """整行可点击的多选卡片，键盘和程序变量更新共用同一状态。"""
    def __init__(self,parent,*,title,description,variable):
        super().__init__(parent,bg="#ffffff",highlightthickness=1,
                         highlightbackground="#e4dfed",padx=14,pady=11,takefocus=1,cursor="hand2")
        self.variable = variable
        self.columnconfigure(1,weight=1)
        self.check = tk.Canvas(self,width=21,height=21,bg="#ffffff",bd=0,highlightthickness=0)
        self.check.grid(row=0,column=0,rowspan=2,padx=(0,11),sticky="n",pady=2)
        self.title_label = tk.Label(self,text=title,bg="#ffffff",fg=INK,
                                   font=("Microsoft YaHei UI",10,"bold"),anchor="w")
        self.title_label.grid(row=0,column=1,sticky="ew")
        self.description = tk.Label(self,text=description,bg="#ffffff",fg=MUTED,
                                    font=("Microsoft YaHei UI",9),anchor="w")
        self.description.grid(row=1,column=1,sticky="ew",pady=(4,0))
        for widget in (self,self.check,self.title_label,self.description):
            widget.bind("<Button-1>",self.toggle)
        self.bind("<space>",self.toggle)
        self.bind("<Return>",self.toggle)
        self._trace = variable.trace_add("write",lambda *_:self.paint())
        self.bind("<Destroy>",self._cleanup)
        self.paint()

    def toggle(self,_=None):
        self.focus_set()
        self.variable.set(not self.variable.get())
        return "break"

    def paint(self):
        selected = self.variable.get()
        bg = "#f0eafa" if selected else "#ffffff"
        self.configure(bg=bg,highlightbackground="#baa5e5" if selected else "#e4dfed")
        for widget in (self.check,self.title_label,self.description):
            widget.configure(bg=bg)
        self.check.delete("all")
        self.check.create_polygon(6,2,15,2,19,2,19,6,19,15,19,19,15,19,6,19,2,19,2,15,2,6,2,2,
            smooth=True,splinesteps=24,fill=ACCENT if selected else "#ffffff",
            outline=ACCENT if selected else "#bdb5ca",width=1.5)
        if selected:
            self.check.create_line(6,10,9,13,15,7,fill="#ffffff",width=2.2,capstyle="round",joinstyle="round")

    def _cleanup(self,event):
        if event.widget is self:
            self.variable.trace_remove("write",self._trace)


class ActionButton(tk.Canvas):
    """圆角操作按钮：整块可点击，保留按钮状态与键盘操作。"""
    def __init__(self,parent,*,primary=False,text="",command=None,width=None,**kwargs):
        import tkinter.font as tkfont
        self.primary,self.command,self.label = primary,command,text
        self._state,self._hover,self._pressed = "normal",False,False
        self._font = tkfont.Font(root=parent,family="Microsoft YaHei UI",size=10)
        self._min_width = self._font.measure("0")*width+20 if width else 0
        self._button_width = max(self._min_width,self._font.measure(text)+32)
        try:
            background = parent.cget("bg")
        except tk.TclError:
            from tkinter import ttk
            background = ttk.Style(parent).lookup(parent.winfo_class(),"background") or "#f0f0f0"
        super().__init__(parent,width=self._button_width,height=40,bg=background,
                         bd=0,highlightthickness=0,takefocus=1,cursor="hand2",**kwargs)
        self.bind("<Configure>",lambda _:self._paint())
        self.bind("<Enter>",lambda _:self._pointer(True))
        self.bind("<Leave>",lambda _:self._pointer(False))
        self.bind("<ButtonPress-1>",self._press)
        self.bind("<ButtonRelease-1>",self._release)
        self.bind("<space>",lambda _:self.invoke())
        self.bind("<Return>",lambda _:self.invoke())
        self.bind("<FocusIn>",lambda _:self._paint())
        self.bind("<FocusOut>",lambda _:self._paint())
        self._paint()

    def _pointer(self,inside):
        self._hover = inside
        self._paint()

    def _press(self,_):
        if self._state!="disabled":
            self.focus_set()
            self._pressed = True
            self._paint()

    def _release(self,event):
        pressed,self._pressed = self._pressed,False
        self._paint()
        if pressed and 0<=event.x<self.winfo_width() and 0<=event.y<self.winfo_height():
            return self.invoke()

    def _paint(self):
        from PIL import Image,ImageDraw,ImageTk
        width,height = max(self._button_width,self.winfo_width()),max(40,self.winfo_height())
        disabled = self._state=="disabled"
        fill = ("#c9bddf" if disabled else "#6951b6" if self._hover or self._pressed else ACCENT) if self.primary else ("#f5f3f8" if disabled else "#eee9f8" if self._hover or self._pressed else "#ffffff")
        border = fill if self.primary else "#e1dbe9"
        bitmap = Image.new("RGBA",(width*3,height*3))
        draw = ImageDraw.Draw(bitmap)
        draw.rounded_rectangle((2,2,width*3-3,height*3-3),radius=27,fill=fill,outline=border,width=3)
        if self.focus_get() is self:
            draw.rounded_rectangle((8,8,width*3-9,height*3-9),radius=21,outline="#d6caf1" if self.primary else ACCENT,width=3)
        self._surface = ImageTk.PhotoImage(bitmap.resize((width,height),Image.Resampling.LANCZOS),master=self)
        self.delete("all")
        self.create_image(0,0,image=self._surface,anchor="nw")
        self.create_text(width/2,height/2,text=self.label,font=self._font,
                         fill="#aaa3b6" if disabled else "#ffffff" if self.primary else INK)

    def configure(self,cnf=None,**kwargs):
        if isinstance(cnf,dict):
            kwargs = dict(cnf,**kwargs)
        changed = False
        for key in ("text","command","state"):
            if key in kwargs:
                value = kwargs.pop(key)
                setattr(self,{"text":"label","state":"_state"}.get(key,key),value)
                changed = True
                if key=="text":
                    self._button_width = max(self._min_width,self._font.measure(value)+32)
                    kwargs["width"] = self._button_width
        result = super().configure(**kwargs) if kwargs or not changed else None
        if changed:
            self._paint()
        return result

    config = configure

    def cget(self,key):
        if key in ("text","state","command"):
            return getattr(self,{"text":"label","state":"_state"}.get(key,key))
        return super().cget(key)

    def invoke(self):
        if self._state!="disabled" and self.command:
            return self.command()

    def state(self,states=None):
        if states is not None:
            if "disabled" in states:
                self.configure(state="disabled")
            elif "!disabled" in states:
                self.configure(state="normal")
        return ("disabled",) if self._state=="disabled" else ()

    def instate(self,states):
        return all((item[1:] not in self.state()) if item.startswith("!") else item in self.state() for item in states)


def center_dialog(window,parent,width=None,height=None):
    window.update_idletasks()
    width = width or window.winfo_reqwidth()
    height = height or window.winfo_reqheight()
    x = parent.winfo_x()+(parent.winfo_width()-width)//2
    y = parent.winfo_y()+(parent.winfo_height()-height)//2
    x = max(0,min(x,window.winfo_screenwidth()-width))
    y = max(0,min(y,window.winfo_screenheight()-height))
    window.geometry(f"{width}x{height}+{x}+{y}")
