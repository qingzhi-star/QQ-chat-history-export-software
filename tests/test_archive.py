from dataclasses import replace
import json
from pathlib import Path
import threading
import zipfile

import pytest

from qqexport.export.archive import ArchiveReader,ArchiveError,create_archive,encrypted
from qqexport.export.cancel import ExportCancelled
from qqexport.export import orchestrator
from tests.test_orchestrator import make_source


@pytest.fixture(autouse=True)
def home(tmp_path,monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME",str(tmp_path/"home"))


@pytest.mark.parametrize("password",[None,"synthetic archive passphrase"])
def test_archive_roundtrip_offline_media_and_cleanup(tmp_path,password):
    src = make_source(tmp_path)
    target = tmp_path/"backup.qqarchive"
    result = create_archive(src,["c2c:u_a"],target,password=password)
    assert result["messages"]==2 and encrypted(target)==(password is not None)
    reader = ArchiveReader(target,password=password)
    root = reader.root
    offline = reader.source()
    assert [m.msg_id for m in offline.iter_messages("c2c:u_a")]==[1,2]
    assert offline.count("c2c:u_a")==2
    assert offline.window("c2c:u_a",1,1)[0][1].segments[0].media.source.read_bytes()==b"img"
    assert not any(str(tmp_path) in p.read_text(encoding="utf-8") for p in root.glob("messages/*.jsonl"))
    report = orchestrator.export_chats(offline,["c2c:u_a"],orchestrator.ExportOptions(formats={"txt","html"},out_base=tmp_path/"offline"))
    assert report.messages_total==2
    reader.close()
    assert not root.exists()
    assert target.exists()


def test_wrong_password_and_tamper_are_rejected(tmp_path):
    src = make_source(tmp_path)
    target = tmp_path/"backup.qqarchive"
    create_archive(src,["c2c:u_a"],target,password="synthetic archive passphrase")
    with pytest.raises(ArchiveError,match="密码"):
        ArchiveReader(target,password="wrong synthetic password")
    data = bytearray(target.read_bytes())
    data[-20] ^= 1
    target.write_bytes(data)
    with pytest.raises(ArchiveError,match="修改"):
        ArchiveReader(target,password="synthetic archive passphrase")


@pytest.mark.parametrize("name",["../outside.txt","media/../../outside.txt","C:/outside.txt","media/CON.txt"])
def test_zip_slip_and_reserved_names_rejected_without_external_write(tmp_path,name):
    target = tmp_path/"bad.qqarchive"
    with zipfile.ZipFile(target,"w") as archive:
        archive.writestr(name,b"bad")
    with pytest.raises(ArchiveError):
        ArchiveReader(target)
    assert not (tmp_path/"outside.txt").exists()


def test_changed_member_fails_checksum(tmp_path):
    src = make_source(tmp_path)
    target = tmp_path/"good.qqarchive"
    create_archive(src,["c2c:u_a"],target)
    bad = tmp_path/"bad.qqarchive"
    with zipfile.ZipFile(target) as original,zipfile.ZipFile(bad,"w") as archive:
        for info in original.infolist():
            archive.writestr(info, b"modified" if info.filename=="index.html" else original.read(info))
    with pytest.raises(ArchiveError,match="校验"):
        ArchiveReader(bad)


def test_cancel_and_existing_archive_keep_original(tmp_path):
    src = make_source(tmp_path)
    target = tmp_path/"backup.qqarchive"
    target.write_bytes(b"old backup")
    with pytest.raises(ArchiveError,match="存在"):
        create_archive(src,["c2c:u_a"],target)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ExportCancelled):
        create_archive(src,["c2c:u_a"],target,overwrite=True,cancel=cancel)
    assert target.read_bytes()==b"old backup"


def test_archive_cli_does_not_access_qq_when_offline(tmp_path,monkeypatch,capsys):
    from qqexport import cli
    src = make_source(tmp_path)
    target = tmp_path/"backup.qqarchive"
    create_archive(src,["c2c:u_a"],target)
    monkeypatch.setattr(cli,"_open_source",lambda args:pytest.fail("offline must not open QQ"))
    assert cli.main(["archive","verify",str(target)])==0
    assert cli.main(["archive","export",str(target),"--format","txt","--out",str(tmp_path/"out")])==0
