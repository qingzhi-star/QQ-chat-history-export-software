"""当前账号卡片与原位展开列表，仅加载本机头像和显示资料。"""
from dataclasses import dataclass
import os
import sqlite3
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk
from qqexport.core import companions,keystore
from qqexport.qq import contacts,avatars
from qqexport.gui_avatars import rounded,placeholder
from qqexport.ui import INK,MUTED,ACCENT,ActionButton


@dataclass(frozen=True)
class Identity:
    uin: str
    name: str = "QQ 用户"
    avatar: bytes | None = None
    active: bool = False


def profile_name(book,uin):
    person = book.friends_by_uin.get(uin)
    if person and (person.nick or person.remark):
        return person.nick or person.remark
    for (_,member_uin),member in book.members_by_uin.items():
        if member_uin==uin and member.nick:
            return member.nick
    return "QQ 用户"


def database_in_use(account):
    """只探测文件共享占用，不读取认证资料；占用是活跃账号的本机信号。"""
    if os.name!="nt":
        return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
                                  wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(account.msg_db),0x80000000,0,None,3,128,None)
    if handle==ctypes.c_void_p(-1).value:
        return ctypes.get_last_error() in (32,33)
    kernel.CloseHandle(handle)
    return False


def read_identity(account):
    name = "QQ 用户"
    try:
        key = keystore.KeyStore().load(account.uin)
        if key:
            from qqexport.temporary import workspace
            with workspace("profiles") as temp:
                from pathlib import Path
                profile = companions.decrypt_companion(account.db_dir/"profile_info.db",key,Path(temp))
                book = contacts.Contacts.load(profile,None,account_uin=account.uin)
                name = profile_name(book,account.uin)
                if name=="QQ 用户":
                    group = companions.decrypt_companion(account.db_dir/"group_info.db",key,Path(temp))
                    name = profile_name(contacts.Contacts.load(profile,group,account_uin=account.uin),account.uin)
    except (OSError,RuntimeError,ValueError,sqlite3.Error):
        pass
    return Identity(account.uin,name,avatars.fetch_avatar(account.uin),database_in_use(account))


