"""绮录的操作按钮与居中窗口；聊天绘制不依赖这些样式。"""
import tkinter as tk

ACCENT = "#7860c6"
INK = "#302c40"
MUTED = "#81798e"
SURFACE = "#f7f5fb"


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
