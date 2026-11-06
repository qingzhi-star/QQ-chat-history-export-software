"""冻结内核通过既有匿名管道调用 Electron 打印，不启动系统浏览器。"""
from pathlib import Path
import threading
import time
import uuid
from qqexport.export.cancel import check_cancel


class PdfBridge:
    def __init__(self,send):
        self.send,self.pending,self.lock = send,{},threading.Lock()

    def complete(self,message):
        with self.lock:
            entry = self.pending.get(message.get("id"))
            if entry:
                entry["result"] = message
                entry["event"].set()

    def render(self,html_path,out_pdf,*,timeout=600,cancel=None,**_):
        from qqexport.export.pdf import PdfResult
        check_cancel(cancel)
        identifier = uuid.uuid4().hex
        entry = {"event":threading.Event(),"result":None}
        with self.lock:
            self.pending[identifier] = entry
        try:
            self.send(dict(event="printPdf",id=identifier,html=str(Path(html_path).resolve()),output=str(Path(out_pdf).resolve())))
            end = time.monotonic()+timeout
            while not entry["event"].wait(.05):
                check_cancel(cancel)
                if time.monotonic()>end:
                    return PdfResult(None,"PDF 生成超时，已保留 HTML")
            check_cancel(cancel)
            result = entry["result"]
            output = Path(out_pdf)
            if result.get("ok") is not True or not output.is_file() or not output.read_bytes().startswith(b"%PDF-"):
                return PdfResult(None,"PDF 生成失败，请检查保存位置和磁盘空间")
            return PdfResult(output)
        finally:
            with self.lock:
                self.pending.pop(identifier,None)
            self.send(dict(event="cancelPdf",id=identifier))