class AccountPanel:
    def __init__(self,app,parent,before):
        self.app,self.parent = app,parent
        self.identities,self.rows,self.photos = {},{},{}
        self.popup = None
        self.loaded = set()
        self.generation = 0
        self.card = tk.Frame(parent,bg="#ffffff",padx=12,pady=14)
        self.card.pack(fill="x",before=before,pady=(0,8))
        self.card.columnconfigure(1,weight=1)
        self.avatar = tk.Label(self.card,bg="#ffffff")
        self.avatar.grid(row=0,column=0,rowspan=2,padx=(0,10))
        self.name = tk.Label(self.card,text="选择账号",font=("Microsoft YaHei UI",11,"bold"),
                             fg=INK,bg="#ffffff",anchor="w",wraplength=165,justify="left")
        self.name.grid(row=0,column=1,sticky="w")
        self.number = tk.Label(self.card,text="点击右侧切换",font=("Microsoft YaHei UI",9),fg=MUTED,bg="#ffffff",anchor="w")
        self.number.grid(row=1,column=1,sticky="w",pady=(3,0))
        self.button = ActionButton(self.card,text="⇄",width=3,command=self.toggle)
        self.button.grid(row=0,column=2,rowspan=2,padx=(8,0))
        self._set_photo("header",self.avatar,None,"绮录",48)

    def _set_photo(self,key,label,data,name,size):
        photo = ImageTk.PhotoImage(rounded(data or placeholder(name,size),size),master=self.app.root)
        self.photos[key] = photo
        label.configure(image=photo)

    def current(self,source):
        if source.meta.get("account") is None:
            self.name.configure(text=source.meta.get("display_account","离线资料"))
            self.number.configure(text="本机资料")
            self._set_photo("header",self.avatar,None,"离线",48)
            return
        uin = source.account_uin
        old = self.identities.get(uin,Identity(uin))
        name = profile_name(source.contacts,uin)
        if name=="QQ 用户":
            name = old.name
        identity = Identity(uin,name,old.avatar,True)
        self.identities[uin] = identity
        self.paint_current(identity)
        if not source.meta.get("synthetic"):
            self.load_identity(source.meta["account"])

    def paint_current(self,identity):
        self.name.configure(text=identity.name)
        self.number.configure(text=f"QQ {identity.uin}")
        current = getattr(self,"_painted_identity",None)
        if current!=(identity.uin,identity.name,identity.avatar):
            self._set_photo("header",self.avatar,identity.avatar,identity.name,48)
            self._painted_identity = identity.uin,identity.name,identity.avatar

    def load_identity(self,account):
        if account.uin in self.loaded or account.uin in getattr(self,"pending",set()):
            return
        if not hasattr(self,"pending"):
            self.pending = set()
        self.pending.add(account.uin)
        def done(identity):
            self.pending.discard(account.uin)
            self.loaded.add(account.uin)
            if self.app._closed:
                return
            self.identities[identity.uin] = identity
            if self.app.uin==identity.uin:
                self.paint_current(identity)
            if self.popup is not None and self.popup.winfo_exists() and identity.uin in self.rows:
                self.paint_row(identity)
        def work():
            known = self.identities.get(account.uin)
            if known and known.name!="QQ 用户":
                return Identity(account.uin,known.name,avatars.fetch_avatar(account.uin),database_in_use(account))
            return read_identity(account)
        self.app.gui_avatars.tasks.submit(work,done,
            lambda _:done(self.identities.get(account.uin,Identity(account.uin))))

    def toggle(self):
        if self.popup is not None:
            self.close()
        else:
            self.app._switch_account()

    def show(self,accounts):
        self.close()
        self.accounts = list(accounts)
        self.generation += 1
        self.popup = tk.Frame(self.parent,bg="#ffffff",highlightthickness=1,highlightbackground="#d9d0eb")
        self.popup.place(x=0,y=self.card.winfo_y()+self.card.winfo_height(),relwidth=1)
        self.popup.lift()
        head = tk.Frame(self.popup,bg="#f5f1fb",padx=12,pady=8)
        head.pack(fill="x")
        tk.Label(head,text="切换账号",font=("Microsoft YaHei UI",10,"bold"),fg=INK,bg="#f5f1fb").pack(side="left")
        tk.Button(head,text="×",command=self.close,bg="#f5f1fb",fg=MUTED,relief="flat",bd=0).pack(side="right")
        for account in sorted(accounts,key=lambda a:(a.uin!=self.app.uin,not self.identities.get(a.uin,Identity(a.uin)).active)):
            identity = self.identities.get(account.uin,Identity(account.uin))
            row = tk.Frame(self.popup,bg="#ffffff",padx=12,pady=10,cursor="hand2",takefocus=1)
            row.pack(fill="x")
            row.columnconfigure(1,weight=1)
            image = tk.Label(row,bg="#ffffff")
            image.grid(row=0,column=0,rowspan=2,padx=(0,10))
            name = tk.Label(row,text=identity.name,font=("Microsoft YaHei UI",10,"bold"),fg=INK,bg="#ffffff",anchor="w")
            name.grid(row=0,column=1,sticky="w")
            number = tk.Label(row,text=f"QQ {identity.uin}",font=("Microsoft YaHei UI",9),fg=MUTED,bg="#ffffff")
            number.grid(row=1,column=1,sticky="w",pady=(2,0))
            mark = tk.Label(row,text="当前" if account.uin==self.app.uin else "",font=("Microsoft YaHei UI",9),fg=ACCENT,bg="#ffffff")
            mark.grid(row=0,column=2,rowspan=2,padx=(8,0))
            self.rows[account.uin] = row,image,name,mark
            for widget in (row,image,name,number,mark):
                widget.bind("<Button-1>",lambda _,account=account:self.choose(account))
            row.bind("<Return>",lambda _,account=account:self.choose(account))
            self.paint_row(identity)
            if not getattr(self.app,"_synthetic_accounts",False):
                self.load_identity(account)
        self._dismiss_binding = self.app.root.bind("<Button-1>",self.dismiss,add="+")

    def paint_row(self,identity):
        _,image,name,mark = self.rows[identity.uin]
        name.configure(text=identity.name)
        mark.configure(text="当前" if identity.uin==self.app.uin else "")
        self._set_photo(identity.uin,image,identity.avatar,identity.name,38)

    def choose(self,account):
        self.close()
        self.app._open_account(account,auto_key=True)

    def dismiss(self,event):
        widget = event.widget
        while widget is not None:
            if widget in (self.popup,self.card):
                return
            widget = getattr(widget,"master",None)
        self.close()

    def close(self):
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None
        self.rows.clear()
        self.photos = {key:value for key,value in self.photos.items() if key=="header"}
        binding = getattr(self,"_dismiss_binding",None)
        if binding:
            self.app.root.unbind("<Button-1>",binding)
            self._dismiss_binding = None
