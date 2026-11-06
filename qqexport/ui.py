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


class ActionButton(tk.Button):
    def __init__(self,parent,*,primary=False,**kwargs):
        self.primary = primary
        self.normal = ACCENT if primary else "#ffffff"
        self.hover = "#6951b6" if primary else "#eee9f8"
        super().__init__(parent,bg=self.normal,fg="#ffffff" if primary else INK,
            activebackground=self.hover,activeforeground="#ffffff" if primary else INK,
            disabledforeground="#aaa3b6",font=("Microsoft YaHei UI",10),
            relief="flat",bd=0,highlightthickness=1,highlightbackground=ACCENT if primary else "#e1dbe9",
            padx=16,pady=7,cursor="hand2",**kwargs)
        self.bind("<Enter>",lambda _:self.configure(bg=self.hover) if self.cget("state")!="disabled" else None)
        self.bind("<Leave>",lambda _:self.configure(bg=self.normal))

    def state(self,states=None):
        if states is not None:
            if "disabled" in states:
                self.configure(state="disabled")
            elif "!disabled" in states:
                self.configure(state="normal")
        return ("disabled",) if self.cget("state")=="disabled" else ()

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
