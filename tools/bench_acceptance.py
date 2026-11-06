"""P2 合成验收：实际 Tk/进程 RSS/Edge，不访问本机 QQ。"""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from tools.bench_product import dataset,incremental_benchmark
from tests.helpers import synth_proto as sp
from qqexport.repository import MessageRepository


def rss():
    class Counters(ctypes.Structure):
        _fields_ = [("cb",wintypes.DWORD),("faults",wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
            ("peak_ws","ws","peak_pool_paged","pool_paged","peak_pool_nonpaged","pool_nonpaged","pagefile","peak_pagefile")]
    info = Counters()
    info.cb = ctypes.sizeof(info)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(info),info.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return {"rss_mib":round(info.ws/1024**2,2),"peak_rss_mib":round(info.peak_ws/1024**2,2)}


def percentile(values,p=.95):
    return sorted(values)[min(len(values)-1,int(len(values)*p))]


def gui(rootdir):
    from PIL import Image
    from qqexport.product_gui import ProductApp
    from qqexport.tasks import DataSession
    from qqexport.tk_runtime import create_root
    from qqexport.qq import media
    os.environ["QQEXPORT_HOME"] = str(rootdir/"state")
    source = dataset(rootdir,100000)
    picdir = rootdir/"media"/"Pic"/"Ori"
    picdir.mkdir(parents=True)
    updates = []
    for i in range(20000):
        token = hashlib.md5(f"synthetic-{i}".encode()).hexdigest()
        Image.new("RGB",(320,240),(i%256,i//256%256,i//65536%256)).save(picdir/(token+".png"))
        updates.append((sp.message(sp.image_element(md5=bytes.fromhex(token),width=320,height=240)),i*5+1))
    con = sqlite3.connect(source.msg_db)
    con.execute('CREATE INDEX benchmark_mid ON c2c_msg_table("40001")')
    con.executemany('UPDATE c2c_msg_table SET "40800"=? WHERE "40001"=?',updates)
    con.commit()
    con.close()
    del updates
    source.media_index = media.build_index(rootdir/"media")
    source.ctx.resolve_media = lambda kind,md5,local,name:media.resolve(source.media_index,kind,md5,local,name)
    before = hashlib.sha256(source.msg_db.read_bytes()).hexdigest()
    start = time.perf_counter()
    repo = MessageRepository(source)
    repo_ms = (time.perf_counter()-start)*1000
    class SyntheticApp(ProductApp):
        def _setup_logging(self):
            pass
        def _load_chats(self):
            self.session = DataSession(source,repo)
            self._loaded(source,source.chats(),{}, {})
    root = create_root()
    app = SyntheticApp(root)
    def wait(predicate,timeout=10):
        end = time.monotonic()+timeout
        while not predicate():
            root.update()
            if time.monotonic()>end:
                raise RuntimeError("GUI synthetic wait timeout")
            time.sleep(.001)
    try:
        root.update()
        start = time.perf_counter()
        app._select_chat(0)
        wait(lambda:app.virtual.rows and all(row.message is not None for row,_ in app.virtual.rows.values()))
        first = (time.perf_counter()-start)*1000
        wait(lambda:any(row.photo is not None for row,_ in app.virtual.rows.values()))
        thumbs = (time.perf_counter()-start)*1000
        app.virtual.see(0)
        wait(lambda:app.virtual.message(0) is not None)
        times,work = [],[]
        for frame in range(240):
            start = time.perf_counter()
            app.virtual.see(frame)
            root.update()
            work.append((time.perf_counter()-start)*1000)
            time.sleep(max(0,1/60-(time.perf_counter()-start)))
            times.append(time.perf_counter()-start)
        sizes = rss()
        return {"messages":100000,"images":20000,"image_pixels":"320x240 synthetic PNG",
                "repository_init_ms":round(repo_ms,2),"first_readable_ms":round(first,2),"first_thumbnail_ms":round(thumbs,2),
                "scroll_fps":round(1/statistics.mean(times),2),"frame_p95_ms":round(percentile(times)*1000,2),
                "main_update_p95_ms":round(percentile(work),2),"main_update_max_ms":round(max(work),2),
                "cached_pages":len(app.virtual.pages),"row_pool":len(app.virtual.rows)+len(app.virtual.free),
                "source_readonly":before==hashlib.sha256(source.msg_db.read_bytes()).hexdigest(),**sizes}
    finally:
        app.close()
        while not app.tasks.idle():
            root.update()
            time.sleep(.005)


def global_search(rootdir):
    from qqexport.search import SearchIndex,Query
    source = dataset(rootdir,500000)
    con = sqlite3.connect(source.msg_db)
    con.execute('UPDATE c2c_msg_table SET "40021"=\'u_bench_\'||("40001"%50),"40020"=\'u_bench_\'||("40001"%50)')
    con.commit()
    con.close()
    repo,index = MessageRepository(source),SearchIndex()
    try:
        start = time.perf_counter()
        index.build(repo)
        cold = time.perf_counter()-start
        durations = []
        for _ in range(5):
            start = time.perf_counter()
            assert len(index.find(Query("benchmark")))>0
            durations.append((time.perf_counter()-start)*1000)
        return {"messages":500000,"chats":50,"index_seconds":round(cold,3),"hot_p95_ms":round(percentile(durations),2),**rss()}
    finally:
        index.close()
        repo.close()


def batch(rootdir):
    from qqexport.tasks import TaskRunner
    from qqexport.tk_runtime import create_root
    from qqexport.export.jobs import ExportJob
    from qqexport.export.orchestrator import ExportOptions
    source = dataset(rootdir,1000)
    con = sqlite3.connect(source.msg_db)
    con.execute('UPDATE c2c_msg_table SET "40021"=\'u_bench_\'||("40001"%50),"40020"=\'u_bench_\'||("40001"%50)')
    con.commit()
    con.close()
    root = create_root()
    root.withdraw()
    runner = TaskRunner(root)
    job = ExportJob(source.account_uin,[info.chat_id for info in source.chats()],ExportOptions(format="txt",out_base=rootdir/"out"))
    ticks = []
    last = time.perf_counter()
    def tick():
        nonlocal last
        now = time.perf_counter()
        ticks.append((now-last)*1000)
        last = now
        root.after(10,tick)
    tick()
    errors = []
    future = runner.submit(lambda:job.run(source),lambda _:None,errors.append)
    try:
        while not future.done():
            root.update()
            time.sleep(.001)
        future.result()
        assert all(task.status=="done" for task in job.tasks)
        return {"chats":len(job.tasks),"messages":1000,"tick_interval_ms":10,"max_tick_gap_ms":round(max(ticks),2),"errors":len(errors)}
    finally:
        runner.close()
        runner.pool.shutdown(wait=True)
        root.destroy()
        source.close()


def real_pdf(rootdir):
    from qqexport.export import pdf
    from qqexport.export.cancel import ExportCancelled
    from tests.test_orchestrator import make_source
    from qqexport.export.orchestrator import export_chats,ExportOptions
    source = make_source(rootdir)
    from PIL import Image
    image = next((rootdir/"nt_data").rglob("*.jpg"))
    Image.new("RGB",(800,600),"blue").save(image)
    report = export_chats(source,["c2c:u_a"],ExportOptions(formats={"txt","html","pdf"},out_base=rootdir/"out",require_all_formats=True))
    assert report.chats_exported==1 and not report.pdf_error
    output = Path(report.exported_dirs[0])
    size = (output/"chat.pdf").stat().st_size
    cancel = threading.Event()
    timer = threading.Timer(.2,cancel.set)
    timer.start()
    stopped = False
    try:
        pdf.write_pdf(output/"chat.html",rootdir/"cancelled.pdf",cancel=cancel)
    except ExportCancelled:
        stopped = True
    finally:
        timer.join()
        source.close()
    return {"edge_found":pdf.find_edge() is not None,"real_pdf_bytes":size,"cancel_observed":stopped}


if __name__=="__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=["gui","search","batch","pdf","incremental"])
    parser.add_argument("--report",type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="qqexport-acceptance-") as temp:
        result = incremental_benchmark(100000) if args.mode=="incremental" else globals()[{"search":"global_search","pdf":"real_pdf"}.get(args.mode,args.mode)](Path(temp))
    result = {"mode":args.mode,"os":platform.system(),"os_release":platform.release(),"python":platform.python_version(),**result}
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
