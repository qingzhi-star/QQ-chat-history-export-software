import threading
import pytest
from qqexport.electron_pdf import PdfBridge
from qqexport.export.cancel import ExportCancelled


def test_pdf_response_routes_to_waiter_and_checks_output(tmp_path):
    output = tmp_path/"synthetic.pdf"
    events = []
    printer = None
    def send(event):
        events.append(event)
        if event["event"]=="printPdf":
            output.write_bytes(b"%PDF-1.7\nsynthetic")
            printer.complete(dict(id=event["id"],ok=True))
    printer = PdfBridge(send)
    assert printer.render(tmp_path/"synthetic.html",output,timeout=1).ok
    assert [event["event"] for event in events]==["printPdf","cancelPdf"]
    assert not printer.pending


def test_pdf_cancellation_closes_parent_printer_and_removes_waiter(tmp_path):
    events,cancel = [],threading.Event()
    def send(event):
        events.append(event)
        if event["event"]=="printPdf":
            cancel.set()
    printer = PdfBridge(send)
    with pytest.raises(ExportCancelled):
        printer.render(tmp_path/"synthetic.html",tmp_path/"synthetic.pdf",cancel=cancel)
    assert events[-1]["event"]=="cancelPdf" and not printer.pending


def test_failed_or_stale_pdf_response_is_not_success(tmp_path):
    printer = None
    def send(event):
        if event["event"]=="printPdf":
            printer.complete(dict(id="unrelated",ok=True))
            printer.complete(dict(id=event["id"],ok=False))
    printer = PdfBridge(send)
    assert not printer.render(tmp_path/"synthetic.html",tmp_path/"synthetic.pdf").ok
    assert not printer.pending
