"""合成多会话加载及真实 Tk 滚动基准；不访问本机 QQ。"""
import argparse
import base64
import gc
import io
import json
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import types
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PIL import Image
from qqexport.export.model import Message,Segment,MediaRef
from qqexport.repository import MessageRepository
from qqexport.virtual_list import VirtualMessages
from qqexport.tk_runtime import create_root
from qqexport.product_gui import ProductApp
from tools.bench_product import dataset


def baseline(revision):
    modules = {}
    for name in ("classic_row","virtual_list","repository","product_gui"):
        module = types.ModuleType("qqexport._baseline_"+name)
        code = subprocess.check_output(["git","show",f"{revision}:qqexport/{name}.py"]).decode("utf-8")
        sys.modules[module.__name__] = module
        exec(compile(code,f"{revision}:{name}.py","exec"),module.__dict__)
        modules[name] = module
    modules["virtual_list"].MessageRow = modules["classic_row"].MessageRow
    return modules["repository"].MessageRepository,modules["virtual_list"].VirtualMessages,modules["product_gui"].ProductApp


def sidebar_scroll(cls,source,infos,metas):
    class SyntheticApp(cls):
        def _setup_logging(self):
            pass
        def _load_chats(self):
            pass
    root = create_root()
    app = SyntheticApp(root)
    try:
        app._loaded(source,infos,metas,{})
        root.update()
        samples = []
        for i in range(120):
            start = time.perf_counter()
            app._hover_row(i%len(infos),True)
            app._wheel(types.SimpleNamespace(delta=-120 if i<60 else 120))
            root.update()
            samples.append((time.perf_counter()-start)*1000)
        refresh = []
        for _ in range(3):
            start = time.perf_counter()
            app._loaded(source,infos,metas,{})
            root.update()
            refresh.append((time.perf_counter()-start)*1000)
        return {"p50_ms":round(statistics.median(samples),2),"p95_ms":round(sorted(samples)[113],2),
                "max_ms":round(max(samples),2),"refresh_p50_ms":round(statistics.median(refresh),2),
                "row_widgets":sum(row is not None for row in app.row_widgets)}
    finally:
        app.close()
        del app,root
        gc.collect()


def overview(cls,source):
    start = time.perf_counter()
    repo = cls(source)
    initialized = time.perf_counter()
    try:
        if hasattr(repo,"sidebar"):
            infos,metas = repo.sidebar()
        else:
            infos,metas = source.chats(),{}
            for info in infos:
                last = repo.page(info.chat_id,limit=1,reverse=True).messages
                if last:
                    metas[info.chat_id] = last[-1].ts
        done = time.perf_counter()
        return {"repository_ms":round((initialized-start)*1000,2),
                "sidebar_ms":round((done-initialized)*1000,2),"total_ms":round((done-start)*1000,2),
                "chats":len(infos),"summaries":len(metas)}
    finally:
        repo.close()


def scrolling(cls,rootdir,selecting,count=100000):
    avatars = []
    for color in ("blue","green","red"):
        stream = io.BytesIO()
        Image.new("RGB",(64,64),color).save(stream,format="PNG")
        avatars.append("data:image/png;base64,"+base64.b64encode(stream.getvalue()).decode())
    pictures = []
    for i in range(12):
        path = rootdir/f"synthetic-{i}.png"
        Image.new("RGB",(240,100),(i*20,120,80)).save(path)
        pictures.append(path)
    root = create_root()
    root.geometry("1000x700")
    view = None
    def request(offset,size):
        messages = [(i,Message(i,100+i,None,None,"合成测试",False,
            [Segment("text",f"合成正文 {i}")]+([Segment("image",media=MediaRef("image",source=pictures[i%12],missing=False))] if i%4==0 else []),
            avatar_data=avatars[i%3])) for i in range(offset,min(offset+size,count))]
        view.loaded(offset,messages)
    def load(index,media):
        with Image.open(media.source) as image:
            copied = image.copy()
        root.after_idle(lambda:view.image_ready(index,copied,str(media.source)))
    view = cls(root,request,lambda index:None,image_loader=load)
    view.pack(fill="both",expand=True)
    view.selection(selecting,set())
    view.reset(count)
    root.update()
    view.see(50000)
    root.update()
    samples = []
    try:
        for i in range(240):
            start = time.perf_counter()
            row = view.rows[view.anchor()[0]][0]
            event = types.SimpleNamespace(delta=-120 if i<120 else 120)
            (row.renderer._sel_wheel if selecting else view.wheel)(event)
            root.update()
            samples.append((time.perf_counter()-start)*1000)
        return {"p50_ms":round(statistics.median(samples),2),"p95_ms":round(sorted(samples)[227],2),
                "max_ms":round(max(samples),2),"pool":len(view.rows)+len(view.free),"pages":len(view.pages)}
    finally:
        root.destroy()
        del view,root
        gc.collect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline",default="02d7b4e")
    parser.add_argument("--messages",type=int,default=100000)
    parser.add_argument("--chats",type=int,default=200)
    parser.add_argument("--report",type=Path,required=True)
    args = parser.parse_args()
    old_repo,old_view,old_app = baseline(args.baseline)
    with tempfile.TemporaryDirectory(prefix="qqexport-ui-bench-") as temp:
        rootdir = Path(temp)
        source = dataset(rootdir,args.messages)
        con = sqlite3.connect(source.msg_db)
        try:
            con.execute('UPDATE c2c_msg_table SET "40021"=\'u_benchmark_\'||("40001"%?),"40030"=\'12345\'',(args.chats,))
            con.commit()
        finally:
            con.close()
        result = {"messages":args.messages,"chats":args.chats,"baseline":args.baseline,
                  "before_load":overview(old_repo,source),"after_load":overview(MessageRepository,source)}
        repo = MessageRepository(source)
        infos,metas = repo.sidebar()
        repo.close()
        import os
        os.environ["QQEXPORT_HOME"] = str(rootdir/"state")
        result["before_sidebar"] = sidebar_scroll(old_app,source,infos,metas)
        result["after_sidebar"] = sidebar_scroll(ProductApp,source,infos,metas)
        for name,cls in (("before",old_view),("after",VirtualMessages)):
            for mode in (False,True):
                result[name+("_selection" if mode else "_preview")] = scrolling(cls,rootdir,mode)
        source.close()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))


if __name__=="__main__":
    main()
