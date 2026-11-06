"""生成默认禁用的 Windows 计划任务文件；运行时只用已有本机密钥。"""
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime
from xml.etree import ElementTree as ET
from qqexport.errors import UserError
from qqexport.export.orchestrator import ExportOptions,validate_destination


def prepare(source,chats,formats,output,xml_path,clock="20:00"):
    account = source.meta.get("account")
    if account is None or not chats:
        raise UserError("计划导出需要当前本机 QQ 账号及明确选择的会话")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",clock):
        raise UserError("时间应为 HH:MM")
    xml_path = Path(xml_path).resolve()
    plan_path = xml_path.with_suffix(".plan.json")
    validate_destination(source,xml_path)
    validate_destination(source,plan_path)
    validate_destination(source,Path(output))
    if xml_path.exists() or plan_path.exists():
        raise UserError("任务文件已存在，请选择新文件名")
    plan = {"schema_version":1,"uin":account.uin,"data_dir":str(account.data_dir.parent.parent),
            "chats":list(chats),"formats":sorted(formats),"output":str(Path(output).resolve())}
    ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("",ns)
    task = ET.Element("{"+ns+"}Task",version="1.2")
    def child(parent,name,text=None,**attrs):
        element = ET.SubElement(parent,"{"+ns+"}"+name,attrs)
        element.text = text
        return element
    trigger = child(child(task,"Triggers"),"CalendarTrigger")
    child(trigger,"StartBoundary",datetime.now().strftime("%Y-%m-%dT")+clock+":00")
    child(trigger,"Enabled","true")
    child(child(trigger,"ScheduleByDay"),"DaysInterval","1")
    principal = child(child(task,"Principals"),"Principal",id="Author")
    child(principal,"LogonType","InteractiveToken")
    child(principal,"RunLevel","LeastPrivilege")
    settings = child(task,"Settings")
    for key,value in [("MultipleInstancesPolicy","IgnoreNew"),("DisallowStartIfOnBatteries","false"),
                      ("StopIfGoingOnBatteries","false"),("StartWhenAvailable","false"),
                      ("RunOnlyIfNetworkAvailable","false"),("Enabled","false"),("ExecutionTimeLimit","PT4H")]:
        child(settings,key,value)
    action = child(child(task,"Actions",Context="Author"),"Exec")
    child(action,"Command",sys.executable)
    arguments = ["--scheduled-export",str(plan_path)] if getattr(sys,"frozen",False) else ["-m","qqexport","scheduled-run",str(plan_path)]
    child(action,"Arguments",subprocess.list2cmdline(arguments))
    child(action,"WorkingDirectory",str(Path(sys.executable).parent if getattr(sys,"frozen",False) else Path(__file__).resolve().parent.parent))
    plan_path.parent.mkdir(parents=True,exist_ok=True)
    plan_created,xml_created = False,False
    try:
        with plan_path.open("x",encoding="utf-8") as stream:
            plan_created = True
            json.dump(plan,stream,ensure_ascii=False,indent=2)
        with xml_path.open("xb") as stream:
            xml_created = True
            stream.write(ET.tostring(task,encoding="utf-16",xml_declaration=True))
    except BaseException:
        if plan_created:
            plan_path.unlink(missing_ok=True)
        if xml_created:
            xml_path.unlink(missing_ok=True)
        raise
    return xml_path


def run(path):
    from qqexport.core import discover,keystore
    from qqexport.export.session import ExportSource
    from qqexport.export.jobs import ExportJob
    try:
        path = Path(path)
        if path.stat().st_size>1024*1024:
            raise ValueError
        plan = json.loads(path.read_text(encoding="utf-8"))
        if (type(plan["schema_version"]) is not int or plan["schema_version"]!=1
                or not isinstance(plan["uin"],str) or not plan["uin"].isdigit()
                or not isinstance(plan["data_dir"],str) or not isinstance(plan["output"],str)
                or not isinstance(plan["chats"],list) or not 1<=len(plan["chats"])<=10000
                or any(not isinstance(chat,str) or not chat.startswith(("c2c:","group:")) for chat in plan["chats"])
                or not isinstance(plan["formats"],list) or not plan["formats"]
                or any(not isinstance(fmt,str) or fmt not in {"txt","html","pdf"} for fmt in plan["formats"])):
            raise ValueError
    except (OSError,ValueError,KeyError,TypeError):
        raise UserError("计划导出配置无效；未开始导出") from None
    account = discover.select_account(discover.discover_accounts(explicit=Path(plan["data_dir"])),plan["uin"])
    key = keystore.KeyStore().load(account.uin)
    if not key:
        raise UserError("计划账号没有本机缓存密钥；不会自动扫描进程")
    source = ExportSource.open(account,key)
    try:
        job = ExportJob(account.uin,plan["chats"],ExportOptions(formats=set(plan["formats"]),out_base=Path(plan["output"])),write_manifest=True)
        job.run(source)
        return 0 if all(task.status in {"done","skipped"} for task in job.tasks) else 1
    finally:
        source.close()


def prepare_dialog(app):
    from tkinter import filedialog,simpledialog,messagebox
    from qqexport.archive_ui import ArchiveTask
    if app.session is None or not app.source.meta.get("account"):
        return
    session = app.session
    chats = sorted(app.batch_selected) or ([app.current_chat_id] if app.current_chat_id else [])
    if not chats:
        messagebox.showinfo("请选择会话","请先选中会话，或 Ctrl 多选会话。",parent=app.root)
        return
    clock = simpledialog.askstring("生成每日计划","输入每日运行时间 HH:MM：",initialvalue="20:00",parent=app.root)
    if clock is None:
        return
    output = filedialog.asksaveasfilename(parent=app.root,title="保存默认禁用的任务 XML",defaultextension=".xml",filetypes=[("计划任务","*.xml")])
    if not output or session.retired:
        return
    def done(_):
        messagebox.showinfo("任务文件已生成","已生成默认禁用的 XML 与计划 JSON。\n请在 Windows 任务计划程序中导入，核对运行账号、程序路径、输出目录后启用。\n仅在本人登录时运行，需已有缓存密钥；移动软件后请重新生成。\n本工具没有注册任务，也不会常驻后台。",parent=app.root)
    formats,out = set(app.preferences.formats),app.last_out
    ArchiveTask(app,"生成计划任务",lambda repo,cancel:prepare(repo,chats,formats,out,output,clock),done,session)
