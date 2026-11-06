import json
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pytest
from qqexport import scheduled
from qqexport.errors import UserError
from tests.test_orchestrator import make_source


def test_prepared_schedule_is_disabled_no_secrets_and_explicit_account(tmp_path):
    source = make_source(tmp_path)
    source.meta["account"] = SimpleNamespace(uin="12345",data_dir=tmp_path/"Tencent Files"/"12345"/"nt_qq",db_dir=tmp_path/"qq-protected")
    output = tmp_path/"synthetic task.xml"
    scheduled.prepare(source,["c2c:u_a"],{"txt"},tmp_path/"out",output,"20:30")
    ns = {"t":"http://schemas.microsoft.com/windows/2004/02/mit/task"}
    task = ET.parse(output)
    assert task.find("t:Settings/t:Enabled",ns).text=="false"
    assert task.find("t:Principals/t:Principal/t:LogonType",ns).text=="InteractiveToken"
    plan = json.loads(output.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert plan["uin"]=="12345" and plan["formats"]==["txt"]
    assert not any(word in plan for word in ("key","password","messages"))
    original = output.read_bytes()
    with pytest.raises(UserError):
        scheduled.prepare(source,["c2c:u_a"],{"txt"},tmp_path/"out",output)
    assert output.read_bytes()==original


def test_invalid_plan_does_not_discover_or_open_qq(tmp_path,monkeypatch):
    from qqexport.core import discover
    monkeypatch.setattr(discover,"discover_accounts",lambda **kwargs:pytest.fail("invalid plan must stop before QQ"))
    path = tmp_path/"invalid.json"
    path.write_text('{"schema_version":999}',encoding="utf-8")
    with pytest.raises(UserError):
        scheduled.run(path)
