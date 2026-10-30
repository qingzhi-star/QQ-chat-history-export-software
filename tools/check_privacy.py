#!/usr/bin/env python3
"""提交前敏感信息扫描（spec §10.1、修正案 A §0.5）。

扫描 staged diff（或指定提交范围）的新增行，命中即非零退出。
规则刻意收紧：只报**高置信度**的泄漏形状，避免误报导致扫描被绕过。

用法：
    python tools/check_privacy.py                 # 扫描 staged + 未跟踪文件
    python tools/check_privacy.py --audit         # 只读巡检全部受版本控制的文件
    python tools/check_privacy.py --check-author  # 额外检查作者/提交者邮箱
    python tools/check_privacy.py --range origin/main..HEAD

两种检查同时保留（修正案 A §0.5）：默认扫描拦住**新**泄漏，`--audit` 清理
**历史遗留**。后者只读，不修改任何文件，输出只含文件、行号与类别。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# 已批准的合成 uin。测试与文档只允许使用这些账号标识。
ALLOWED_SYNTHETIC_UINS = frozenset({
    "123456789", "987654321", "11111111", "22222222", "33333333", "44444444",
    "55555555", "66666666", "77777777", "88888888", "99999999", "12345",
})

# 已批准的合成口令。测试里出现这些字面量是预期的。
ALLOWED_SYNTHETIC_KEYS = frozenset({
    "#8FakeKeyForTest", "#8WrongKeyXXXXXX", "#8AnotherKeyXXXX",
    "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
})

# 路径里的通用/占位用户名，不算泄漏。
# 本表刻意保持极短：任何真实用户名一旦进来，就等于放过我们要防的那条泄漏。
GENERIC_PATH_NAMES = frozenset({
    "user", "users", "<user>", "me", "public", "default", "all users",
    "default user", ".", "..",
})

DATA_FILE_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".amr", ".silk")

# 引号内独立的 5–12 位纯数字：真实 uin 的实际泄漏形状
_QUOTED_UIN_RE = re.compile(r"""["'](\d{5,12})["']""")
# uin = 12345 / --uin 12345 形式
_UIN_ASSIGN_RE = re.compile(r"(?:uin|qq)\s*[=:]\s*['\"]?(\d{5,12})\b", re.IGNORECASE)
# Users\<name>\ 或 /home/<name>/。允许连续 1–2 个分隔符：Markdown 与 Python
# 源码里的转义写法 `D:\\Users\\<name>\\Documents` 曾绕过旧规则（真实用户名由此漏检）。
_USER_DIR_RE = re.compile(r"(?:Users|home)[\\/]{1,2}([A-Za-z0-9_.<>-]+)[\\/]{1,2}",
                          re.IGNORECASE)
# 引号内 16 或 32 字节可打印 ASCII
_KEY_LITERAL_RE = re.compile(r"""[bB]?["']([\x21-\x7e]{16}|[\x21-\x7e]{32})["']""")
_KEY_HINT_RE = re.compile(
    r"(?i)passphrase|password|secret|api[_-]?key|\btoken\b|口令|密钥")

# spec §7.1 的 QQ 字段号 / 列名（protobuf 字段号与 SQLite 列名），不是账号标识。
# 它们与 5–12 位 uin 形状相同，所以在「非身份上下文」里豁免；
# 一旦该行出现 uin / QQ 号 / 账号 / 数据目录等身份语汇，照常报告。
QQ_FIELD_IDS = frozenset({
    # spec §7.1 的字段号
    "40001", "40011", "40012", "40013", "40020", "40021", "40030", "40033",
    "40050", "40600", "40605", "40800", "40801", "40900", "49154", "49155",
    "45002", "45003", "45101", "45402", "45405", "45406", "45411", "45412",
    "45419", "45503", "45550", "45802", "45803", "45804", "45812", "45906",
    "45907", "47402", "47404", "47413", "47703", "47710", "47901", "47902",
    "47904", "48151", "48152", "48601", "48602", "48603",
    # P1 探测补充：消息表其余列 / 元素字段 / 伴随库列号（2026-10-29）
    "40002", "40003", "40005", "40006", "40008", "40009", "40010", "40026",
    "40027", "40040", "40041", "40052", "40058", "40060", "40062", "40083",
    "40084", "40090", "40093", "40100", "40105", "40601", "40722", "40850",
    "40851", "45001", "45408", "45416", "45418", "45424", "45505", "45511",
    "45513", "45517", "45518", "45815", "45816", "45817", "45818", "45819",
    "45820", "45821", "45822", "45823", "45824", "45825", "45826", "45827",
    "45828", "45830", "47403", "47411", "47415", "47416", "47422", "47423",
    "47601", "47602", "47603", "47604", "47605", "47606", "47702", "47704",
    "47713", "80810", "80824", "80900", "80901", "80902", "80903", "80905",
    "80908", "80909", "80910", "80935", "80970", "80975", "80980", "80981",
    "80983", "80995",
    # 伴随库列号（profile_info_v6 / group_* / rich_media.file_table）
    "20002", "20009", "25007", "320001", "320002", "60001", "60002", "60004",
    "60005", "60007", "60011", "60012", "60013", "60017", "60018", "60020",
    "60021", "60216", "64002", "64003", "64004", "64005",
})
_IDENTITY_CONTEXT_RE = re.compile(
    r"(?i)\buin\b|qq\s*号|账号|客户号|tencent files|--uin|用户目录")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def _git_z(*args: str) -> list[str]:
    """NUL 分隔的 git 输出。避免 core.quotepath 把中文文件名转义成八进制串
    —— 那样 `open()` 会失败，未跟踪的新文档就会被静默漏扫。"""
    out = subprocess.run(["git", *args], capture_output=True,
                         encoding="utf-8", errors="replace", check=True).stdout
    return [p for p in out.split("\0") if p]


def _diff(rev_range: str | None) -> str:
    if rev_range:
        return _git("log", "-p", "--format=", rev_range)
    return _git("diff", "--cached", "-U0")


def _files(rev_range: str | None) -> list[str]:
    args = ["diff", "--name-only", rev_range] if rev_range \
        else ["diff", "--cached", "--name-only"]
    return [ln for ln in _git(*args).splitlines() if ln.strip()]


def _untracked() -> list[str]:
    """尚未 git add 的新文件。

    默认扫描必须覆盖它们：本项目真实踩过一次——新建的文档里写了真实标识符，
    因为还没 staged，`git diff --cached` 看不到，扫描器报了 OK。
    """
    return _git_z("-c", "core.quotepath=false", "ls-files", "--others",
                  "--exclude-standard", "-z")


class _UnreadableFile(RuntimeError):
    """列表里的文件读不出来。扫描器必须 fail-closed，不能静默跳过。"""


def _text_lines(path: str) -> list[str] | None:
    """读取一个文件的文本行；二进制文件返回 None（不按行扫）。

    读失败抛 `_UnreadableFile`：静默跳过等于给泄漏留后门。
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(4 * 1024 * 1024)
    except OSError as e:
        raise _UnreadableFile(f"{path}: {type(e).__name__}") from e
    if b"\x00" in raw[:8192]:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    return text.splitlines()


def _untracked_lines(paths: list[str]) -> list[str]:
    lines: list[str] = []
    for p in paths:
        got = _text_lines(p)
        if got is not None:
            lines.extend(got)
    return lines


def _added_lines(diff: str) -> list[str]:
    return [ln[1:] for ln in diff.splitlines()
            if ln.startswith("+") and not ln.startswith("+++")]


def _is_regex_source(line: str) -> bool:
    return "re.compile" in line or "_RE =" in line


def check_uins(added: list[str]) -> list[str]:
    hits = []
    for line in added:
        identity_context = bool(_IDENTITY_CONTEXT_RE.search(line))
        for rx in (_QUOTED_UIN_RE, _UIN_ASSIGN_RE):
            for m in rx.finditer(line):
                val = m.group(1)
                if val in ALLOWED_SYNTHETIC_UINS:
                    continue
                if val in QQ_FIELD_IDS and not identity_context:
                    continue
                hits.append(f"疑似真实 uin {val} -> {line.strip()[:110]}")
    return hits


def check_user_dirs(added: list[str]) -> list[str]:
    hits = []
    for line in added:
        if _is_regex_source(line):
            continue
        for m in _USER_DIR_RE.finditer(line):
            name = m.group(1)
            if name.lower() in GENERIC_PATH_NAMES:
                continue
            if name.startswith("<") and name.endswith(">"):
                continue                      # 尖括号占位符，如 <name> / <user>
            if set(name) <= {".", "*"}:
                continue                      # 省略号或通配
            hits.append(f"疑似真实用户目录 {name!r} -> {line.strip()[:110]}")
    return hits


def check_data_files(files: list[str]) -> list[str]:
    hits = []
    for f in files:
        low = f.lower()
        if low.endswith(DATA_FILE_SUFFIXES):
            hits.append(f"数据文件被 staged: {f}")
        elif low.endswith(".bin") and "fixtures" not in low:
            hits.append(f"二进制文件被 staged（fixture 须人工确认后 -f 添加）: {f}")
    return hits


def check_key_literals(added: list[str]) -> list[str]:
    """只在「该行明显在谈论密钥」时报，避免把普通字符串常量当口令。"""
    hits = []
    for line in added:
        if not _KEY_HINT_RE.search(line):
            continue
        for m in _KEY_LITERAL_RE.finditer(line):
            val = m.group(1)
            if val in ALLOWED_SYNTHETIC_KEYS:
                continue
            if val.isidentifier() or val.isalpha():
                continue
            if not (any(c.isdigit() for c in val) and any(c.isalpha() for c in val)):
                continue
            hits.append(f"疑似真实口令字面量 -> {line.strip()[:110]}")
    return hits


def check_author(rev_range: str | None) -> list[str]:
    fmt = "%an <%ae>%n%cn <%ce>"
    out = _git("log", f"--format={fmt}", rev_range) if rev_range \
        else _git("log", "-1", f"--format={fmt}")
    hits = []
    for line in set(out.splitlines()):
        m = re.search(r"<([^>]+)>", line)
        if not m:
            continue
        local = m.group(1).split("@")[0]
        for d in re.findall(r"\d{5,12}", local):
            if d not in ALLOWED_SYNTHETIC_UINS:
                hits.append(f"提交者邮箱本地部分含疑似真实 uin: {line.strip()}")
    return hits


def audit_tracked() -> tuple[list[str], int, int]:
    """只读巡检全部受版本控制的文件，返回 (命中, 文件数, 行数)。

    与默认的提交前扫描互补：默认只看到**改动**，历史遗留的泄漏只有全库
    巡检才能发现（spec §10.1、修正案 A §0.5）。规则与默认扫描完全一致，
    避免两套判定标准互相漂移。
    """
    findings: list[str] = []
    files = _git_z("-c", "core.quotepath=false", "ls-files", "-z")
    n_files = n_lines = 0
    for path in files:
        try:
            lines = _text_lines(path)
        except _UnreadableFile as e:
            findings.append(f"无法读取受控文件（无法确认无泄漏）: {e}")
            continue
        if lines is None:
            continue
        n_files += 1
        for lineno, line in enumerate(lines, 1):
            n_lines += 1
            for hit in (check_uins([line]) + check_user_dirs([line])
                        + check_key_literals([line])):
                findings.append(f"{path}:{lineno}: {hit}")
    return findings, n_files, n_lines


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    p = argparse.ArgumentParser(description="提交前敏感信息扫描")
    p.add_argument("--range", dest="rev_range", default=None)
    p.add_argument("--check-author", action="store_true")
    p.add_argument("--audit", action="store_true",
                   help="只读巡检全部受版本控制的文件（清理历史遗留泄漏）")
    args = p.parse_args(argv)

    if args.audit:
        findings, n_files, n_lines = audit_tracked()
        scope = f"all tracked files（{n_files} 个文件，{n_lines} 行）"
        if findings:
            print(f"[FAIL] 全库隐私巡检未通过（{scope}，{len(findings)} 项）：",
                  file=sys.stderr)
            for f in findings:
                print(f"  - {f}", file=sys.stderr)
            print("\n处理：改为合成值或占位符；"
                  "历史提交若已推送，按 HANDOFF §7 的规则处理。", file=sys.stderr)
            return 1
        print(f"[OK] 全库隐私巡检通过（{scope}）")
        return 0

    diff = _diff(args.rev_range)
    files = _files(args.rev_range)
    added = _added_lines(diff)

    untracked: list[str] = []
    if args.rev_range is None:
        untracked = _untracked()
        try:
            added = added + _untracked_lines(untracked)
        except _UnreadableFile as e:
            print(f"[FAIL] 无法读取未跟踪文件（无法确认无泄漏）: {e}",
                  file=sys.stderr)
            return 1
        files = files + untracked

    findings: list[str] = []
    findings += check_uins(added)
    findings += check_user_dirs(added)
    findings += check_data_files(files)
    findings += check_key_literals(added)
    if args.check_author:
        findings += check_author(args.rev_range)

    scope = args.rev_range or "staged + untracked"
    if findings:
        seen: list[str] = []
        for f in findings:
            if f not in seen:
                seen.append(f)
        print(f"[FAIL] 敏感信息扫描未通过（{scope}，{len(seen)} 项去重后）：",
              file=sys.stderr)
        for f in seen:
            print(f"  - {f}", file=sys.stderr)
        print("\n处理：改为合成值或占位符后重新提交；"
              "若已提交但未推送，用 git reset --mixed 重建提交。", file=sys.stderr)
        return 1
    print(f"[OK] 敏感信息扫描通过（{scope}：{len(files)} 个文件，{len(added)} 行新增）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
