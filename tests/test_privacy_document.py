import json
from tools import build_privacy


def test_privacy_source_matches_bundled_policy_and_pins_same_pages_url():
    html,metadata=build_privacy.render()
    root=build_privacy.ROOT
    assert (root/"desktop/renderer/privacy.html").read_text(encoding="utf-8")==html
    assert (root/"desktop/privacy-policy.json").read_text(encoding="utf-8")==metadata
    assert json.loads(metadata)["url"] in (root/"desktop/renderer/index.html").read_text(encoding="utf-8")
    assert "<script" not in html and "<iframe" not in html
    assert "绮录QQ聊天记录导出软件" in html
