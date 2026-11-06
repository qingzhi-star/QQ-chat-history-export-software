"""privacy.md 同时生成软件内附全文和 Pages 静态页，不依赖第三方脚本。"""
import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import re
import shutil

ROOT=Path(__file__).resolve().parent.parent
URL="https://qingzhi-star.github.io/QL-qq-chat-history-exporter/privacy.html"


def render():
    text=(ROOT/"privacy.md").read_text(encoding="utf-8")
    version=re.search(r"^条款版本：([\d.\-]+)$",text,re.M)[1]
    blocks=[]
    for block in text.strip().split("\n\n"):
        if block.startswith("# "):
            blocks.append("<h1>"+escape(block[2:])+"</h1>")
        elif block.startswith("## "):
            blocks.append("<h2>"+escape(block[3:])+"</h2>")
        elif block.startswith("- "):
            blocks.append("<ul>"+"".join("<li>"+escape(line[2:])+"</li>" for line in block.splitlines())+"</ul>")
        else:
            blocks.append("<p>"+escape(block)+"</p>")
    page='<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page+='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'self\'; img-src \'self\'; base-uri \'none\'; form-action \'none\'">'
    page+='<title>隐私条款 · 绮录QQ聊天记录导出软件</title><link rel="stylesheet" href="privacy.css"></head><body><article>'
    page+='<img class="brand-icon" src="brand/qilu.png" alt="绮录">'+"\n".join(blocks)+"</article></body></html>\n"
    metadata=dict(version=version,url=URL,sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    return page,json.dumps(metadata,ensure_ascii=False,indent=2)+"\n"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--site",type=Path)
    args=parser.parse_args()
    page,metadata=render()
    files={ROOT/"desktop/renderer/privacy.html":page,ROOT/"desktop/privacy-policy.json":metadata}
    for path,content in files.items():
        if args.check:
            if path.read_text(encoding="utf-8")!=content:
                raise ValueError("隐私条款派生文件不同步，请运行 tools/build_privacy.py")
        else:
            path.write_text(content,encoding="utf-8")
    if args.site:
        args.site.mkdir(parents=True,exist_ok=True)
        (args.site/"privacy.html").write_text(page,encoding="utf-8")
        shutil.copy2(ROOT/"docs/site/index.html",args.site/"index.html")
        shutil.copy2(ROOT/"docs/site/site.css",args.site/"site.css")
        shutil.copy2(ROOT/"docs/images/desktop-preview.png",args.site/"desktop-preview.png")
        (args.site/"privacy-policy.json").write_text(metadata,encoding="utf-8")
        (args.site/".nojekyll").touch()
        shutil.copy2(ROOT/"privacy.md",args.site/"privacy.md")
        shutil.copy2(ROOT/"desktop/renderer/privacy.css",args.site/"privacy.css")
        (args.site/"brand").mkdir(exist_ok=True)
        shutil.copy2(ROOT/"qqexport/assets/qilu.png",args.site/"brand/qilu.png")
    print("[OK] privacy.md 与本机/网页条款同步")


if __name__=="__main__":
    main()
