"""T10：用系统 Edge 把本地 HTML 打成 PDF；失败保留 HTML。"""
from __future__ import annotations

import shutil
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from qqexport.export.cancel import check_cancel

desktop_printer = None

_EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


@dataclass
class PdfResult:
    path: Path | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None and self.error is None


def find_edge(candidates: tuple[Path, ...] | list[Path] | None = None) -> Path | None:
    for candidate in (candidates if candidates is not None else _EDGE_CANDIDATES):
        if Path(candidate).is_file():
            return Path(candidate)
    found = shutil.which("msedge")
    return Path(found) if found else None


def write_pdf(html_path: Path, out_pdf: Path, *, timeout: int = 600,
              edge: Path | None = None, cancel=None) -> PdfResult:
    check_cancel(cancel)
    if desktop_printer is not None:
        return desktop_printer.render(html_path,out_pdf,timeout=timeout,cancel=cancel)
    exe = edge or find_edge()
    if exe is None:
        return PdfResult(None, "未找到 Microsoft Edge，请改用 --format html")
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    if out_pdf.exists():
        out_pdf.unlink()
    cmd = [str(exe), "--headless=new", "--disable-gpu",
           "--no-pdf-header-footer", f"--print-to-pdf={out_pdf}",
           html_path.resolve().as_uri()]
    try:
        if cancel is None:
            with tempfile.TemporaryDirectory(prefix="qqexport-pdf-profile-") as profile:
                subprocess.run(cmd+[f"--user-data-dir={profile}","--no-first-run",
                    "--disable-background-networking","--disable-extensions","--disable-sync"],
                    capture_output=True, timeout=timeout)
        else:
            with tempfile.TemporaryDirectory(prefix="qqexport-pdf-profile-") as profile:
                cmd += [f"--user-data-dir={profile}","--no-first-run","--disable-background-networking",
                        "--disable-extensions","--disable-sync"]
                with subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) as proc:
                    deadline = time.monotonic()+timeout
                    try:
                        while proc.poll() is None:
                            check_cancel(cancel)
                            if time.monotonic()>deadline:
                                raise subprocess.TimeoutExpired(cmd,timeout)
                            time.sleep(.1)
                    finally:
                        if proc.poll() is None:
                            if os.name == "nt":
                                subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],
                                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                            if proc.poll() is None:
                                proc.kill()
                            proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        return PdfResult(None, f"PDF 生成超时（>{timeout}s），已保留 html")
    except OSError as exc:
        return PdfResult(None, f"Edge 启动失败：{type(exc).__name__}")
    if not out_pdf.is_file() or not out_pdf.read_bytes()[:4] == b"%PDF":
        return PdfResult(None, "Edge 未产出有效 PDF，已保留 html")
    return PdfResult(out_pdf)
