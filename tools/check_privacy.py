#!/usr/bin/env python3
"""提交前敏感信息扫描（spec §10.1、修正案 A §0.5）。

扫描 staged diff（或指定提交范围）的新增行，命中即非零退出。
规则刻意收紧：只报**高置信度**的泄漏形状，避免误报导致扫描被绕过。

用法：
    python tools/check_privacy.py                 # 扫描已 staged 的改动
    python tools/check_privacy.py --check-author  # 额外检查作者/提交者邮箱
    python tools/check_privacy.py --range origin/main..HEAD
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# 已批准的合成 uin。测试与文档只允许使用这些账号标识。
ALLOWED_SYNTHETIC_UINS = frozenset({
    "123456789", "11111111", "22222222", "33333333", "44444444",
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
# Users\<name>\ 或 /home/<name>/
_USER_DIR_RE = re.compile(r"(?:Users|home)[\\/]([A-Za-z0-9_.<>-]+)[\\/]",
                          re.IGNORECASE)
# 引号内 16 或 32 字节可打印 ASCII
_KEY_LITERAL_RE = re.compile(r"""[bB]?["']([\x21-\x7e]{16}|[\x21-\x7e]{32})["']""")
_KEY_HINT_RE = re.compile(
    r"(?i)passphrase|password|secret|api[_-]?key|\btoken\b|口令|密钥")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


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
    out = _git("ls-files", "--others", "--exclude-standard")
    return [ln for ln in out.splitlines() if ln.strip()]


def _untracked_lines(paths: list[str]) -> list[str]:
    lines: list[str] = []
    for p in paths:
        try:
            with open(p, "rb") as f:
                raw = f.read(4 * 1024 * 1024)
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue                     # 二进制文件不按行扫
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", "replace")
        lines.extend(text.splitlines())
    return lines


def _added_lines(diff: str) -> list[str]:
    return [ln[1:] for ln in diff.splitlines()
            if ln.startswith("+") and not ln.startswith("+++")]


def _is_regex_source(line: str) -> bool:
    return "re.compile" in line or "_RE =" in line


def check_uins(added: list[str]) -> list[str]:
    hits = []
    for line in added:
        for rx in (_QUOTED_UIN_RE, _UIN_ASSIGN_RE):
            for m in rx.finditer(line):
                val = m.group(1)
                if val in ALLOWED_SYNTHETIC_UINS:
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


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    p = argparse.ArgumentParser(description="提交前敏感信息扫描")
    p.add_argument("--range", dest="rev_range", default=None)
    p.add_argument("--check-author", action="store_true")
    args = p.parse_args(argv)

    diff = _diff(args.rev_range)
    files = _files(args.rev_range)
    added = _added_lines(diff)

    untracked: list[str] = []
    if args.rev_range is None:
        untracked = _untracked()
        added = added + _untracked_lines(untracked)
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
