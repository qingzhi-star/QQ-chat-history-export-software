"""T10：PDF 测试（假 Edge，不依赖系统安装）。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from qqexport.export import pdf


def test_find_edge_prefers_candidate(tmp_path):
    fake = tmp_path / "msedge.exe"
    fake.write_bytes(b"x")
    assert pdf.find_edge([fake]) == fake


def test_write_pdf_success_and_flags(tmp_path, monkeypatch):
    html = tmp_path / "chat.html"
    html.write_text("<html></html>", encoding="utf-8")
    out = tmp_path / "chat.pdf"
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        out.write_bytes(b"%PDF-1.4 test")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(pdf.subprocess, "run", fake_run)
    res = pdf.write_pdf(html, out, edge=tmp_path / "msedge.exe")
    assert res.ok and out.is_file()
    assert "--headless=new" in seen["cmd"]
    assert f"--print-to-pdf={out}" in seen["cmd"]


def test_write_pdf_timeout_keeps_html(tmp_path, monkeypatch):
    html = tmp_path / "chat.html"
    html.write_text("<html></html>", encoding="utf-8")
    out = tmp_path / "chat.pdf"

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(pdf.subprocess, "run", fake_run)
    res = pdf.write_pdf(html, out, edge=tmp_path / "edge.exe", timeout=1)
    assert not res.ok and "超时" in (res.error or "")
    assert html.is_file()


def test_write_pdf_missing_edge(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "find_edge", lambda candidates=None: None)
    res = pdf.write_pdf(tmp_path / "x.html", tmp_path / "x.pdf")
    assert not res.ok and res.path is None


def test_cancel_pdf_terminates_only_owned_process_tree(tmp_path,monkeypatch):
    import threading
    import pytest
    from qqexport.export.cancel import ExportCancelled
    cancel = threading.Event()
    calls = []
    class Process:
        pid = 12345
        returncode = None
        def __enter__(self):
            return self
        def __exit__(self,*args):
            pass
        def poll(self):
            cancel.set()
            return self.returncode
        def wait(self,**kwargs):
            return self.returncode
        def kill(self):
            self.returncode = -1
    child = Process()
    monkeypatch.setattr(pdf.subprocess,"Popen",lambda cmd,**kw:child)
    def terminate(cmd,**kwargs):
        calls.append(cmd)
        child.returncode = -1
    monkeypatch.setattr(pdf.subprocess,"run",terminate)
    with pytest.raises(ExportCancelled):
        pdf.write_pdf(tmp_path/"chat.html",tmp_path/"chat.pdf",edge=tmp_path/"edge.exe",cancel=cancel)
    if pdf.os.name=="nt":
        assert calls==[["taskkill","/PID",str(child.pid),"/T","/F"]]
