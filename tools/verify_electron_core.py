"""验证完整 Electron 应用的归档、搜索和内置 PDF，不依赖外部浏览器。"""
import json
import os
from pathlib import Path
import subprocess


def verify(executable,work):
    from PIL import Image
    from tests.test_orchestrator import make_source
    from qqexport.repository import MessageRepository
    from qqexport.export.archive import create_archive
    from tools.build_release import sha256
    work.mkdir()
    source = make_source(work)
    picture = next((work/"nt_data").rglob("*.jpg"))
    Image.new("RGB",(800,600),"#7860c6").save(picture)
    originals = [source.msg_db,work/"profile.db",work/"group.db",picture]
    before = {path:sha256(path) for path in originals}
    repo = MessageRepository(source)
    archive = work/"synthetic.qqarchive"
    try:
        create_archive(repo,["c2c:u_a"],archive,password="synthetic-smoke-passphrase")
    finally:
        repo.close()
        source.close()
    archive_hash = sha256(archive)
    report_path = work/"pipeline.json"
    env = dict(os.environ,QILU_PIPELINE_ARCHIVE=str(archive),QILU_PIPELINE_OUTPUT=str(work/"output"),
               QILU_PIPELINE_REPORT=str(report_path))
    if executable.name=="qilu-core.exe":
        raise ValueError("PDF 验收必须传入 Electron 主 exe，不能只运行内核")
    if executable.name.lower()=="electron.exe":
        env["QILU_ELECTRON_RUNTIME"] = str(executable)
        env.pop("QILU_PACKAGED_EXE",None)
    else:
        env["QILU_PACKAGED_EXE"] = str(executable)
    root = Path(__file__).resolve().parent.parent
    subprocess.run(["node",str(root/"desktop/test/pdf.cjs")],cwd=root,env=env,check=True,timeout=120)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["source_readonly"] = archive_hash==sha256(archive) and all(sha256(path)==digest for path,digest in before.items())
    assert report["source_readonly"]
    return report
