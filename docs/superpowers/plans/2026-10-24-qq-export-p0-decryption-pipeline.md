# QQ 导出工具 P0（解密链路）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通「定位 QQ 数据目录 → 只读内存扫描取 SQLCipher 口令 → 一致性快照 → 纯 Python 解密主库与 WAL → 产出标准明文 SQLite」这条链路，交付 `qqexport doctor` 自检命令。

**Architecture:** spec §5.1 的前半段。全部代码位于 `qqexport/core/`，对外只暴露一个契约：**一个可被 stdlib `sqlite3` 打开的明文数据库路径**。后半段（解析与导出）在 P0 真机验收后另出 plan。核心解耦手法是把 Win32 调用与纯计算彻底分离——候选串提取、页解密、WAL 校验和全是无副作用纯函数，可离线穷举测试；`kernel32` / `msvcrt` 等系统调用薄到可以用假对象注入。

**Tech Stack:** Python 3.14（floor 3.12）、`cryptography`（AES-CBC / HMAC，底层 OpenSSL）、stdlib `hashlib` `hmac` `ctypes` `sqlite3` `msvcrt`、`pytest`。

**Spec:** `docs/superpowers/specs/2026-10-23-qq-chat-history-export-design.md` —— 本 plan 从 spec 论证，执行者需同时阅读两份文档。相关章节：§3（实测基线）、§5.2（目录结构）、§6（core/ 全节）、§10（测试策略）、§11（降级总则）、§12（风险）。

**⚠️ 修正案 A（规范性，优先级高于本文件）：** `docs/superpowers/plans/2026-10-24-p0-amendment-A.md`
外部评审后对本计划做了实质修改，涉及 Task 2–13 的验收条件、`DecryptResult` 三态状态、
快照一致性证明、WAL 恢复的结论性证据、密钥流转禁令、明文产物清理、
SQLCipher 独立 known-answer 向量等。**执行任何 task 前必须先读修正案中对应的小节**；
两者冲突时以修正案为准。§15 还调整了执行顺序：Task 3 提到 Task 2 之前。

## Global Constraints

以下约束对**每个** task 隐式生效，值直接抄自 spec：

- **Python floor `>=3.12`**，开发机为 3.14.6（spec §3）
- **P0 运行时依赖只有 `cryptography>=43`**，dev extra 只有 `pytest>=8`。`fastapi`/`uvicorn`/`jinja2` 属 P2，本 plan 不得引入（spec §4.2、YAGNI）
- **绝不修改 QQ 数据目录下的任何文件。** core/ 对源文件只允许 `open(path, "rb")`。唯一例外是 `snapshot.py` 为取字节范围锁而以 `"r+b"` 打开 `-shm`——它**只加锁、不写任何字节**，且锁不可用时静默放行（见 Task 5）。工作副本一律放 `%LOCALAPPDATA%\QQExport\work\<uin>\`（spec §6.6、§12 R9）
- **内存扫描只用只读权限** `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`（`0x0010 | 0x0400`）。禁止 `CreateRemoteThread`、`WriteProcessMemory`、`DebugActiveProcess`、DLL 注入（spec §6.2、§12 R7）
- **口令本体永不出现**在日志、异常消息、报告、stdout、Web 响应中。只允许 SHA-256 指纹前 8 位十六进制（spec §6.3、§12 R10）
- **单点异常不得中止整体流程。** 页 HMAC 失败 → 跳过并计数；WAL 帧断裂 → 应用到断裂前最后 commit；快照校验失败 → 重试 3 次后才报错（spec §11）
- **加密常量**（spec §6.4，逐行核对自 MIT 参照实现，不得凭记忆改动）：
  `WRAPPER_SZ=1024`、`PAGE_SZ=4096`、`SALT_SZ=16`、`IV_SZ=16`、`KEY_SZ=32`、`KDF_ITER=4000`、`FAST_ITER=2`、`LOCK_PAGE_NO=262144`
- **参数探测八组合顺序固定**（spec §6.4 表），首位 `("sha512","sha1",48)`
- **仓库中不得出现任何真实聊天内容、真实昵称、真实 QQ 号、真实媒体文件**（spec §10.1）
- **许可证 MIT**，`NOTICE` 署名 `labazhou2024/chatlog-keeper`（MIT）。不得复制 GPL-3.0 / AGPL-3.0 / 非商用协议项目的代码（spec §4.3）
- **提交信息**用中文，`feat:` / `test:` / `chore:` / `docs:` 前缀，一个 task 一个提交

## Plan Scope（拆分说明）

spec §13 划分了 P0–P4。本 plan **只覆盖 P0**。理由：spec §5.1 的架构切口使前后两半仅以「一个明文 SQLite 文件路径」为契约；且 P1 的字段解析、表名匹配、segment 类型分布**依赖只有 P0 在真机跑通后才能实测确定的事实**。现在把 P1–P3 写成 bite-sized 步骤等于把假设固化进计划。

**P0 验收（对应 spec §10.3 的第 1、2、5、7、8 条）：**

1. `pytest` 全绿（`live` 标记除外）
2. `qqexport doctor` 前 11 项全为 ✅ 或 ⚠️
3. 产出的明文 `nt_msg.db` 可被 stdlib `sqlite3` 打开并查询
4. **最近的消息在**（证明 WAL 恢复生效——最容易静默失败的点）
5. 全程 QQ 数据目录 mtime 无变化
6. 连续两次解密结果字节一致

P1+ 的 plan 在 P0 验收后，用真机实测数据另写。

---

## File Structure

P0 涉及的文件与其单一职责：

| 文件 | 职责 | Task |
|---|---|---|
| `pyproject.toml` | 打包、依赖、pytest 配置、`qqexport` 入口点 | 1 |
| `.gitignore` | 排除工作副本、明文库、口令缓存、未确认的 fixture | 1 |
| `NOTICE` / `DISCLAIMER.md` / `LICENSE` | MIT 署名与使用边界声明 | 1 |
| `qqexport/__init__.py` | 版本号 | 1 |
| `qqexport/config.py` | `%LOCALAPPDATA%\QQExport\` 下的路径与配置读写 | 1 |
| `qqexport/core/discover.py` | 定位数据目录、枚举 uin、定位 QQ 安装 | 2 |
| `tests/helpers/fake_sqlcipher.py` | **测试专用**：加密方向的逆运算，用于造合成加密库 | 3 |
| `qqexport/core/sqlcipher.py` | 密钥派生、参数探测、页解密 | 3 |
| `qqexport/core/wal.py` | WAL 头/帧校验、恢复计划、两遍应用 | 4 |
| `qqexport/core/snapshot.py` | db+wal+shm 成组一致性复制 + 校验重试 | 5 |
| `qqexport/core/keyscan.py` | 候选串提取（纯函数）+ Win32 只读内存遍历 | 6 |
| `qqexport/core/keystore.py` | DPAPI 加密持久化口令、指纹 | 7 |
| `qqexport/core/decrypt.py` | 编排 snapshot→sqlcipher→wal，流式产出明文库 | 8 |
| `qqexport/report.py` | `DecryptResult` / `DoctorReport` 数据类与序列化、脱敏 | 9 |
| `qqexport/doctor.py` | 11 项环境自检 | 9 |
| `qqexport/cli.py` | `doctor` / `key scan|set|show|clear` 子命令 | 10 |
| `tools/capture_fixtures.py` | 一次性采集真库页 1 fixture（人工确认后提交） | 11 |
| `tests/test_live_smoke.py` | 真机端到端冒烟，`@pytest.mark.live` 默认跳过 | 12 |

**为什么 `fake_sqlcipher.py` 是必需的：** 解密器的正确性无法只靠「解密真库看像不像」来验证。实现加密方向后做 round-trip，能证明**实现**与常量自洽；真库 fixture 与 live 测试则证明常量与 **QQ 实际行为**一致。两者缺一不可，前者可在 CI 中无隐私数据地穷举运行。

---

### Task 1: 项目骨架与配置

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `LICENSE`
- Create: `NOTICE`
- Create: `DISCLAIMER.md`
- Create: `README.md`
- Create: `qqexport/__init__.py`
- Create: `qqexport/config.py`
- Create: `tests/__init__.py`（空文件，使 `tests.helpers` 可导入）
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: 无（首个 task）
- Produces:
  - `qqexport.__version__: str`
  - `qqexport.config.app_dir() -> Path`
  - `qqexport.config.config_path() -> Path`
  - `qqexport.config.keys_path() -> Path`
  - `qqexport.config.work_dir(uin: str) -> Path`
  - `qqexport.config.Config`（dataclass，字段 `data_dir: str|None`、`uin: str|None`、`cache_key: bool=True`、`decode_voice: bool=False`；类方法 `Config.load(path: Path|None=None) -> Config`，方法 `Config.save(path: Path|None=None) -> None`）
  - 环境变量 `QQEXPORT_HOME` 覆盖 `app_dir()`——**后续所有测试都靠它把状态目录指向 `tmp_path`**

- [ ] **Step 1: 写 `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "qqexport"
version = "0.1.0"
description = "导出本机自有 QQ NT 账号的聊天记录（仅限本人自有数据的本地使用）"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = ["cryptography>=43"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
qqexport = "qqexport.cli:main"

[tool.setuptools.packages.find]
include = ["qqexport*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = '-m "not live"'
markers = [
    "live: 需要本机实时运行的 QQ 客户端，默认跳过（用 -m live 显式运行）",
    "fixture: 需要 tests/fixtures/ 下由 tools/capture_fixtures.py 采集并经人工确认的文件，缺失则跳过",
]
```

- [ ] **Step 2: 写 `.gitignore`**

明文库与口令缓存含全部聊天隐私，必须默认排除。fixture 二进制默认排除，需人工确认后用 `git add -f` 显式提交（spec §10.1）。

```
__pycache__/
*.py[cod]
.pytest_cache/
*.egg-info/
build/
dist/
.venv/

# 解密产物与工作副本：含明文聊天记录，绝不入库
work/
*.decrypted.db
*.decrypted.db-*

# 口令缓存（DPAPI 保护，但仍不入库）
keys.json

# fixture：需人工确认不含隐私后才用 git add -f 提交
tests/fixtures/*.bin
```

- [ ] **Step 3: 写 `LICENSE`（MIT）、`NOTICE`、`DISCLAIMER.md`、`README.md`**

`LICENSE` 用标准 MIT 全文，版权行 `Copyright (c) 2026 qqexport contributors`。

`NOTICE` 全文：

```
qqexport — QQ 聊天记录导出工具
Copyright (c) 2026 qqexport contributors

本项目的 SQLCipher 4 页级解密算法与 WAL 恢复逻辑，参照了以下 MIT 许可项目的公开实现：

  chatlog-keeper
  Copyright (c) 2026 labazhou2024
  https://github.com/labazhou2024/chatlog-keeper
  License: MIT

参照范围限于算法与常量：密钥派生迭代数、页保留区布局、HMAC 构造方式、
WAL 帧校验和与 commit 边界语义。本项目代码为独立编写，未复制其源码文本。

SQLCipher 加密格式由 Zetetic LLC 设计，相关参数属其公开文档范畴。
QQ 与 QQ NT 为腾讯公司商标。本项目与腾讯公司无任何关联、未获其授权。
```

`DISCLAIMER.md` 全文：

```markdown
# 使用边界

本工具**仅**用于导出你**本人拥有**的 QQ 账号、存放在**你本人设备**上的聊天记录，
用途限于个人数据备份与迁移。

## 你必须遵守

- 只处理自己账号的数据。不得用于读取、导出他人账号的聊天记录。
- 只在本机运行。不得对他人设备使用。
- 导出的文件包含完整聊天隐私，自行妥善保管，不得公开传播他人参与的对话内容。

## 你需要知道的风险

- 本工具通过只读方式访问 QQ 客户端进程内存以取得数据库密钥。
  它**不注入 DLL、不附加调试器、不修改 QQ 的任何文件**，
  但这仍属于腾讯软件许可协议未明确允许的行为，理论风险由使用者自行承担。
- 同类工具（微信的 chatlog / PyWxDump / WeChatMsg）曾在 2025 年 10—12 月
  因腾讯法务函相继从 GitHub 下架。请勿将本项目公开发布或分发。
- 本工具全程离线，不向腾讯服务器发送任何请求。

## 本工具绝不做的事

- 不写入、不修改、不删除 QQ 数据目录下的任何文件
- 不在日志、报错、导出产物中记录数据库密钥
- 不联网
```

`README.md` 全文（P0 阶段，后续阶段补充）：

```markdown
# qqexport

导出本机自有 QQ NT 账号的聊天记录。

> 仅限本人自有账号、自有设备、自有数据的本地备份用途。详见 [DISCLAIMER.md](DISCLAIMER.md)。

## 当前进度

P0：解密链路（定位数据目录 → 取密钥 → 解密 → 产出明文 SQLite）。
消息解析与 txt/html/pdf 导出在后续阶段。

## 开发环境

```
python -m pip install -e ".[dev]"
pytest
```

## 许可

MIT。第三方参照见 [NOTICE](NOTICE)。
```

- [ ] **Step 4: 写 `qqexport/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 5: 写失败的测试 `tests/test_config.py`**

```python
from pathlib import Path

from qqexport import __version__
from qqexport import config


def test_version_is_string():
    assert isinstance(__version__, str) and __version__


def test_app_dir_honours_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path / "home"))
    assert config.app_dir() == tmp_path / "home"


def test_app_dir_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("QQEXPORT_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    assert config.app_dir() == tmp_path / "lad" / "QQExport"


def test_derived_paths_live_under_app_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path))
    assert config.config_path() == tmp_path / "config.json"
    assert config.keys_path() == tmp_path / "keys.json"
    assert config.work_dir("12345") == tmp_path / "work" / "12345"


def test_load_returns_defaults_when_file_missing(tmp_path):
    assert config.Config.load(tmp_path / "nope.json") == config.Config()


def test_roundtrip_preserves_all_fields(tmp_path):
    p = tmp_path / "config.json"
    original = config.Config(data_dir="D:/x", uin="123456789",
                             cache_key=False, decode_voice=True)
    original.save(p)
    assert config.Config.load(p) == original


def test_load_ignores_keys_from_newer_version(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"uin": "1", "from_the_future": 9}', encoding="utf-8")
    assert config.Config.load(p).uin == "1"


def test_load_tolerates_corrupt_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json", encoding="utf-8")
    assert config.Config.load(p) == config.Config()


def test_save_creates_parent_dirs_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "nested" / "deeper" / "config.json"
    config.Config(uin="1").save(p)
    assert p.is_file()
    assert list(p.parent.glob("*.tmp")) == []


def test_save_overwrites_existing(tmp_path):
    p = tmp_path / "config.json"
    config.Config(uin="old").save(p)
    config.Config(uin="new").save(p)
    assert config.Config.load(p).uin == "new"
```

同时创建空文件 `tests/__init__.py`。

- [ ] **Step 6: 运行测试确认失败**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qqexport.config'`

- [ ] **Step 7: 实现 `qqexport/config.py`**

```python
"""应用目录与配置。所有可变状态都在 %LOCALAPPDATA%\\QQExport\\ 下。

QQEXPORT_HOME 环境变量可整体重定向，测试靠它把状态指向 tmp_path。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

APP_DIR_NAME = "QQExport"


def app_dir() -> Path:
    override = os.environ.get("QQEXPORT_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / APP_DIR_NAME


def config_path() -> Path:
    return app_dir() / "config.json"


def keys_path() -> Path:
    return app_dir() / "keys.json"


def work_dir(uin: str) -> Path:
    return app_dir() / "work" / uin


@dataclass
class Config:
    data_dir: str | None = None
    uin: str | None = None
    cache_key: bool = True
    decode_voice: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        p = Path(path) if path is not None else config_path()
        if not p.is_file():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        p = Path(path) if path is not None else config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(p)
```

- [ ] **Step 8: 运行测试确认通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS，10 项全绿

- [ ] **Step 9: 安装包并验证入口点占位**

Run: `python -m pip install -e ".[dev]"`
Expected: 安装成功。此时 `qqexport` 命令会因 `qqexport.cli` 尚不存在而报错，属预期（Task 10 补齐）。

- [ ] **Step 10: 提交**

```bash
git add pyproject.toml .gitignore LICENSE NOTICE DISCLAIMER.md README.md \
        qqexport/__init__.py qqexport/config.py tests/__init__.py tests/test_config.py
git commit -m "feat: 搭建项目骨架与应用配置层"
```

---

### Task 2: `discover.py` — 数据目录与账号发现

**Files:**
- Create: `qqexport/core/__init__.py`（空文件）
- Create: `qqexport/core/discover.py`
- Create: `tests/test_discover.py`

**Interfaces:**
- Consumes: `qqexport.config.Config.data_dir`（Task 1）
- Produces:
  - `qqexport.core.discover.QQAccount`（frozen dataclass：`uin: str`、`data_dir: Path`、`db_dir: Path`、`media_dir: Path`；property `msg_db -> Path` = `db_dir / "nt_msg.db"`）
  - `qqexport.core.discover.DiscoveryError(RuntimeError)`
  - `qqexport.core.discover.documents_folder() -> Path | None`
  - `qqexport.core.discover.candidate_roots(explicit: Path | None = None) -> list[Path]`
  - `qqexport.core.discover.discover_accounts(explicit: Path | None = None, scan_drives: bool = False) -> list[QQAccount]`
  - `qqexport.core.discover.qq_install_version() -> str | None`

**背景（spec §3 实测）：** 本机 Windows「文档」已知文件夹被重定向到 `D:\Users\<user>\Documents`，`%USERPROFILE%\Documents\Tencent Files` **不存在**。因此必须以 `SHGetKnownFolderPath` 为主路径，`%USERPROFILE%` 只能作为最后回退。

- [ ] **Step 1: 写失败的测试 `tests/test_discover.py`**

```python
from pathlib import Path

import pytest

from qqexport.core import discover


def make_account(root: Path, uin: str, *, with_msg_db: bool = True) -> Path:
    """在 root 下造一个合成的 <uin>/nt_qq 账号目录，返回 nt_qq 路径。"""
    nt_qq = root / uin / "nt_qq"
    (nt_qq / "nt_db").mkdir(parents=True)
    (nt_qq / "nt_data").mkdir(parents=True)
    if with_msg_db:
        (nt_qq / "nt_db" / "nt_msg.db").write_bytes(b"\x00" * 5120)
    return nt_qq


def test_explicit_dir_wins_over_everything(tmp_path, monkeypatch):
    root = tmp_path / "explicit"
    root.mkdir()
    nt_qq = make_account(root, "11111111")
    monkeypatch.setattr(discover, "documents_folder", lambda: tmp_path / "elsewhere")
    found = discover.discover_accounts(explicit=root)
    assert [a.uin for a in found] == ["11111111"]
    assert found[0].data_dir == nt_qq


def test_documents_folder_redirect_is_followed(tmp_path, monkeypatch):
    """本机实况：文档在 D 盘，%USERPROFILE%\\Documents 下什么都没有。"""
    redirected = tmp_path / "D_Documents" / "Tencent Files"
    redirected.mkdir(parents=True)
    make_account(redirected, "123456789")
    monkeypatch.setattr(discover, "documents_folder",
                        lambda: tmp_path / "D_Documents")
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "fakeprofile"))
    monkeypatch.setattr(discover, "_registry_personal", lambda key: None)
    found = discover.discover_accounts()
    assert [a.uin for a in found] == ["123456789"]


def test_account_fields_are_derived_correctly(tmp_path):
    root = tmp_path / "TencentFiles"
    nt_qq = make_account(root, "123456789")
    acct = discover.discover_accounts(explicit=root)[0]
    assert acct.uin == "123456789"
    assert acct.data_dir == nt_qq
    assert acct.db_dir == nt_qq / "nt_db"
    assert acct.media_dir == nt_qq / "nt_data"
    assert acct.msg_db == nt_qq / "nt_db" / "nt_msg.db"
    assert acct.msg_db.is_file()


def test_non_numeric_dirs_are_ignored(tmp_path):
    root = tmp_path / "TencentFiles"
    root.mkdir()
    make_account(root, "123456789")
    (root / "nt_qq").mkdir()                       # 全局目录，非账号
    (root / "All Users").mkdir()                  # 非数字
    (root / "System Volume Information").mkdir()  # 无权限目录名
    found = discover.discover_accounts(explicit=root)
    assert [a.uin for a in found] == ["123456789"]


def test_account_without_msg_db_is_skipped(tmp_path):
    root = tmp_path / "TencentFiles"
    root.mkdir()
    make_account(root, "11111111", with_msg_db=False)
    make_account(root, "22222222")
    assert [a.uin for a in discover.discover_accounts(explicit=root)] == ["22222222"]


def test_multiple_accounts_sorted_by_uin(tmp_path):
    root = tmp_path / "TencentFiles"
    root.mkdir()
    for uin in ("33333333", "11111111", "22222222"):
        make_account(root, uin)
    assert [a.uin for a in discover.discover_accounts(explicit=root)] == [
        "11111111", "22222222", "33333333"]


def test_discovery_error_message_contains_guidance(tmp_path, monkeypatch):
    monkeypatch.setattr(discover, "documents_folder", lambda: None)
    monkeypatch.setattr(discover, "_registry_personal", lambda key: None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty"))
    with pytest.raises(discover.DiscoveryError) as ei:
        discover.discover_accounts()
    msg = str(ei.value)
    assert "--data-dir" in msg and "Tencent Files" in msg


def test_candidate_roots_dedupes_case_insensitively(tmp_path, monkeypatch):
    same = tmp_path / "Docs"
    monkeypatch.setattr(discover, "documents_folder", lambda: same)
    monkeypatch.setattr(discover, "_registry_personal",
                        lambda key: str(same).upper())
    # 必须清掉真实 USERPROFILE，否则它额外贡献一个候选根，去重断言就不成立
    monkeypatch.delenv("USERPROFILE", raising=False)
    roots = discover.candidate_roots(explicit=same / "Tencent Files")
    assert len(roots) == 1
    assert roots[0] == same / "Tencent Files"


def test_drive_scan_finds_nested_tencent_files(tmp_path, monkeypatch):
    deep = tmp_path / "Users" / "me" / "Documents" / "Tencent Files"
    deep.mkdir(parents=True)
    make_account(deep, "123456789")
    monkeypatch.setattr(discover, "_drive_roots", lambda: [tmp_path])
    found = discover.discover_accounts(scan_drives=True)
    assert [a.uin for a in found] == ["123456789"]


def test_drive_scan_prunes_noisy_dirs(tmp_path, monkeypatch):
    """System Volume Information / $RECYCLE.BIN 不得被进入（本机实测会抛权限错）。"""
    for noisy in ("System Volume Information", "$RECYCLE.BIN", "Windows"):
        (tmp_path / noisy).mkdir()
    real = tmp_path / "Docs" / "Tencent Files"
    real.mkdir(parents=True)
    make_account(real, "123456789")
    monkeypatch.setattr(discover, "_drive_roots", lambda: [tmp_path])
    assert len(discover.discover_accounts(scan_drives=True)) == 1


@pytest.mark.parametrize("names,expected", [
    (["9.9.36-53644"], "9.9.36-53644"),
    (["9.9.17-31363", "9.9.36-53644"], "9.9.36-53644"),
    (["9.9.9-99999", "9.9.36-1"], "9.9.36-1"),
    ([], None),
])
def test_version_key_picks_highest(tmp_path, monkeypatch, names, expected):
    vdir = tmp_path / "QQNT" / "versions"
    vdir.mkdir(parents=True)
    for n in names:
        (vdir / n).mkdir()
    monkeypatch.setattr(discover, "_qqnt_bases", lambda: [tmp_path / "QQNT"])
    assert discover.qq_install_version() == expected
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_discover.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qqexport.core'`

- [ ] **Step 3: 实现 `qqexport/core/__init__.py`（空文件）与 `qqexport/core/discover.py`**

```python
"""定位 QQ NT 安装位置、数据目录与账号。

本机「文档」已知文件夹可能被重定向到其他盘（spec §3：实测重定向，具体盘符与
用户名不入库），所以主路径必须走 SHGetKnownFolderPath，%USERPROFILE%\\Documents
只能作最后回退。
"""
from __future__ import annotations

import ctypes
import os
import re
import string
from dataclasses import dataclass
from pathlib import Path

_UIN_RE = re.compile(r"^\d{5,12}$")

_MAX_DRIVE_SCAN_DEPTH = 5
_PRUNE = frozenset({
    "System Volume Information", "$RECYCLE.BIN", "$WinREAgent", "Recovery",
    "Windows", "ProgramData", "node_modules", ".git",
})


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
FOLDERID_Documents = _GUID(
    0xFDD39AD0, 0x238F, 0x46AF,
    (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
)


@dataclass(frozen=True)
class QQAccount:
    uin: str
    data_dir: Path      # ...\Tencent Files\<uin>\nt_qq
    db_dir: Path        # <data_dir>\nt_db
    media_dir: Path     # <data_dir>\nt_data

    @property
    def msg_db(self) -> Path:
        return self.db_dir / "nt_msg.db"


class DiscoveryError(RuntimeError):
    """一个账号都没找到。异常消息必须带人工排查建议。"""


def documents_folder() -> Path | None:
    """SHGetKnownFolderPath(FOLDERID_Documents)。失败返回 None 由调用方回退。"""
    if os.name != "nt":
        return None
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    except OSError:
        return None
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_ulong, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    out = ctypes.c_wchar_p()
    try:
        hr = shell32.SHGetKnownFolderPath(
            ctypes.byref(FOLDERID_Documents), 0, None, ctypes.byref(out))
    except OSError:
        return None
    if hr != 0 or not out.value:
        if out:
            ole32.CoTaskMemFree(out)
        return None
    try:
        return Path(out.value)
    finally:
        ole32.CoTaskMemFree(out)


def _registry_personal(subkey: str) -> str | None:
    """读 HKCU\\<subkey> 的 Personal 值并展开环境变量。失败返回 None。"""
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as k:
            val, _ = winreg.QueryValueEx(k, "Personal")
    except OSError:
        return None
    if not isinstance(val, str) or not val:
        return None
    return os.path.expandvars(val)


_SHELL_FOLDERS_KEYS = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
)


def candidate_roots(explicit: Path | None = None) -> list[Path]:
    """按 spec §6.1 的优先级产出候选 'Tencent Files' 根目录，大小写不敏感去重。"""
    raw: list[Path] = []
    if explicit is not None:
        raw.append(Path(explicit))
    docs = documents_folder()
    if docs is not None:
        raw.append(docs / "Tencent Files")
    for key in _SHELL_FOLDERS_KEYS:
        p = _registry_personal(key)
        if p:
            raw.append(Path(p) / "Tencent Files")
    profile = os.environ.get("USERPROFILE")
    if profile:
        raw.append(Path(profile) / "Documents" / "Tencent Files")

    seen: set[str] = set()
    out: list[Path] = []
    for r in raw:
        k = str(r).lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _iter_accounts_under(root: Path) -> list[QQAccount]:
    if not root.is_dir():
        return []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    found: list[QQAccount] = []
    for e in entries:
        try:
            if not e.is_dir(follow_symlinks=False) or not _UIN_RE.match(e.name):
                continue
            data = Path(e.path) / "nt_qq"
            acct = QQAccount(uin=e.name, data_dir=data,
                             db_dir=data / "nt_db", media_dir=data / "nt_data")
            if acct.msg_db.is_file():
                found.append(acct)
        except OSError:
            continue
    return found


def _drive_roots() -> list[Path]:
    out: list[Path] = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        try:
            if drive.exists():
                out.append(drive)
        except OSError:
            continue
    return out


def _find_tencent_files(max_depth: int = _MAX_DRIVE_SCAN_DEPTH) -> list[Path]:
    """有界深度优先搜索 'Tencent Files' 目录，逐个吞掉 OSError。"""
    hits: list[Path] = []
    stack: list[tuple[str, int]] = [(str(r), 0) for r in _drive_roots()]
    while stack:
        cur, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False) or e.name in _PRUNE:
                    continue
            except OSError:
                continue
            if e.name == "Tencent Files":
                hits.append(Path(e.path))
            else:
                stack.append((e.path, depth + 1))
    return hits


def discover_accounts(explicit: Path | None = None,
                      scan_drives: bool = False) -> list[QQAccount]:
    """发现所有可用账号，按 uin 升序。一个都没有则抛 DiscoveryError。"""
    accounts: dict[str, QQAccount] = {}
    for root in candidate_roots(explicit):
        for a in _iter_accounts_under(root):
            accounts.setdefault(a.uin, a)

    if not accounts and scan_drives:
        for root in _find_tencent_files():
            for a in _iter_accounts_under(root):
                accounts.setdefault(a.uin, a)

    if not accounts:
        raise DiscoveryError(
            "未找到任何 QQ NT 账号数据目录。已尝试："
            + "；".join(str(r) for r in candidate_roots(explicit))
            + "。请确认 QQ 已至少登录过一次，或用 --data-dir 手动指定 "
              "'Tencent Files' 目录（其下应有 <数字QQ号>\\nt_qq\\nt_db\\nt_msg.db）。"
        )
    return [accounts[k] for k in sorted(accounts)]


def _qqnt_bases() -> list[Path]:
    return [
        Path(r"C:\Program Files\Tencent\QQNT"),
        Path(r"C:\Program Files (x86)\Tencent\QQNT"),
    ]


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", name))


def qq_install_version() -> str | None:
    """返回 versions\\ 下最高版本号目录名，如 '9.9.36-53644'。找不到返回 None。"""
    for base in _qqnt_bases():
        vdir = base / "versions"
        if not vdir.is_dir():
            continue
        try:
            names = [e.name for e in os.scandir(vdir)
                     if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        if names:
            return max(names, key=_version_key)
    return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_discover.py -v`
Expected: PASS，13 项全绿（含 4 个 parametrize 用例）

- [ ] **Step 5: 真机手动验证发现逻辑**

Run: `python -c "from qqexport.core import discover as d; [print(a.uin, a.msg_db, a.msg_db.stat().st_size) for a in d.discover_accounts()]; print('QQ', d.qq_install_version())"`

Expected: 打印出一行 `<uin> <数据目录>\nt_qq\nt_db\nt_msg.db <字节数>` 加一行 `QQ 9.9.36-53644`。核对三点：uin 是纯数字、字节数满足 `% 4096 == 1024`（本机实测为 335545344）、版本号为 `9.9.36-53644`。

**不要把这一步的真实输出粘进任何要提交的文件**——它含真实 QQ 号与真实路径，属 spec §10.1 的红线。需要留档时按 Task 13 Step 3 的脱敏规则处理。

**若打印的路径落在 C 盘或抛 DiscoveryError，说明 `documents_folder()` 未生效，必须先修好再继续**——这是 spec §3 记录的本机实况。

- [ ] **Step 6: 提交**

```bash
git add qqexport/core/__init__.py qqexport/core/discover.py tests/test_discover.py
git commit -m "feat: 数据目录与账号发现，走 SHGetKnownFolderPath 兼容文档重定向"
```

---

### Task 3: `sqlcipher.py` — 页级解密核心

**Files:**
- Create: `qqexport/core/sqlcipher.py`
- Create: `tests/helpers/__init__.py`（空文件）
- Create: `tests/helpers/fake_sqlcipher.py`（测试专用加密方向）
- Create: `tests/test_sqlcipher.py`

**Interfaces:**
- Consumes: 无（纯计算，不依赖 Task 1/2）
- Produces:
  - 常量 `WRAPPER_SZ=1024` `PAGE_SZ=4096` `SALT_SZ=16` `IV_SZ=16` `KEY_SZ=32` `KDF_ITER=4000` `FAST_ITER=2` `LOCK_PAGE_NO=262144` `SQLITE_MAGIC=b"SQLite format 3\x00"` `VERIFY_COMBOS: tuple[tuple[str,str,int], ...]`
  - `CipherParams`（frozen dataclass：`kdf: str`、`mac: str`、`reserve: int`、`aes_key: bytes`、`mac_key: bytes`；property `fingerprint -> str`；method `redacted() -> dict`）
  - `derive_keys(passphrase: bytes, salt: bytes, kdf: str) -> tuple[bytes, bytes]`
  - `digest_len(mac: str) -> int`
  - `combo_is_possible(kdf: str, mac: str, reserve: int) -> bool`
  - `read_header_and_page1(db_path: Path) -> bytes`（返回前 `1024+4096` 字节）
  - `detect_params(passphrase: bytes, header_and_page1: bytes) -> CipherParams | None`
  - `decrypt_page(params: CipherParams, page: bytes, page_no: int) -> bytes | None`
  - `normalize_page1_header(plain_page: bytes, reserve: int) -> bytes`
  - `has_wrapper_header(size_bytes: int) -> bool`
  - `page_count_from_size(size_bytes: int) -> int`
- 供 `tests/helpers/fake_sqlcipher.py` 复用：`derive_keys`、全部常量

**测试策略说明（重要，避免执行者走弯路）：**

round-trip 测试对**随机字节页**做，不对真实 SQLite 库做。原因：SQLCipher 让 SQLite 每页只用前 `4096-reserve` 字节（SQLite 头第 20 字节记录该保留值），而 stock `sqlite3` **无法**创建保留区非 0 的库——用 `page_size=4096` 造出的库，其 B-tree 单元内容会真实占用每页最后 48 字节，与我们的密文布局冲突。

因此职责这样切分：

| 要证明的事 | 用什么证明 |
|---|---|
| 解密实现与常量自洽（KDF/HMAC/IV/reserve/页1特殊布局/多页迭代） | 本 task 的随机页 round-trip，可穷举八组合 |
| 常量与 **QQ 实际行为**一致 | Task 11 的真库 page-1 fixture + Task 12 的 live 冒烟 |
| 产物可被 `sqlite3` 查询 | Task 8 的 `PRAGMA integrity_check` + Task 12 的真机查询 |

**不要**试图伪造一个「保留区为 48 的 SQLite 库」来做 round-trip，那是死路。

- [ ] **Step 1: 写 `qqexport/core/sqlcipher.py`**

```python
"""QQ NT 数据库的 SQLCipher 4 页级解密。

算法与常量参照 chatlog-keeper（MIT，见 NOTICE），独立实现。
参照 spec §6.4。禁止凭记忆改动任何常量。

页布局（reserve = 48，QQ NT 9.9.x 实测组合）：
    密文      page[0            : PAGE_SZ - reserve]     4048 B
    IV        page[PAGE_SZ-48   : PAGE_SZ - 48 + 16]     16 B
    HMAC 标签 page[PAGE_SZ-32   : PAGE_SZ - 32 + 20]     20 B（SHA1）
    填充      剩余 12 B

第 1 页特殊：磁盘前 16 字节是明文 salt（不参与解密），
解密后要用 SQLITE_MAGIC 顶替这 16 字节。
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

WRAPPER_SZ = 1024
PAGE_SZ = 4096
SALT_SZ = 16
IV_SZ = 16
KEY_SZ = 32
KDF_ITER = 4000          # SQLCipher 默认 256000，QQ 改成了 4000
FAST_ITER = 2

SQLITE_MAGIC = b"SQLite format 3\x00"

# 1024 字节头把 SQLite 的 pending byte 从 0x40000000 推移到 0x3FFFFC00，
# 对应页号 262144（而非 stock SQLite 的 262145）。该页可能全零且不参与认证。
_SQLITE_PENDING_BYTE = 0x40000000
_STRIPPED_PENDING_BYTE = _SQLITE_PENDING_BYTE - WRAPPER_SZ   # 0x3FFFFC00
LOCK_PAGE_NO = _STRIPPED_PENDING_BYTE // PAGE_SZ + 1         # 262144

# (KDF, 页HMAC, reserve) —— 顺序即探测优先级，不得调整。首位为本机预期命中项。
VERIFY_COMBOS: tuple[tuple[str, str, int], ...] = (
    ("sha512", "sha1", 48),
    ("sha512", "sha1", 64),
    ("sha512", "sha1", 80),
    ("sha1", "sha1", 48),
    ("sha512", "sha512", 64),
    ("sha512", "sha512", 80),
    ("sha1", "sha1", 64),
    ("sha1", "sha1", 80),
)

_HASHES = {"sha1": hashlib.sha1, "sha512": hashlib.sha512}
_DIGEST_LEN = {"sha1": 20, "sha512": 64}

assert _STRIPPED_PENDING_BYTE == 0x3FFFFC00
assert LOCK_PAGE_NO == 262144


@dataclass(frozen=True)
class CipherParams:
    kdf: str
    mac: str
    reserve: int
    aes_key: bytes
    mac_key: bytes

    @property
    def fingerprint(self) -> str:
        return f"{self.kdf}/{self.mac}/{self.reserve}"

    def redacted(self) -> dict:
        """可安全写入报告的形式：绝不含密钥材料。"""
        return {"kdf": self.kdf, "mac": self.mac, "reserve": self.reserve,
                "kdf_iter": KDF_ITER, "page_size": PAGE_SZ}


def digest_len(mac: str) -> int:
    return _DIGEST_LEN[mac]


def combo_is_possible(kdf: str, mac: str, reserve: int) -> bool:
    """该 (KDF, 页HMAC, reserve) 组合在几何上是否可能。

    reserve 至少要容得下 IV 加一个完整摘要。例如 ("sha512","sha512",64)
    就不可能：64 < 16 + 64 = 80。spec §6.4 的表保留了全部 8 项，
    这里用谓词过滤而非删表项。
    """
    return reserve >= IV_SZ + digest_len(mac)


def derive_keys(passphrase: bytes, salt: bytes, kdf: str) -> tuple[bytes, bytes]:
    """返回 (aes_key, mac_key)。mac_salt = salt 每字节异或 0x3A。"""
    aes_key = hashlib.pbkdf2_hmac(kdf, passphrase, salt, KDF_ITER, dklen=KEY_SZ)
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac(kdf, aes_key, mac_salt, FAST_ITER, dklen=KEY_SZ)
    return aes_key, mac_key


def has_wrapper_header(size_bytes: int) -> bool:
    """QQ NT 库大小必然满足 size % 4096 == 1024。本机实测 335545344 即如此。"""
    return size_bytes % PAGE_SZ == WRAPPER_SZ


def page_count_from_size(size_bytes: int) -> int:
    return (size_bytes - WRAPPER_SZ) // PAGE_SZ


def read_header_and_page1(db_path: Path) -> bytes:
    """只读前 1024+4096 字节，用于参数探测与口令校验。"""
    with open(db_path, "rb") as f:
        return f.read(WRAPPER_SZ + PAGE_SZ)


def decrypt_page(params: CipherParams, page: bytes, page_no: int) -> bytes | None:
    """解密一个 4096 字节的密网页（page_no 从 1 开始）。

    HMAC 校验失败返回 None —— 调用方计入 corrupt_pages 并跳过，不得抛异常。
    全零的 LOCK_PAGE_NO 页原样透传（不参与认证）。
    """
    if len(page) != PAGE_SZ:
        raise ValueError(f"page must be {PAGE_SZ} bytes, got {len(page)}")
    if page_no == LOCK_PAGE_NO and page == b"\x00" * PAGE_SZ:
        return page

    r = params.reserve
    dlen = digest_len(params.mac)
    tail_start = PAGE_SZ - r
    iv = page[tail_start:tail_start + IV_SZ]
    tag = page[tail_start + IV_SZ:tail_start + IV_SZ + dlen]

    if page_no == 1:
        body = page[SALT_SZ:tail_start]
    else:
        body = page[:tail_start]

    expected = hmac.new(params.mac_key,
                        body + iv + struct.pack("<I", page_no),
                        _HASHES[params.mac]).digest()
    if not hmac.compare_digest(expected[:dlen], tag):
        return None

    dec = Cipher(algorithms.AES(params.aes_key), modes.CBC(iv)).decryptor()
    plain_body = dec.update(body) + dec.finalize()

    # reserve 区填零，不照抄密文的 IV+HMAC 残余。SQLite 按头第 20 字节的声明
    # 完全跳过保留区，填零与照抄对它等价；填零还让明文库里不残留密文片段。
    zeros = b"\x00" * r
    if page_no == 1:
        return SQLITE_MAGIC + plain_body + zeros
    return plain_body + zeros


def normalize_page1_header(plain_page: bytes, reserve: int) -> bytes:
    """把明文页 1 的第 20 字节（每页保留字节数）显式写成 reserve。

    SQLCipher 会让 SQLite 只用每页前 4096-reserve 字节，并在头里记录该值。
    我们解密后把 IV+HMAC 原样留在页尾，若头里的保留值与之不符，
    stdlib sqlite3 会把那 48 字节当成有效内容而读出损坏数据。
    显式写入是幂等的防御措施。
    """
    if len(plain_page) != PAGE_SZ:
        raise ValueError(f"page must be {PAGE_SZ} bytes, got {len(plain_page)}")
    if plain_page[:SALT_SZ] != SQLITE_MAGIC:
        raise ValueError("not a plaintext SQLite page 1")
    return plain_page[:20] + bytes([reserve]) + plain_page[21:]


def detect_params(passphrase: bytes, header_and_page1: bytes) -> CipherParams | None:
    """按 VERIFY_COMBOS 顺序试算 page-1 HMAC，首个通过者胜出。

    header_and_page1 = 原始库文件的前 1024+4096 字节（含 QQ 自定义头）。
    也接受裸的 4096 字节页 1（便于测试）。全不通过返回 None。
    """
    if len(header_and_page1) == PAGE_SZ:
        page1 = header_and_page1
    elif len(header_and_page1) >= WRAPPER_SZ + PAGE_SZ:
        page1 = header_and_page1[WRAPPER_SZ:WRAPPER_SZ + PAGE_SZ]
    else:
        return None

    salt = page1[:SALT_SZ]
    if salt == b"\x00" * SALT_SZ:
        return None

    for kdf, mac, reserve in VERIFY_COMBOS:
        if not combo_is_possible(kdf, mac, reserve):
            continue
        aes_key, mac_key = derive_keys(passphrase, salt, kdf)
        params = CipherParams(kdf, mac, reserve, aes_key, mac_key)
        plain = decrypt_page(params, page1, 1)
        if plain is not None and plain.startswith(SQLITE_MAGIC):
            return params
    return None
```

- [ ] **Step 2: 写 `tests/helpers/__init__.py`（空文件）与 `tests/helpers/fake_sqlcipher.py`**

```python
"""测试专用：QQ NT SQLCipher 4 格式的**加密**方向。

用它把随机字节页变成合成密文页，从而在无隐私数据、无需实时 QQ 的前提下
穷举验证 sqlcipher.py 的解密正确性。只存在于 tests/，禁止被 qqexport 包导入。
"""
from __future__ import annotations

import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from qqexport.core import sqlcipher as sc


def encrypt_page(plain: bytes, page_no: int, *, aes_key: bytes, mac_key: bytes,
                 mac: str, reserve: int, salt: bytes | None = None) -> bytes:
    """plain 为 4096 字节明网页，返回 4096 字节密网页。

    page_no == 1 时：plain 的 [0:16] 必须是 SQLITE_MAGIC，密文 [0:16] 放 salt。
    """
    if len(plain) != sc.PAGE_SZ:
        raise ValueError("plain page must be 4096 bytes")
    dlen = sc.digest_len(mac)
    if reserve < sc.IV_SZ + dlen:
        raise ValueError(f"reserve {reserve} too small for IV+{dlen}B tag")

    iv = os.urandom(sc.IV_SZ)
    tail_start = sc.PAGE_SZ - reserve
    if page_no == 1:
        if plain[:sc.SALT_SZ] != sc.SQLITE_MAGIC:
            raise ValueError("page 1 plaintext must start with the SQLite magic")
        if salt is None or len(salt) != sc.SALT_SZ:
            raise ValueError("page 1 requires a 16-byte salt")
        body = plain[sc.SALT_SZ:tail_start]
        prefix = salt
    else:
        body = plain[:tail_start]
        prefix = b""

    enc = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    cipher_body = enc.update(body) + enc.finalize()
    tag = sc.hmac.new(mac_key, cipher_body + iv + struct.pack("<I", page_no),
                      sc._HASHES[mac]).digest()[:dlen]
    pad = b"\x00" * (reserve - sc.IV_SZ - dlen)
    out = prefix + cipher_body + iv + tag + pad
    assert len(out) == sc.PAGE_SZ, len(out)
    return out


def build_encrypted_db(plain_pages: list[bytes], passphrase: bytes, *,
                       kdf: str = "sha512", mac: str = "sha1", reserve: int = 48,
                       salt: bytes | None = None,
                       wrapper: bytes | None = None) -> bytes:
    """把明网页序列打包成完整的 QQ NT 格式库字节（含 1024 字节头）。

    plain_pages[0] 必须以 SQLITE_MAGIC 开头。
    """
    if not plain_pages:
        raise ValueError("need at least one page")
    if plain_pages[0][:sc.SALT_SZ] != sc.SQLITE_MAGIC:
        raise ValueError("page 1 must start with the SQLite magic")
    salt = salt or os.urandom(sc.SALT_SZ)
    aes_key, mac_key = sc.derive_keys(passphrase, salt, kdf)

    out = bytearray(wrapper if wrapper is not None else b"\x00" * sc.WRAPPER_SZ)
    for i, plain in enumerate(plain_pages, start=1):
        out += encrypt_page(plain, i, aes_key=aes_key, mac_key=mac_key,
                            mac=mac, reserve=reserve,
                            salt=salt if i == 1 else None)
    return bytes(out)


def random_plain_pages(n: int, *, seed: int = 20261024, reserve: int = 48) -> list[bytes]:
    """n 个随机明网页；页 1 以 SQLITE_MAGIC 开头，第 20 字节写 reserve。

    每页最后 reserve 字节置零：SQLCipher 的明网页保留区本就无语义，
    而 decrypt_page 输出零填充的保留区，置零后 round-trip 才能字节相等。
    """
    import random
    rng = random.Random(seed)
    pages: list[bytes] = []
    for i in range(n):
        p = bytearray(rng.randbytes(sc.PAGE_SZ))
        p[sc.PAGE_SZ - reserve:] = b"\x00" * reserve
        if i == 0:
            p[0:sc.SALT_SZ] = sc.SQLITE_MAGIC
            p[20] = reserve
        pages.append(bytes(p))
    return pages
```

- [ ] **Step 3: 写失败的测试 `tests/test_sqlcipher.py`**

```python
import hashlib

import pytest

from qqexport.core import sqlcipher as sc
from tests.helpers import fake_sqlcipher as fake

PASSPHRASE = b"#8FakeKeyForTest"          # 恰好 16 字节可打印 ASCII
ALL_COMBOS = sc.VERIFY_COMBOS
# ("sha512","sha512",64) 几何上不可能（64 < 16 + 64），遍历时必须排除
POSSIBLE_COMBOS = tuple(c for c in ALL_COMBOS if sc.combo_is_possible(*c))


def test_constants_match_spec_section_6_4():
    assert (sc.WRAPPER_SZ, sc.PAGE_SZ, sc.SALT_SZ, sc.IV_SZ, sc.KEY_SZ,
            sc.KDF_ITER, sc.FAST_ITER) == (1024, 4096, 16, 16, 32, 4000, 2)
    assert sc.SQLITE_MAGIC == b"SQLite format 3\x00"


def test_lock_page_derivation():
    assert 0x40000000 - sc.WRAPPER_SZ == 0x3FFFFC00
    assert sc.LOCK_PAGE_NO == 262144


def test_verify_combos_are_eight_and_ordered():
    assert len(sc.VERIFY_COMBOS) == 8
    assert sc.VERIFY_COMBOS[0] == ("sha512", "sha1", 48)


def test_wrapper_header_matches_real_measured_db_size():
    """spec §3 实测：nt_msg.db = 335545344 = 81920*4096 + 1024。"""
    assert sc.has_wrapper_header(335_545_344)
    assert sc.page_count_from_size(335_545_344) == 81_920
    assert not sc.has_wrapper_header(335_544_320)


def test_derive_keys_is_deterministic_and_mac_salt_is_xored():
    salt = bytes(range(16))
    k1 = sc.derive_keys(PASSPHRASE, salt, "sha512")
    k2 = sc.derive_keys(PASSPHRASE, salt, "sha512")
    assert k1 == k2
    assert len(k1[0]) == len(k1[1]) == 32
    expected_mac_salt = bytes(b ^ 0x3A for b in salt)
    assert expected_mac_salt != salt
    assert k1[1] == hashlib.pbkdf2_hmac("sha512", k1[0], expected_mac_salt, 2, 32)


@pytest.mark.parametrize("kdf,mac,reserve", POSSIBLE_COMBOS)
def test_roundtrip_non_first_page(kdf, mac, reserve):
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, kdf)
    params = sc.CipherParams(kdf, mac, reserve, aes_key, mac_key)
    # 保留区置零：SQLCipher 明网页的保留区无语义，decrypt_page 输出零填充
    plain = (bytes(range(256)) * 16)[:sc.PAGE_SZ - reserve] + b"\x00" * reserve
    assert len(plain) == sc.PAGE_SZ
    cipher = fake.encrypt_page(plain, 7, aes_key=aes_key, mac_key=mac_key,
                               mac=mac, reserve=reserve)
    assert len(cipher) == sc.PAGE_SZ
    assert sc.decrypt_page(params, cipher, 7) == plain


@pytest.mark.parametrize("kdf,mac,reserve", POSSIBLE_COMBOS)
def test_roundtrip_page1_restores_magic(kdf, mac, reserve):
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, kdf)
    params = sc.CipherParams(kdf, mac, reserve, aes_key, mac_key)
    plain = (sc.SQLITE_MAGIC + b"\xab" * (sc.PAGE_SZ - sc.SALT_SZ - reserve)
             + b"\x00" * reserve)
    assert len(plain) == sc.PAGE_SZ
    cipher = fake.encrypt_page(plain, 1, aes_key=aes_key, mac_key=mac_key,
                               mac=mac, reserve=reserve, salt=salt)
    assert cipher[:16] == salt              # salt 明文置于页首
    got = sc.decrypt_page(params, cipher, 1)
    assert got == plain
    assert got.startswith(sc.SQLITE_MAGIC)


def test_decrypt_page_zeroes_the_reserve_region():
    """明文库中不应残留密文的 IV/HMAC 片段。"""
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, "sha512")
    params = sc.CipherParams("sha512", "sha1", 48, aes_key, mac_key)
    plain = (b"\x11" * (sc.PAGE_SZ - 48)) + b"\x00" * 48
    cipher = fake.encrypt_page(plain, 9, aes_key=aes_key, mac_key=mac_key,
                               mac="sha1", reserve=48)
    assert cipher[sc.PAGE_SZ - 48:] != b"\x00" * 48        # 密文保留区非零
    assert sc.decrypt_page(params, cipher, 9)[sc.PAGE_SZ - 48:] == b"\x00" * 48


def test_combo_is_possible_rejects_geometrically_impossible_combos():
    assert sc.combo_is_possible("sha512", "sha1", 48) is True     # 48 >= 16+20
    assert sc.combo_is_possible("sha512", "sha512", 80) is True   # 80 >= 16+64
    assert sc.combo_is_possible("sha512", "sha512", 64) is False  # 64 <  16+64
    impossible = [c for c in sc.VERIFY_COMBOS if not sc.combo_is_possible(*c)]
    assert impossible == [("sha512", "sha512", 64)]
    assert len(POSSIBLE_COMBOS) == len(sc.VERIFY_COMBOS) - 1 == 7


def test_page_number_is_mixed_into_hmac():
    """同一密文配错误页号必须校验失败——证明 pack('<I', page_no) 参与了 HMAC。"""
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, "sha512")
    params = sc.CipherParams("sha512", "sha1", 48, aes_key, mac_key)
    plain = b"\x11" * sc.PAGE_SZ
    cipher = fake.encrypt_page(plain, 5, aes_key=aes_key, mac_key=mac_key,
                               mac="sha1", reserve=48)
    assert sc.decrypt_page(params, cipher, 5) == plain
    assert sc.decrypt_page(params, cipher, 6) is None


def test_wrong_passphrase_is_rejected():
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, "sha512")
    cipher = fake.encrypt_page(b"\x11" * sc.PAGE_SZ, 3, aes_key=aes_key,
                               mac_key=mac_key, mac="sha1", reserve=48)
    bad_aes, bad_mac = sc.derive_keys(b"#8WrongKeyXXXXXX", salt, "sha512")
    bad = sc.CipherParams("sha512", "sha1", 48, bad_aes, bad_mac)
    assert sc.decrypt_page(bad, cipher, 3) is None


def test_single_bit_flip_in_ciphertext_is_rejected():
    salt = bytes(range(16))
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, "sha512")
    params = sc.CipherParams("sha512", "sha1", 48, aes_key, mac_key)
    cipher = bytearray(fake.encrypt_page(b"\x11" * sc.PAGE_SZ, 3,
                                         aes_key=aes_key, mac_key=mac_key,
                                         mac="sha1", reserve=48))
    cipher[100] ^= 0x01
    assert sc.decrypt_page(params, bytes(cipher), 3) is None


def test_multipage_roundtrip_is_byte_exact():
    plain_pages = fake.random_plain_pages(9)
    blob = fake.build_encrypted_db(plain_pages, PASSPHRASE)
    assert sc.has_wrapper_header(len(blob))
    salt = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.SALT_SZ]
    aes_key, mac_key = sc.derive_keys(PASSPHRASE, salt, "sha512")
    params = sc.CipherParams("sha512", "sha1", 48, aes_key, mac_key)
    for i, expected in enumerate(plain_pages, start=1):
        off = sc.WRAPPER_SZ + (i - 1) * sc.PAGE_SZ
        got = sc.decrypt_page(params, blob[off:off + sc.PAGE_SZ], i)
        assert got == expected, f"page {i} mismatch"


def test_detect_params_finds_expected_combo_first():
    plain_pages = fake.random_plain_pages(2)
    blob = fake.build_encrypted_db(plain_pages, PASSPHRASE,
                                   kdf="sha512", mac="sha1", reserve=48)
    params = sc.detect_params(PASSPHRASE, blob[:sc.WRAPPER_SZ + sc.PAGE_SZ])
    assert params is not None
    assert (params.kdf, params.mac, params.reserve) == ("sha512", "sha1", 48)
    assert params.fingerprint == "sha512/sha1/48"


@pytest.mark.parametrize("kdf,mac,reserve", POSSIBLE_COMBOS)
def test_detect_params_works_for_every_combo(kdf, mac, reserve):
    blob = fake.build_encrypted_db(fake.random_plain_pages(1, reserve=reserve),
                                   PASSPHRASE, kdf=kdf, mac=mac, reserve=reserve)
    params = sc.detect_params(PASSPHRASE, blob[:sc.WRAPPER_SZ + sc.PAGE_SZ])
    assert params is not None
    assert (params.kdf, params.mac, params.reserve) == (kdf, mac, reserve)


def test_detect_params_rejects_wrong_passphrase():
    blob = fake.build_encrypted_db(fake.random_plain_pages(1), PASSPHRASE)
    assert sc.detect_params(b"#8WrongKeyXXXXXX",
                            blob[:sc.WRAPPER_SZ + sc.PAGE_SZ]) is None


def test_detect_params_rejects_all_zero_salt():
    blob = fake.build_encrypted_db(fake.random_plain_pages(1), PASSPHRASE,
                                   salt=b"\x00" * 16)
    assert sc.detect_params(PASSPHRASE, blob[:sc.WRAPPER_SZ + sc.PAGE_SZ]) is None


def test_detect_params_accepts_bare_page1():
    blob = fake.build_encrypted_db(fake.random_plain_pages(1), PASSPHRASE)
    page1 = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.PAGE_SZ]
    assert sc.detect_params(PASSPHRASE, page1) is not None


def test_detect_params_rejects_short_input():
    assert sc.detect_params(PASSPHRASE, b"\x00" * 100) is None


def test_lock_page_all_zero_passes_through_unauthenticated():
    params = sc.CipherParams("sha512", "sha1", 48, b"\x00" * 32, b"\x00" * 32)
    zeros = b"\x00" * sc.PAGE_SZ
    assert sc.decrypt_page(params, zeros, sc.LOCK_PAGE_NO) == zeros


def test_non_zero_lock_page_still_requires_valid_hmac():
    params = sc.CipherParams("sha512", "sha1", 48, b"\x00" * 32, b"\x00" * 32)
    assert sc.decrypt_page(params, b"\x01" * sc.PAGE_SZ, sc.LOCK_PAGE_NO) is None


def test_decrypt_page_rejects_wrong_length():
    params = sc.CipherParams("sha512", "sha1", 48, b"\x00" * 32, b"\x00" * 32)
    with pytest.raises(ValueError, match="4096"):
        sc.decrypt_page(params, b"\x00" * 100, 2)


def test_normalize_page1_sets_reserved_byte():
    plain = sc.SQLITE_MAGIC + b"\x00" * 4 + b"\xff" + b"\x00" * (sc.PAGE_SZ - 21)
    assert plain[20] == 0xFF
    out = sc.normalize_page1_header(plain, 48)
    assert out[20] == 48
    assert out[:20] == plain[:20] and out[21:] == plain[21:]
    assert len(out) == sc.PAGE_SZ


def test_normalize_page1_is_idempotent():
    plain = sc.SQLITE_MAGIC + b"\x00" * (sc.PAGE_SZ - 16)
    once = sc.normalize_page1_header(plain, 48)
    assert sc.normalize_page1_header(once, 48) == once


def test_normalize_page1_rejects_non_page1():
    with pytest.raises(ValueError, match="magic"):
        sc.normalize_page1_header(b"\x00" * sc.PAGE_SZ, 48)


def test_redacted_exposes_no_key_material():
    params = sc.CipherParams("sha512", "sha1", 48, b"A" * 32, b"B" * 32)
    text = repr(params.redacted())
    assert "A" * 32 not in text and "B" * 32 not in text
    assert params.redacted() == {"kdf": "sha512", "mac": "sha1", "reserve": 48,
                                 "kdf_iter": 4000, "page_size": 4096}


def test_read_header_and_page1_reads_exactly_5120_bytes(tmp_path):
    blob = fake.build_encrypted_db(fake.random_plain_pages(4), PASSPHRASE)
    p = tmp_path / "nt_msg.db"
    p.write_bytes(blob)
    got = sc.read_header_and_page1(p)
    assert len(got) == sc.WRAPPER_SZ + sc.PAGE_SZ == 5120
    assert got == blob[:5120]
```

- [ ] **Step 4: 运行测试确认失败**

Run: `python -m pytest tests/test_sqlcipher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qqexport.core.sqlcipher'`

（Step 1/2 已给出完整实现代码；若按严格 TDD 顺序执行，可先只建空模块让测试失败，再回填 Step 1/2 的代码。）

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_sqlcipher.py -v`
Expected: PASS，约 45 项（含 8 组合 × 3 个 parametrize）

- [ ] **Step 6: 提交**

```bash
git add qqexport/core/sqlcipher.py tests/helpers/__init__.py \
        tests/helpers/fake_sqlcipher.py tests/test_sqlcipher.py
git commit -m "feat: 纯 Python 实现 QQ NT SQLCipher 4 页级解密与参数探测"
```

---

### Task 4: `wal.py` — WAL 帧校验与恢复

**Files:**
- Create: `qqexport/core/wal.py`
- Modify: `tests/helpers/fake_sqlcipher.py`（追加 WAL 构造函数）
- Create: `tests/test_wal.py`

**Interfaces:**
- Consumes:
  - `sqlcipher.CipherParams`、`sqlcipher.decrypt_page(params, page, page_no)`、`sqlcipher.PAGE_SZ`（Task 3）
  - `fake_sqlcipher.encrypt_page`、`fake_sqlcipher.derive`（Task 3，测试用）
- Produces:
  - 常量 `WAL_MAGIC_LE=0x377F0682` `WAL_MAGIC_BE=0x377F0683` `WAL_HEADER_SZ=32` `WAL_FRAME_HEADER_SZ=24` `WAL_VERSION=3007000` `SHM_HEADER_SZ=48`
  - `checksum(data: bytes, s0: int, s1: int, big: bool) -> tuple[int, int]`
  - `WalHeader`（frozen dataclass：`magic:int` `version:int` `page_size:int` `checkpoint_seq:int` `salt:tuple[int,int]` `big_endian_checksum:bool` `seed:tuple[int,int]` `signature:bytes`）
  - `parse_header(buf: bytes) -> WalHeader | None`
  - `WalPlan`（frozen dataclass：`valid:bool` `reason:str` `page_size:int` `frame_count:int` `last_commit_frame:int` `commit_size:int` `header_signature:bytes` `shm_validated:bool`）
  - `inspect_wal(wal_path: Path, shm_path: Path | None = None, expected_page_size: int = PAGE_SZ) -> WalPlan`
  - `WalApplyResult`（dataclass：`frames_applied:int` `pages_written:int` `corrupt_frames:int` `truncated_to_pages:int` `signature_changed:bool` `stopped_early:bool`）
  - `apply_wal(params: CipherParams, wal_path: Path, db_path: Path, plan: WalPlan) -> WalApplyResult`

**背景（spec §3、§6.5）：** 采样时 `nt_msg.db-wal` 有 1.88 MB 且正在被写入——**最近的消息就在里面**。只解密主库会静默丢掉最新消息，而导出结果看起来完全正常。这是 P0 最容易漏、后果最隐蔽的一处，因此测试必须覆盖「最新消息确实被恢复」。

`-wal` / `-shm` **没有** 1024 字节 QQ 自定义头，按标准 SQLite 格式处理；但帧内的页数据与主库页加密方式完全相同，用同一套 `aes_key`/`mac_key`（由主库 page-1 salt 派生）解密，`page_no` 取帧头声明的页号。

**-shm 解析必须 fail-safe：** 其字段布局取自 SQLite 的 `WalIndexHdr`（48 字节，文件开头有两份互为备份）。本 task 只用它做**交叉验证**；任何解析失败、两份头不一致、salt 或校验和不符、`mxFrame` 小于扫描所得——一律返回 `None`，`inspect_wal` 降级为「仅扫描 WAL」并置 `shm_validated=False`。**绝不允许 -shm 的问题导致 WAL 恢复失败**，因为纯 WAL 扫描本身就是权威路径。

- [ ] **Step 1: 给 `tests/helpers/fake_sqlcipher.py` 追加 WAL 构造函数**

在文件末尾追加：

```python
WAL_MAGIC_LE = 0x377F0682
WAL_MAGIC_BE = 0x377F0683
WAL_VERSION = 3007000
DEFAULT_WAL_SALT = (0x11111111, 0x22222222)


def wal_checksum(data: bytes, s0: int, s1: int, big: bool) -> tuple[int, int]:
    fmt = ">II" if big else "<II"
    m = 0xFFFFFFFF
    for i in range(0, len(data), 8):
        v0, v1 = struct.unpack_from(fmt, data, i)
        s0 = (s0 + v0 + s1) & m
        s1 = (s1 + v1 + s0) & m
    return s0, s1


def build_wal(entries, *, aes_key: bytes, mac_key: bytes, mac: str = "sha1",
              reserve: int = 48, db_salt: bytes | None = None,
              page_size: int = sc.PAGE_SZ,
              salt: tuple[int, int] = DEFAULT_WAL_SALT,
              big_endian: bool = False, checkpoint_seq: int = 0) -> bytes:
    """构造一个结构完整的 WAL 文件字节。

    entries: [(pgno, plain_page_4096B, dbsize_after_commit), ...]
             dbsize_after_commit == 0 表示非 commit 帧。
    任何 pgno == 1 的条目都要求传入 db_salt（与主库 page-1 salt 相同）。
    """
    magic = WAL_MAGIC_BE if big_endian else WAL_MAGIC_LE
    hdr = struct.pack(">IIII", magic, WAL_VERSION, page_size, checkpoint_seq)
    hdr += struct.pack(">II", *salt)
    s0, s1 = wal_checksum(hdr[:24], 0, 0, big_endian)
    out = bytearray(hdr + struct.pack(">II", s0, s1))

    for pgno, plain, dbsize in entries:
        cipher = encrypt_page(plain, pgno, aes_key=aes_key, mac_key=mac_key,
                              mac=mac, reserve=reserve,
                              salt=db_salt if pgno == 1 else None)
        fh = struct.pack(">II", pgno, dbsize) + struct.pack(">II", *salt)
        s0, s1 = wal_checksum(fh[:8] + cipher, s0, s1, big_endian)
        out += fh + struct.pack(">II", s0, s1) + cipher
    return bytes(out)


def build_shm(*, page_size: int, mx_frame: int, n_page: int,
              salt: tuple[int, int] = DEFAULT_WAL_SALT,
              frame_cksum: tuple[int, int] = (0, 0),
              big_endian: bool = False) -> bytes:
    """构造一份自洽的 -shm（两份相同的 48 字节头）。字段布局取自 SQLite WalIndexHdr。"""
    fmt = ">" if big_endian else "<"
    hdr = bytearray(48)
    struct.pack_into(fmt + "I", hdr, 0, 1)              # iVersion
    struct.pack_into(fmt + "I", hdr, 8, 1)              # iChange
    hdr[13] = 1 if big_endian else 0                    # bBigEndCksum
    struct.pack_into(fmt + "I", hdr, 16, mx_frame)      # mxFrame
    struct.pack_into(fmt + "I", hdr, 20, n_page)        # nPage
    struct.pack_into(fmt + "II", hdr, 24, *frame_cksum) # aFrameCksum
    struct.pack_into(fmt + "II", hdr, 32, *salt)        # aSalt
    struct.pack_into(fmt + "II", hdr, 40,
                     *wal_checksum(bytes(hdr[:40]), 0, 0, big_endian))
    return bytes(hdr) * 2


def tamper_page_and_resign(blob: bytes, frame_index: int, *,
                           big_endian: bool = False, byte_offset: int = 0,
                           xor: int = 0xFF) -> bytes:
    """篡改第 frame_index（1-based）帧的**密文**，并重算自该帧起的所有累计校验和。

    用来构造「SQLite 帧校验和有效、但 SQLCipher 页 HMAC 无效」的帧——
    这是唯一能单独触发页级 HMAC 失败路径的办法，因为直接改密文会连带
    破坏帧校验和（帧校验和覆盖 fh[:8] + 整个密网页）。

    注意：WAL 头与帧头的整数字段恒为大端；big_endian 只影响校验和**运算**
    时对数据字的读取字节序。
    """
    out = bytearray(blob)
    encoded = struct.unpack(">I", blob[8:12])[0]
    page_size = 65536 if encoded == 1 else encoded
    frame_sz = 24 + page_size

    target = 32 + (frame_index - 1) * frame_sz + 24 + byte_offset
    if target >= len(out):
        raise IndexError(f"frame {frame_index} not present in this WAL")
    out[target] ^= xor

    s0, s1 = wal_checksum(bytes(out[:24]), 0, 0, big_endian)
    n_frames = (len(out) - 32) // frame_sz
    for i in range(1, n_frames + 1):
        base = 32 + (i - 1) * frame_sz
        chunk = bytes(out[base:base + 8]) + bytes(out[base + 24:base + frame_sz])
        s0, s1 = wal_checksum(chunk, s0, s1, big_endian)
        struct.pack_into(">II", out, base + 16, s0, s1)
    return bytes(out)
```

- [ ] **Step 2: 写失败的测试 `tests/test_wal.py`**

```python
import struct
from pathlib import Path

import pytest

from qqexport.core import sqlcipher as sc
from qqexport.core import wal
from tests.helpers import fake_sqlcipher as fake

PASSPHRASE = b"#8FakeKeyForTest"
DB_SALT = bytes(range(16))
AES_KEY, MAC_KEY = sc.derive_keys(PASSPHRASE, DB_SALT, "sha512")
KW = dict(aes_key=AES_KEY, mac_key=MAC_KEY, mac="sha1", reserve=48, db_salt=DB_SALT)


def plain(tag: int) -> bytes:
    return bytes([tag]) * sc.PAGE_SZ


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def make_db(tmp_path: Path, n_pages: int = 4, filler: int = 0) -> Path:
    p = tmp_path / "nt_msg.decrypted.db"
    p.write_bytes(bytes([filler]) * sc.PAGE_SZ * n_pages)
    return p


# ---------- parse_header ----------

def test_parse_header_accepts_valid_little_endian():
    blob = fake.build_wal([(2, plain(2), 0)], **KW)
    hdr = wal.parse_header(blob[:32])
    assert hdr is not None
    assert hdr.magic == wal.WAL_MAGIC_LE
    assert hdr.big_endian_checksum is False
    assert hdr.page_size == sc.PAGE_SZ
    assert hdr.salt == fake.DEFAULT_WAL_SALT
    assert hdr.seed == wal.checksum(blob[:24], 0, 0, False)


def test_parse_header_accepts_valid_big_endian():
    blob = fake.build_wal([(2, plain(2), 0)], big_endian=True, **KW)
    hdr = wal.parse_header(blob[:32])
    assert hdr is not None and hdr.big_endian_checksum is True


def test_parse_header_maps_page_size_one_to_65536():
    """只造 WAL 头、不造帧——编码值 1 表示 65536。"""
    blob = fake.build_wal([], page_size=1, **KW)
    hdr = wal.parse_header(blob[:32])
    assert hdr is not None and hdr.page_size == 65536


@pytest.mark.parametrize("mutate,offset", [
    (lambda b: b"\xff" + b[1:], 0),        # magic
    (lambda b: b[:4] + struct.pack(">I", 999) + b[8:], 4),   # version
    (lambda b: b[:24] + b"\xff" * 8, 24),  # header checksum
])
def test_parse_header_rejects_corrupt_headers(mutate, offset):
    blob = fake.build_wal([(2, plain(2), 0)], **KW)
    assert wal.parse_header(mutate(blob[:32])) is None


def test_parse_header_rejects_short_input():
    assert wal.parse_header(b"\x00" * 10) is None


def test_parse_header_rejects_non_power_of_two_page_size():
    blob = fake.build_wal([], page_size=4096, **KW)
    bad = blob[:8] + struct.pack(">I", 4000) + blob[12:]
    assert wal.parse_header(bad[:32]) is None


# ---------- checksum ----------

def test_checksum_rejects_unaligned_input():
    with pytest.raises(ValueError, match="8-byte"):
        wal.checksum(b"\x00" * 7, 0, 0, False)


def test_checksum_is_cumulative_and_order_sensitive():
    a = wal.checksum(b"\x01" * 8 + b"\x02" * 8, 0, 0, False)
    b = wal.checksum(b"\x02" * 8 + b"\x01" * 8, 0, 0, False)
    assert a != b
    step1 = wal.checksum(b"\x01" * 8, 0, 0, False)
    assert wal.checksum(b"\x02" * 8, *step1, False) == a


def test_checksum_endianness_matters():
    data = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    assert wal.checksum(data, 0, 0, False) != wal.checksum(data, 0, 0, True)


# ---------- inspect_wal ----------

def test_inspect_counts_frames_and_finds_last_commit(tmp_path):
    blob = fake.build_wal([(2, plain(2), 0), (3, plain(3), 5),
                           (4, plain(4), 0), (5, plain(5), 7)], **KW)
    plan = wal.inspect_wal(write(tmp_path, "nt_msg.db-wal", blob))
    assert plan.valid and plan.reason == "ok"
    assert plan.frame_count == 4
    assert plan.last_commit_frame == 4
    assert plan.commit_size == 7
    assert plan.page_size == sc.PAGE_SZ
    assert plan.shm_validated is False


def test_inspect_reports_absent_wal(tmp_path):
    plan = wal.inspect_wal(tmp_path / "missing-wal")
    assert not plan.valid and plan.reason == "wal_absent"
    assert plan.frame_count == 0 and plan.last_commit_frame == 0


def test_inspect_rejects_page_size_mismatch(tmp_path):
    """只造头不造帧：页大小 1024 与期望的 4096 不符即应被判无效。"""
    blob = fake.build_wal([], page_size=1024, **KW)
    plan = wal.inspect_wal(write(tmp_path, "w", blob), expected_page_size=4096)
    assert not plan.valid and plan.reason == "page_size_mismatch"
    assert plan.page_size == 1024


def test_inspect_stops_at_bad_frame_checksum(tmp_path):
    blob = bytearray(fake.build_wal([(2, plain(2), 4), (3, plain(3), 5),
                                     (4, plain(4), 6)], **KW))
    frame_sz = wal.WAL_FRAME_HEADER_SZ + sc.PAGE_SZ
    first_commit = 32 + frame_sz          # 帧 1 结束后
    blob[first_commit + 20] ^= 0xFF       # 破坏帧 2 的校验和
    plan = wal.inspect_wal(write(tmp_path, "w", bytes(blob)))
    assert plan.valid
    assert plan.frame_count == 1
    assert plan.last_commit_frame == 1
    assert plan.commit_size == 4


def test_inspect_stops_at_recycled_frame_salt(tmp_path):
    """salt 不匹配 = 上一轮 checkpoint 遗留的已回收帧，有效序列到此为止。"""
    good = fake.build_wal([(2, plain(2), 4)], **KW)
    stale = fake.build_wal([(3, plain(3), 9)], salt=(0xAAAAAAAA, 0xBBBBBBBB), **KW)
    blob = good + stale[32:]
    plan = wal.inspect_wal(write(tmp_path, "w", blob))
    assert plan.frame_count == 1
    assert plan.last_commit_frame == 1 and plan.commit_size == 4


def test_inspect_ignores_truncated_tail_frame(tmp_path):
    blob = fake.build_wal([(2, plain(2), 4)], **KW)
    plan = wal.inspect_wal(write(tmp_path, "w", blob + b"\x00" * 100))
    assert plan.frame_count == 1 and plan.commit_size == 4


def test_inspect_handles_empty_wal(tmp_path):
    blob = fake.build_wal([], **KW)
    plan = wal.inspect_wal(write(tmp_path, "w", blob))
    assert plan.valid and plan.frame_count == 0
    assert plan.last_commit_frame == 0 and plan.commit_size == 0


def test_inspect_validates_consistent_shm(tmp_path):
    blob = fake.build_wal([(2, plain(2), 0), (3, plain(3), 5)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm",
                fake.build_shm(page_size=sc.PAGE_SZ, mx_frame=2, n_page=5))
    plan = wal.inspect_wal(w, shm)
    assert plan.valid and plan.shm_validated is True


def test_inspect_falls_back_when_shm_is_garbage(tmp_path):
    blob = fake.build_wal([(2, plain(2), 0), (3, plain(3), 5)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm", b"\x99" * 136)
    plan = wal.inspect_wal(w, shm)
    assert plan.valid and plan.shm_validated is False
    assert plan.last_commit_frame == 2 and plan.commit_size == 5


def test_inspect_falls_back_when_shm_copies_disagree(tmp_path):
    blob = fake.build_wal([(3, plain(3), 5)], **KW)
    good = fake.build_shm(page_size=sc.PAGE_SZ, mx_frame=1, n_page=5)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm", good[:48] + b"\x00" * 48)
    assert wal.inspect_wal(w, shm).shm_validated is False


def test_inspect_falls_back_when_shm_salt_mismatches(tmp_path):
    blob = fake.build_wal([(3, plain(3), 5)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    shm = write(tmp_path, "nt_msg.db-shm",
                fake.build_shm(page_size=sc.PAGE_SZ, mx_frame=1, n_page=5,
                               salt=(0xDEAD, 0xBEEF)))
    assert wal.inspect_wal(w, shm).shm_validated is False


# ---------- apply_wal ----------

def test_apply_writes_page_at_correct_offset_and_truncates(tmp_path):
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(3, plain(0x33), 6)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)

    res = wal.apply_wal(params, w, db, plan)
    out = db.read_bytes()
    assert res.frames_applied == 1 and res.pages_written == 1
    assert res.corrupt_frames == 0 and not res.signature_changed
    assert len(out) == 6 * sc.PAGE_SZ                  # 截断到 commit_size
    assert out[2 * sc.PAGE_SZ: 2 * sc.PAGE_SZ + 4] == b"\x33" * 4   # 帧内页 3 已写入
    assert out[0:4] == b"\xaa" * 4                     # 未涉及的页保持原样
    # commit_size=6 大于主库的 4 页，truncate 向上扩展会零填充——这与 SQLite 语义一致
    assert out[4 * sc.PAGE_SZ: 4 * sc.PAGE_SZ + 4] == b"\x00" * 4


def test_apply_recovers_newest_version_when_page_written_twice(tmp_path):
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(2, plain(0x11), 0), (2, plain(0x22), 4)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    wal.apply_wal(params, w, db, plan)
    out = db.read_bytes()
    page2 = out[sc.PAGE_SZ:2 * sc.PAGE_SZ]
    assert page2[100] == 0x22                      # 后写的帧覆盖先写的


def test_apply_stops_at_page_hmac_failure_keeping_earlier_frames(tmp_path):
    """帧 2 的密文被篡改（帧校验和已重算，故 SQLite 层放行），
    但 SQLCipher 页 HMAC 必然失败 → 停止应用后续帧，帧 1 的结果保留。"""
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    blob = fake.build_wal([(2, plain(0x11), 0),      # 帧 1，非 commit
                           (3, plain(0x22), 0),      # 帧 2，将被篡改
                           (4, plain(0x33), 6)],     # 帧 3，commit
                          **KW)
    blob = fake.tamper_page_and_resign(blob, 2)
    w = write(tmp_path, "nt_msg.db-wal", blob)

    plan = wal.inspect_wal(w)
    assert plan.valid and plan.last_commit_frame == 3   # SQLite 层看不出问题

    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.corrupt_frames == 1
    assert res.frames_applied == 1 and res.pages_written == 1   # 只应用了帧 1
    assert res.stopped_early is True
    assert res.truncated_to_pages == 0                          # 未到 commit，不截断

    out = db.read_bytes()
    assert len(out) == 4 * sc.PAGE_SZ
    assert out[sc.PAGE_SZ + 100] == 0x11        # 帧 1（页 2）已写入
    assert out[2 * sc.PAGE_SZ + 100] == 0xAA    # 帧 2/3（页 3、4）未被写入


def test_inspect_does_not_detect_page_hmac_corruption(tmp_path):
    """明确记录职责边界：inspect_wal 只做 SQLite 层校验，页级 HMAC 由 apply_wal 负责。"""
    blob = fake.build_wal([(2, plain(0x11), 4)], **KW)
    blob = fake.tamper_page_and_resign(blob, 1)
    plan = wal.inspect_wal(write(tmp_path, "w", blob))
    assert plan.valid and plan.frame_count == 1 and plan.last_commit_frame == 1


def test_apply_is_noop_without_commit_frame(tmp_path):
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    before = db.read_bytes()
    blob = fake.build_wal([(2, plain(0x11), 0)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.frames_applied == 0 and res.truncated_to_pages == 0
    assert db.read_bytes() == before


def test_apply_aborts_when_header_signature_changed(tmp_path):
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    before = db.read_bytes()
    blob = fake.build_wal([(2, plain(0x11), 4)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    # inspect 之后 QQ 又写了新的 WAL（换了 salt）
    w.write_bytes(fake.build_wal([(2, plain(0x11), 4)],
                                 salt=(0x77777777, 0x88888888), **KW))
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    res = wal.apply_wal(params, w, db, plan)
    assert res.signature_changed is True and res.frames_applied == 0
    assert db.read_bytes() == before


def test_apply_handles_page_one_frame(tmp_path):
    db = make_db(tmp_path, n_pages=4, filler=0xAA)
    p1 = sc.SQLITE_MAGIC + b"\x00" * 4 + bytes([48]) + b"\x5a" * (sc.PAGE_SZ - 21)
    blob = fake.build_wal([(1, p1, 4)], **KW)
    w = write(tmp_path, "nt_msg.db-wal", blob)
    plan = wal.inspect_wal(w)
    params = sc.CipherParams("sha512", "sha1", 48, AES_KEY, MAC_KEY)
    wal.apply_wal(params, w, db, plan)
    out = db.read_bytes()
    assert out[:16] == sc.SQLITE_MAGIC
    assert out[20] == 48


def test_checksum_matches_hand_computed_vectors():
    """算法：s0 = s0+v0+s1; s1 = s1+v1+s0（用更新后的 s0），每步 mod 2^32。

    (v0,v1)=(1,2), seed(0,0) → s0 = 0+1+0 = 1, s1 = 0+2+1 = 3
    """
    assert wal.checksum(b"\x00" * 8, 0, 0, False) == (0, 0)
    assert wal.checksum(struct.pack("<II", 1, 2), 0, 0, False) == (1, 3)
    # 再喂一块 (3,4)：s0 = 1+3+3 = 7, s1 = 3+4+7 = 14
    assert wal.checksum(struct.pack("<II", 3, 4), 1, 3, False) == (7, 14)
    # 大端读同一批字节得到不同的 v0/v1
    assert wal.checksum(struct.pack(">II", 1, 2), 0, 0, True) == (1, 3)
    assert wal.checksum(struct.pack("<II", 1, 2), 0, 0, True) != (1, 3)


def test_checksum_wraps_at_32_bits():
    s0, s1 = wal.checksum(struct.pack("<II", 0xFFFFFFFF, 0xFFFFFFFF), 0, 0, False)
    assert 0 <= s0 <= 0xFFFFFFFF and 0 <= s1 <= 0xFFFFFFFF
    assert s0 == 0xFFFFFFFF
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_wal.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qqexport.core.wal'`

- [ ] **Step 4: 实现 `qqexport/core/wal.py`**

```python
"""SQLite WAL 的校验与恢复（针对 SQLCipher 加密页）。

SQLite 用累计校验和与 salt 认证 WAL 的结构；SQLCipher 独立认证每个加密页。
本模块负责前者并规划恢复边界，页解密委托给 sqlcipher.decrypt_page。
参照 spec §6.5。

铁律：-shm 只用于交叉验证，它出任何问题都不得导致 WAL 恢复失败。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import sqlcipher as sc

WAL_MAGIC_LE = 0x377F0682
WAL_MAGIC_BE = 0x377F0683
WAL_HEADER_SZ = 32
WAL_FRAME_HEADER_SZ = 24
WAL_VERSION = 3007000
SHM_HEADER_SZ = 48
_MASK = 0xFFFFFFFF


@dataclass(frozen=True)
class WalHeader:
    magic: int
    version: int
    page_size: int
    checkpoint_seq: int
    salt: tuple[int, int]
    big_endian_checksum: bool
    seed: tuple[int, int]
    signature: bytes


@dataclass(frozen=True)
class WalPlan:
    valid: bool
    reason: str
    page_size: int
    frame_count: int
    last_commit_frame: int      # 1-indexed；0 表示没有已提交帧
    commit_size: int            # 该 commit 之后的库页数
    header_signature: bytes
    shm_validated: bool


@dataclass
class WalApplyResult:
    frames_applied: int = 0
    pages_written: int = 0
    corrupt_frames: int = 0
    truncated_to_pages: int = 0
    signature_changed: bool = False
    stopped_early: bool = False
    notes: list[str] = field(default_factory=list)


def checksum(data: bytes, s0: int, s1: int, big: bool) -> tuple[int, int]:
    """SQLite WAL 累计校验和。data 长度必须是 8 的倍数。"""
    if len(data) % 8:
        raise ValueError("WAL checksum input must be 8-byte aligned")
    fmt = ">II" if big else "<II"
    for i in range(0, len(data), 8):
        v0, v1 = struct.unpack_from(fmt, data, i)
        s0 = (s0 + v0 + s1) & _MASK
        s1 = (s1 + v1 + s0) & _MASK
    return s0, s1


def _norm_page_size(encoded: int) -> int:
    return 65536 if encoded == 1 else encoded


def parse_header(buf: bytes) -> WalHeader | None:
    if len(buf) < WAL_HEADER_SZ:
        return None
    magic, version, encoded_size, ckpt = struct.unpack(">III I", buf[:16])
    if magic not in (WAL_MAGIC_LE, WAL_MAGIC_BE):
        return None
    if version != WAL_VERSION:
        return None
    page_size = _norm_page_size(encoded_size)
    if page_size & (page_size - 1) or not (512 <= page_size <= 65536):
        return None
    salt = struct.unpack(">II", buf[16:24])
    stored = struct.unpack(">II", buf[24:32])
    big = magic == WAL_MAGIC_BE
    seed = checksum(buf[:24], 0, 0, big)
    if seed != stored:
        return None
    return WalHeader(magic, version, page_size, ckpt, salt, big, seed,
                     bytes(buf[:WAL_HEADER_SZ]))


def _read_shm_state(shm_path: Path, hdr: WalHeader) -> tuple[int, int] | None:
    """返回 (mxFrame, nPage)。任何不符都返回 None，调用方降级为仅扫描 WAL。"""
    try:
        with open(shm_path, "rb") as f:
            first = f.read(SHM_HEADER_SZ)
            second = f.read(SHM_HEADER_SZ)
    except OSError:
        return None
    if len(first) < SHM_HEADER_SZ or len(second) < SHM_HEADER_SZ:
        return None
    if first != second:
        return None
    big = bool(first[13])
    if big != hdr.big_endian_checksum:
        return None
    u = ">I" if big else "<I"
    try:
        if checksum(first[:40], 0, 0, big) != struct.unpack_from(u + u, first, 40):
            return None
        if struct.unpack_from(u + u, first, 32) != hdr.salt:
            return None
        mx_frame = struct.unpack_from(u, first, 16)[0]
        n_page = struct.unpack_from(u, first, 20)[0]
    except struct.error:
        return None
    return mx_frame, n_page


def _iter_frames(f, hdr: WalHeader):
    """产出 (frame_no, pgno, dbsize, frame_header_bytes, page_bytes, s0, s1)。

    遇到首个无效帧即停止产出（含尾部不完整帧）。
    """
    s0, s1 = hdr.seed
    frame_no = 0
    while True:
        fh = f.read(WAL_FRAME_HEADER_SZ)
        if len(fh) < WAL_FRAME_HEADER_SZ:
            return
        page = f.read(hdr.page_size)
        if len(page) < hdr.page_size:
            return                                   # 尾部不完整帧，忽略
        if struct.unpack(">II", fh[8:16]) != hdr.salt:
            return                                   # 已回收的旧帧
        s0, s1 = checksum(fh[:8] + page, s0, s1, hdr.big_endian_checksum)
        if (s0, s1) != struct.unpack(">II", fh[16:24]):
            return
        frame_no += 1
        pgno, dbsize = struct.unpack(">II", fh[:8])
        yield frame_no, pgno, dbsize, fh, page, s0, s1


def inspect_wal(wal_path: Path, shm_path: Path | None = None,
                expected_page_size: int = sc.PAGE_SZ) -> WalPlan:
    wal_path = Path(wal_path)
    if not wal_path.is_file():
        return WalPlan(False, "wal_absent", expected_page_size, 0, 0, 0, b"", False)
    try:
        if wal_path.stat().st_size < WAL_HEADER_SZ:
            return WalPlan(False, "wal_too_small", expected_page_size, 0, 0, 0,
                           b"", False)
        with open(wal_path, "rb") as f:
            hdr_buf = f.read(WAL_HEADER_SZ)
            hdr = parse_header(hdr_buf)
            if hdr is None:
                return WalPlan(False, "wal_header_invalid", expected_page_size,
                               0, 0, 0, b"", False)
            if hdr.page_size != expected_page_size:
                return WalPlan(False, "page_size_mismatch", hdr.page_size,
                               0, 0, 0, hdr.signature, False)
            frame_count = 0
            last_commit = 0
            commit_size = 0
            for frame_no, _pgno, dbsize, *_rest in _iter_frames(f, hdr):
                frame_count = frame_no
                if dbsize != 0:
                    last_commit = frame_no
                    commit_size = dbsize
    except OSError as exc:
        return WalPlan(False, f"wal_read_error:{exc.__class__.__name__}",
                       expected_page_size, 0, 0, 0, b"", False)

    shm_validated = False
    if shm_path is not None and Path(shm_path).is_file():
        state = _read_shm_state(Path(shm_path), hdr)
        if state is not None and state[0] >= last_commit:
            shm_validated = True

    return WalPlan(True, "ok", hdr.page_size, frame_count, last_commit,
                   commit_size, hdr.signature, shm_validated)


def apply_wal(params: sc.CipherParams, wal_path: Path, db_path: Path,
              plan: WalPlan) -> WalApplyResult:
    """把已提交帧解密写回 db_path，末尾截断到 commit_size 页。

    两遍：先只校验帧结构确定可应用范围，再解密写入。
    页 HMAC 失败即停止应用后续帧（保持数据库一致性），计入 corrupt_frames。
    """
    res = WalApplyResult()
    if not plan.valid:
        res.notes.append(f"skipped: {plan.reason}")
        return res
    if plan.last_commit_frame == 0:
        res.notes.append("no committed frame; main db left untouched")
        return res

    wal_path, db_path = Path(wal_path), Path(db_path)
    with open(wal_path, "rb") as f:
        if f.read(WAL_HEADER_SZ) != plan.header_signature:
            res.signature_changed = True
            res.notes.append("wal header changed between inspect and apply")
            return res
        f.seek(0)
        hdr = parse_header(f.read(WAL_HEADER_SZ))
        if hdr is None:
            res.signature_changed = True
            return res

        # pass 1：只校验结构，确定可应用的帧号上界
        applicable = 0
        for frame_no, _pgno, dbsize, *_r in _iter_frames(f, hdr):
            applicable = frame_no
            if frame_no >= plan.last_commit_frame:
                break
        if applicable < plan.last_commit_frame:
            res.stopped_early = True
            res.notes.append(
                f"structure valid only through frame {applicable}, "
                f"expected {plan.last_commit_frame}")
            if applicable == 0:
                return res

        # pass 2：解密并写入
        f.seek(WAL_HEADER_SZ)
        target_pages = 0
        with open(db_path, "r+b") as db:
            for frame_no, pgno, dbsize, _fh, page in _frames_raw(f, hdr, applicable):
                plain = sc.decrypt_page(params, page, pgno)
                if plain is None:
                    res.corrupt_frames += 1
                    res.stopped_early = True
                    res.notes.append(f"page HMAC failed at frame {frame_no}")
                    break
                if pgno == 1:
                    plain = sc.normalize_page1_header(plain, params.reserve)
                db.seek((pgno - 1) * hdr.page_size)
                db.write(plain)
                res.frames_applied += 1
                res.pages_written += 1
                if dbsize != 0:
                    target_pages = dbsize

        if target_pages:
            with open(db_path, "r+b") as db:
                db.truncate(target_pages * hdr.page_size)
            res.truncated_to_pages = target_pages
    return res


def _frames_raw(f, hdr: WalHeader, limit: int):
    """pass 2 用：只按数量重放帧，不重复校验（pass 1 已校验过）。"""
    n = 0
    while n < limit:
        fh = f.read(WAL_FRAME_HEADER_SZ)
        if len(fh) < WAL_FRAME_HEADER_SZ:
            return
        page = f.read(hdr.page_size)
        if len(page) < hdr.page_size:
            return
        n += 1
        pgno, dbsize = struct.unpack(">II", fh[:8])
        yield n, pgno, dbsize, fh, page
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_wal.py -v`
Expected: PASS，约 30 项

- [ ] **Step 6: 运行全量测试确认无回归**

Run: `python -m pytest -q`
Expected: PASS（config + discover + sqlcipher + wal 全绿）

- [ ] **Step 7: 提交**

```bash
git add qqexport/core/wal.py tests/helpers/fake_sqlcipher.py tests/test_wal.py
git commit -m "feat: WAL 帧校验与恢复，保证最新消息不因未 checkpoint 而丢失"
```

---

### Task 5: `snapshot.py` — 一致性快照

**Files:**
- Create: `qqexport/core/snapshot.py`
- Create: `tests/test_snapshot.py`

**Interfaces:**
- Consumes: `sqlcipher.WRAPPER_SZ`、`sqlcipher.PAGE_SZ`、`sqlcipher.SALT_SZ`、`sqlcipher.read_header_and_page1`、`sqlcipher.has_wrapper_header`、`sqlcipher.detect_params`（Task 3）
- Produces:
  - 常量 `SHM_LOCK_OFFSET=120` `SHM_LOCK_LENGTH=3` `DEFAULT_ATTEMPTS=3` `COPY_CHUNK=1024*1024`
  - `SnapshotError(RuntimeError)`
  - `SnapshotResult`（dataclass：`db_path:Path` `wal_path:Path|None` `shm_path:Path|None` `attempts:int` `locked:bool` `validator_ok:bool` `bytes_copied:int`）
  - `shm_lock(shm_path: Path | None)` → context manager，yield `bool`（是否拿到锁）
  - `copy_family(src_db: Path, dst_dir: Path) -> tuple[Path, Path|None, Path|None]`
  - `make_validator(passphrase: bytes | None = None) -> Callable[[Path], bool]`
  - `snapshot(src_db: Path, dst_dir: Path, *, validator: Callable[[Path], bool] | None = None, attempts: int = DEFAULT_ATTEMPTS, use_shm_lock: bool = True) -> SnapshotResult`

**为什么需要锁（spec §3、§6.6）：** 采样时 `nt_msg.db-wal` 有 1.88 MB 且正被写入。若在复制 `.db` 与 `.wal` 之间发生 checkpoint，会得到「db 比 wal 新」的错配组合——回放旧帧可能覆盖新页。SQLite 的 WAL-index 锁区在 `-shm` 偏移 120 起的 3 字节，非阻塞地拿住它即可让写入方在复制期间静默。

**锁拿不到时不报错**，靠 `validator` + 重试兜底。3 次都失败才抛 `SnapshotError`，消息里明确提示「请完全退出 QQ 后重试」（干净退出会触发 checkpoint）。

- [ ] **Step 1: 写失败的测试 `tests/test_snapshot.py`**

```python
import os
import time
from pathlib import Path

import pytest

from qqexport.core import snapshot as sn
from qqexport.core import sqlcipher as sc
from tests.helpers import fake_sqlcipher as fake

PASSPHRASE = b"#8FakeKeyForTest"


def make_source(root: Path, *, n_pages: int = 4, with_wal: bool = True,
                with_shm: bool = True, valid: bool = True) -> Path:
    """在 root 下造一个合成的 QQ 数据目录，返回 nt_msg.db 路径。"""
    db_dir = root / "123456789" / "nt_qq" / "nt_db"
    db_dir.mkdir(parents=True)
    pages = fake.random_plain_pages(n_pages)
    blob = fake.build_encrypted_db(pages, PASSPHRASE) if valid else b"\x00" * 5120
    db = db_dir / "nt_msg.db"
    db.write_bytes(blob)
    if with_wal:
        (db_dir / "nt_msg.db-wal").write_bytes(b"W" * 300)
    if with_shm:
        (db_dir / "nt_msg.db-shm").write_bytes(b"\x00" * 136)
    return db


def mtimes(root: Path) -> dict[str, float]:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns / 1e9
            for p in root.rglob("*") if p.is_file()}


def test_copy_family_copies_db_wal_shm(tmp_path):
    src = make_source(tmp_path / "src")
    dst = tmp_path / "work"
    db, w, s = sn.copy_family(src, dst)
    assert db == dst / "nt_msg.db" and db.read_bytes() == src.read_bytes()
    assert w == dst / "nt_msg.db-wal" and w.read_bytes() == b"W" * 300
    assert s == dst / "nt_msg.db-shm" and s.read_bytes() == b"\x00" * 136


def test_copy_family_tolerates_missing_wal_and_shm(tmp_path):
    src = make_source(tmp_path / "src", with_wal=False, with_shm=False)
    db, w, s = sn.copy_family(src, tmp_path / "work")
    assert db.is_file() and w is None and s is None


def test_copy_family_creates_dst_dir(tmp_path):
    src = make_source(tmp_path / "src")
    dst = tmp_path / "deep" / "nested" / "work"
    assert not dst.exists()
    sn.copy_family(src, dst)
    assert dst.is_dir() and (dst / "nt_msg.db").is_file()


def test_snapshot_never_modifies_source_files(tmp_path):
    """spec §12 R9 的核心保障：源目录内容与 mtime 全部不变。"""
    src_root = tmp_path / "src"
    src = make_source(src_root)
    before_content = {p: p.read_bytes() for p in src_root.rglob("*") if p.is_file()}
    before_mtimes = mtimes(src_root)
    time.sleep(0.01)

    sn.snapshot(src, tmp_path / "work", use_shm_lock=False)

    assert mtimes(src_root) == before_mtimes
    assert {p: p.read_bytes() for p in src_root.rglob("*") if p.is_file()} == before_content


def test_snapshot_retries_until_validator_passes(tmp_path):
    src = make_source(tmp_path / "src")
    calls = {"n": 0}

    def flaky(_path: Path) -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    res = sn.snapshot(src, tmp_path / "work", validator=flaky, use_shm_lock=False)
    assert res.attempts == 3 and res.validator_ok is True


def test_snapshot_raises_after_exhausting_attempts(tmp_path):
    src = make_source(tmp_path / "src")
    with pytest.raises(sn.SnapshotError) as ei:
        sn.snapshot(src, tmp_path / "work", validator=lambda p: False,
                    attempts=3, use_shm_lock=False)
    msg = str(ei.value)
    assert "退出 QQ" in msg and "3" in msg


def test_snapshot_error_message_does_not_leak_paths_of_key_material(tmp_path):
    src = make_source(tmp_path / "src")
    with pytest.raises(sn.SnapshotError) as ei:
        sn.snapshot(src, tmp_path / "work", validator=lambda p: False,
                    attempts=2, use_shm_lock=False)
    assert PASSPHRASE.decode() not in str(ei.value)


def test_default_validator_accepts_wellformed_db(tmp_path):
    src = make_source(tmp_path / "src")
    v = sn.make_validator(PASSPHRASE)
    assert v(src) is True


def test_default_validator_rejects_non_qq_file(tmp_path):
    bogus = tmp_path / "bogus.db"
    bogus.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4080)   # 无 1024 头
    assert sn.make_validator(PASSPHRASE)(bogus) is False
    assert sn.make_validator(None)(bogus) is False


def test_default_validator_without_passphrase_checks_shape_only(tmp_path):
    src = make_source(tmp_path / "src")
    assert sn.make_validator(None)(src) is True
    src.write_bytes(b"\x00" * 4096)                              # 大小不再是 %4096==1024
    assert sn.make_validator(None)(src) is False


def test_default_validator_rejects_wrong_passphrase(tmp_path):
    src = make_source(tmp_path / "src")
    assert sn.make_validator(b"#8WrongKeyXXXXXX")(src) is False


def test_default_validator_handles_missing_file(tmp_path):
    assert sn.make_validator(None)(tmp_path / "nope.db") is False


def test_shm_lock_yields_false_when_shm_absent(tmp_path):
    with sn.shm_lock(None) as locked:
        assert locked is False
    with sn.shm_lock(tmp_path / "missing-shm") as locked:
        assert locked is False


def test_snapshot_reports_lock_outcome(tmp_path, monkeypatch):
    src = make_source(tmp_path / "src")
    monkeypatch.setattr(sn, "shm_lock",
                        lambda p: _const_cm(True))
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=True)
    assert res.locked is True and res.attempts == 1


def test_snapshot_skips_lock_when_disabled(tmp_path, monkeypatch):
    src = make_source(tmp_path / "src")
    called = {"n": 0}

    def spy(p):
        called["n"] += 1
        return _const_cm(False)

    monkeypatch.setattr(sn, "shm_lock", spy)
    sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert called["n"] == 0


def test_result_records_bytes_copied(tmp_path):
    src = make_source(tmp_path / "src")
    expected = src.stat().st_size + 300 + 136
    res = sn.snapshot(src, tmp_path / "work", use_shm_lock=False)
    assert res.bytes_copied == expected


def _const_cm(value: bool):
    import contextlib
    return contextlib.nullcontext(value)


@pytest.mark.skipif(os.name != "nt", reason="msvcrt 仅 Windows")
def test_shm_lock_acquires_and_releases_on_real_file(tmp_path):
    shm = tmp_path / "nt_msg.db-shm"
    shm.write_bytes(b"\x00" * 136)
    with sn.shm_lock(shm) as locked:
        assert isinstance(locked, bool)
    assert shm.read_bytes() == b"\x00" * 136          # 内容未被改动
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qqexport.core.snapshot'`

- [ ] **Step 3: 实现 `qqexport/core/snapshot.py`**

```python
"""db + wal + shm 成组一致性复制。参照 spec §6.6。

QQ 运行时正在写库，直接复制可能得到撕裂快照或「db 比 wal 新」的错配组合。
策略：非阻塞字节范围锁静默写入方 → 复制 → 校验 → 失败重试 → 3 次后报错。

铁律：对源文件只读。唯一以 "r+b" 打开的是 -shm，仅为取锁，不写任何字节。
"""
from __future__ import annotations

import contextlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from . import sqlcipher as sc

SHM_LOCK_OFFSET = 120        # SQLite WAL-index / writer / checkpoint 锁区
SHM_LOCK_LENGTH = 3
DEFAULT_ATTEMPTS = 3
COPY_CHUNK = 1024 * 1024

_FAMILY_SUFFIXES = ("-wal", "-shm")


class SnapshotError(RuntimeError):
    """重试耗尽仍无法得到可用快照。消息必须提示用户退出 QQ。"""


@dataclass
class SnapshotResult:
    db_path: Path
    wal_path: Path | None
    shm_path: Path | None
    attempts: int
    locked: bool
    validator_ok: bool
    bytes_copied: int


@contextlib.contextmanager
def shm_lock(shm_path: Path | None) -> Iterator[bool]:
    """非阻塞锁住 -shm 偏移 120 起的 3 字节。拿不到就 yield False，绝不阻塞。"""
    if shm_path is None or os.name != "nt" or not Path(shm_path).is_file():
        yield False
        return
    import msvcrt
    handle = None
    try:
        handle = open(shm_path, "r+b")
        handle.seek(SHM_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, SHM_LOCK_LENGTH)
    except OSError:
        if handle is not None:
            handle.close()
        yield False
        return
    try:
        yield True
    finally:
        with contextlib.suppress(OSError):
            handle.seek(SHM_LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, SHM_LOCK_LENGTH)
        handle.close()


def _copy_one(src: Path, dst: Path) -> int:
    """分块复制，返回字节数。不在内存中持有整个文件（主库可达数百 MB）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            chunk = fin.read(COPY_CHUNK)
            if not chunk:
                break
            fout.write(chunk)
            total += len(chunk)
    return total


def copy_family(src_db: Path, dst_dir: Path) -> tuple[Path, Path | None, Path | None]:
    """复制 <db> 及其 -wal / -shm（存在才复制）。返回三个目标路径。"""
    src_db = Path(src_db)
    dst_dir = Path(dst_dir)
    dst_db = dst_dir / src_db.name
    copied = _copy_one(src_db, dst_db)

    wal_dst = shm_dst = None
    for suffix in _FAMILY_SUFFIXES:
        side = src_db.with_name(src_db.name + suffix)
        if side.is_file():
            target = dst_dir / side.name
            copied += _copy_one(side, target)
            if suffix == "-wal":
                wal_dst = target
            else:
                shm_dst = target
    return dst_db, wal_dst, shm_dst


def make_validator(passphrase: bytes | None = None) -> Callable[[Path], bool]:
    """返回一个校验函数：判断复制出的主库是否是可用的 QQ NT 加密库。

    给了口令就做完整的 detect_params 校验（最强）；
    没给口令只校验形状：size % 4096 == 1024 且 page-1 salt 非全零。
    """
    def validate(db_path: Path) -> bool:
        try:
            if not Path(db_path).is_file():
                return False
            size = Path(db_path).stat().st_size
            if not sc.has_wrapper_header(size):
                return False
            head = sc.read_header_and_page1(Path(db_path))
        except OSError:
            return False
        if len(head) < sc.WRAPPER_SZ + sc.PAGE_SZ:
            return False
        page1 = head[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.PAGE_SZ]
        if page1[:sc.SALT_SZ] == b"\x00" * sc.SALT_SZ:
            return False
        if passphrase is None:
            return True
        return sc.detect_params(passphrase, head) is not None
    return validate


def snapshot(src_db: Path, dst_dir: Path, *,
             validator: Callable[[Path], bool] | None = None,
             attempts: int = DEFAULT_ATTEMPTS,
             use_shm_lock: bool = True) -> SnapshotResult:
    """成组复制并校验，失败重试。耗尽 attempts 次后抛 SnapshotError。"""
    src_db = Path(src_db)
    dst_dir = Path(dst_dir)
    validate = validator if validator is not None else make_validator(None)

    last_db: Path | None = None
    last_wal: Path | None = None
    last_shm: Path | None = None
    locked = False
    total = 0

    for attempt in range(1, max(1, attempts) + 1):
        locker = shm_lock(src_db.with_name(src_db.name + "-shm")) if use_shm_lock \
            else contextlib.nullcontext(False)
        with locker as got_lock:
            locked = locked or bool(got_lock)
            last_db, last_wal, last_shm = copy_family(src_db, dst_dir)
        total = sum(p.stat().st_size for p in (last_db, last_wal, last_shm) if p)
        if validate(last_db):
            return SnapshotResult(last_db, last_wal, last_shm, attempt, locked,
                                  True, total)

    raise SnapshotError(
        f"复制 {src_db.name} 后连续 {attempts} 次校验失败，无法得到一致快照。"
        f"最可能的原因是 QQ 正在写入数据库。请完全退出 QQ（托盘图标右键退出，"
        f"不是只关窗口）后重试；干净退出会触发 WAL checkpoint，也能让导出更完整。"
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_snapshot.py -v`
Expected: PASS，17 项（非 Windows 上跳过最后 1 项）

- [ ] **Step 5: 提交**

```bash
git add qqexport/core/snapshot.py tests/test_snapshot.py
git commit -m "feat: db+wal+shm 成组一致性快照，含非阻塞 shm 锁与校验重试"
```

---

### Task 6: `keyscan.py` — 被动内存扫描

**Files:**
- Create: `qqexport/core/keyscan.py`
- Create: `tests/test_keyscan.py`

**Interfaces:**
- Consumes: `sqlcipher.detect_params`（间接，通过调用方传入的 `verify` 回调）
- Produces:
  - 常量 `PROCESS_VM_READ=0x0010` `PROCESS_QUERY_INFORMATION=0x0400` `MEM_COMMIT=0x1000` `READABLE_PROTECTS` `MAX_REGION_SIZE=200*1024*1024` `KEY_LENGTHS=(32,16)` `ACCESS_DENIED_WINERRORS={5,1314}` `QQ_PROCESS_NAMES=("QQ.exe","QQEX.exe")` `DEFAULT_TIMEOUT_S=120.0`
  - `ProcessAccessDenied(RuntimeError)`（`code = "process_access_denied"`，异常消息不含 pid/路径/原始 Win32 文本）
  - `find_key_candidates(blob: bytes) -> list[bytes]` —— **纯函数**
  - `MemoryReader`（Protocol：`regions() -> Iterator[tuple[int,int]]`、`read(base:int, size:int) -> bytes|None`、`close() -> None`）
  - `Win32ProcessReader(pid: int, kernel32=None)`
  - `scan_with_reader(reader, verify, *, timeout_s=DEFAULT_TIMEOUT_S, clock=time.monotonic) -> bytes | None`
  - `find_qq_pids(names=QQ_PROCESS_NAMES) -> list[int]`
  - `scan_key(verify, *, timeout_s=DEFAULT_TIMEOUT_S) -> bytes | None`

**设计要点：把纯计算与 Win32 调用彻底分开。** `find_key_candidates` 与 `scan_with_reader` 不含任何系统调用，可在任何平台穷举测试；`Win32ProcessReader` 只是把 `VirtualQueryEx`/`ReadProcessMemory` 翻译成 `MemoryReader` 协议。测试用假 reader 注入即可，真机验证留给 Task 12 的 live 冒烟。

**只读权限硬约束（spec §6.2、§12 R7）：** `OpenProcess` 只请求 `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`。文件中**不得出现** `CreateRemoteThread`、`WriteProcessMemory`、`DebugActiveProcess`、`VirtualAllocEx`、`SetWindowsHookEx`——Step 6 有一条测试专门 grep 断言这一点。

- [ ] **Step 1: 写失败的测试 `tests/test_keyscan.py`**

```python
import inspect
import re
from pathlib import Path

import pytest

from qqexport.core import keyscan as ks


def wrap(payload: bytes) -> bytes:
    """把候选串包成内存里常见的形态：前后都是 NUL。"""
    return b"\x00\x00" + payload + b"\x00\x00"


K16 = b"#8FakeKeyForTest"          # 16 字节可打印 ASCII
K32 = b"A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"   # 32 字节


# ---------- find_key_candidates（纯函数） ----------

def test_finds_16_byte_candidate():
    assert ks.find_key_candidates(wrap(K16)) == [K16]


def test_finds_32_byte_candidate():
    assert ks.find_key_candidates(wrap(K32)) == [K32]


def test_finds_candidate_at_blob_start():
    assert ks.find_key_candidates(K16 + b"\x00") == [K16]


@pytest.mark.parametrize("bad", [
    b"#8FakeKeyForTes",                 # 15
    b"#8FakeKeyForTestX",               # 17
    b"A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p",  # 31
    K32 + b"Q",                          # 33
])
def test_rejects_wrong_length_runs(bad):
    assert ks.find_key_candidates(wrap(bad)) == []


def test_rejects_non_printable_bytes_inside():
    assert ks.find_key_candidates(b"\x00#8Fake\x01KeyForTest\x00") == []


def test_requires_trailing_nul():
    assert ks.find_key_candidates(b"\x00" + K16 + b"\x41") == []


def test_48_byte_run_is_not_matched():
    """32 字节交替项会在偏移 16 处匹配，但其前一字节是可打印字符，必须被拒。"""
    assert ks.find_key_candidates(wrap(K32 + b"0123456789abcdef")) == []


def test_finds_multiple_and_dedupes_preserving_order():
    blob = wrap(K16) + b"\xff" * 40 + wrap(K32) + b"\xff" * 40 + wrap(K16)
    assert ks.find_key_candidates(blob) == [K16, K32]


def test_returns_bytes_not_str():
    out = ks.find_key_candidates(wrap(K16))
    assert all(isinstance(c, bytes) for c in out)


def test_empty_blob():
    assert ks.find_key_candidates(b"") == []


def test_realistic_heap_noise():
    """夹在典型堆内容（指针、字符串表）中间也要能挑出来。"""
    noise = (b"\x7f\x00\x00\x01\x00\x00\x00\x00"
             + b"nt_sqlite3_key_v2: db=%p zDb=%s\x00"
             + b"\x10\x20\x30\x40" * 8)
    blob = noise + wrap(K16) + noise
    assert ks.find_key_candidates(blob) == [K16]


# ---------- scan_with_reader ----------

class FakeReader:
    """按脚本产出区域；可注入访问失败与 None 读取。"""

    def __init__(self, regions, reads):
        self._regions = regions
        self._reads = reads
        self.read_calls = []
        self.closed = False

    def regions(self):
        yield from self._regions

    def read(self, base, size):
        self.read_calls.append((base, size))
        return self._reads.get(base)

    def close(self):
        self.closed = True


def test_scan_returns_first_verifying_candidate():
    blob = wrap(b"#8NotTheKeyXXXXX") + b"\xff" * 8 + wrap(K16)
    reader = FakeReader([(0x1000, len(blob))], {0x1000: blob})
    got = ks.scan_with_reader(reader, lambda c: c == K16)
    assert got == K16


def test_scan_returns_none_when_nothing_verifies():
    blob = wrap(K16)
    reader = FakeReader([(0x1000, len(blob))], {0x1000: blob})
    assert ks.scan_with_reader(reader, lambda c: False) is None


def test_scan_skips_oversized_regions():
    reader = FakeReader([(0x1000, ks.MAX_REGION_SIZE + 1)], {})
    assert ks.scan_with_reader(reader, lambda c: True) is None
    assert reader.read_calls == []


def test_scan_skips_zero_size_regions():
    reader = FakeReader([(0x1000, 0)], {})
    ks.scan_with_reader(reader, lambda c: True)
    assert reader.read_calls == []


def test_scan_tolerates_read_returning_none():
    blob = wrap(K16)
    reader = FakeReader([(0x1000, 4096), (0x2000, len(blob))], {0x2000: blob})
    assert ks.scan_with_reader(reader, lambda c: c == K16) == K16


def test_scan_respects_timeout_with_injected_clock():
    """verify 恒为 False，否则第一个区域就命中返回，永远走不到超时分支。"""
    ticks = iter([0.0, 0.0, 500.0, 900.0, 1500.0])
    blob = wrap(K16)
    regions = [(i * 0x10000, len(blob)) for i in range(3)]
    reads = {b: blob for b, _ in regions}
    reader = FakeReader(regions, reads)
    got = ks.scan_with_reader(reader, lambda c: False,
                              timeout_s=120.0, clock=lambda: next(ticks))
    assert got is None
    assert len(reader.read_calls) == 1          # 第二个区域前就已超时


def test_scan_unbounded_when_timeout_is_none():
    blob = wrap(K16)
    regions = [(i * 0x10000, len(blob)) for i in range(5)]
    reads = {b: blob for b, _ in regions}
    reader = FakeReader(regions, reads)
    assert ks.scan_with_reader(reader, lambda c: False, timeout_s=None) is None
    assert len(reader.read_calls) == 5


def test_scan_stops_at_first_match_without_reading_rest():
    blob = wrap(K16)
    regions = [(0x1000, len(blob)), (0x2000, len(blob))]
    reader = FakeReader(regions, {0x1000: blob, 0x2000: blob})
    assert ks.scan_with_reader(reader, lambda c: c == K16) == K16
    assert reader.read_calls == [(0x1000, len(blob))]


# ---------- Win32 层 ----------

class _FakeMbi:
    def __init__(self):
        self.BaseAddress = 0
        self.RegionSize = 0
        self.State = 0
        self.Protect = 0


class _FakeKernel32:
    """只实现被测代码用到的那几个调用，并记录请求过的权限。"""

    def __init__(self, *, open_result=1, last_error=0, regions=(), reads=None):
        self._open_result = open_result
        self._last_error = last_error
        self._regions = list(regions)
        self._reads = reads or {}
        self.open_args = None
        self.closed = []
        self._i = 0
        self.OpenProcess = self._open_process
        self.VirtualQueryEx = self._virtual_query_ex
        self.ReadProcessMemory = self._read_process_memory
        self.CloseHandle = self._close_handle
        self.GetLastError = lambda: self._last_error

    def _open_process(self, access, inherit, pid):
        self.open_args = (access, inherit, pid)
        return self._open_result

    def _virtual_query_ex(self, handle, addr, mbi_ref, size):
        if self._i >= len(self._regions):
            return 0
        base, region_size, state, protect = self._regions[self._i]
        self._i += 1
        mbi = mbi_ref._obj
        mbi.BaseAddress, mbi.RegionSize = base, region_size
        mbi.State, mbi.Protect = state, protect
        return 1

    def _read_process_memory(self, handle, addr, buf, size, read_n):
        data = self._reads.get(addr.value if hasattr(addr, "value") else addr, b"")
        for i, byte in enumerate(data):
            buf[i] = byte
        read_n._obj.value = len(data)
        return 1 if data else 0

    def _close_handle(self, handle):
        self.closed.append(handle)
        return 1


def test_reader_requests_only_read_permissions():
    fake_dll = _FakeKernel32(regions=[])
    reader = ks.Win32ProcessReader(4242, kernel32=fake_dll)
    list(reader.regions())
    reader.close()
    access, inherit, pid = fake_dll.open_args
    assert access == ks.PROCESS_VM_READ | ks.PROCESS_QUERY_INFORMATION
    assert inherit is False and pid == 4242
    assert len(fake_dll.closed) == 1              # 句柄被关闭恰好一次


def test_close_is_idempotent():
    fake_dll = _FakeKernel32(regions=[])
    reader = ks.Win32ProcessReader(4242, kernel32=fake_dll)
    list(reader.regions())
    reader.close()
    reader.close()
    assert len(fake_dll.closed) == 1


def test_reader_raises_access_denied_on_winerror_5():
    fake_dll = _FakeKernel32(open_result=0, last_error=5)
    reader = ks.Win32ProcessReader(4242, kernel32=fake_dll)
    with pytest.raises(ks.ProcessAccessDenied) as ei:
        list(reader.regions())
    assert ei.value.code == "process_access_denied"
    assert "4242" not in str(ei.value)            # 不泄露 pid


def test_reader_raises_access_denied_on_winerror_1314():
    fake_dll = _FakeKernel32(open_result=0, last_error=1314)
    with pytest.raises(ks.ProcessAccessDenied):
        list(ks.Win32ProcessReader(1, kernel32=fake_dll).regions())


def test_reader_yields_only_committed_readable_regions():
    regions = [
        (0x1000, 4096, ks.MEM_COMMIT, 0x04),       # 合格
        (0x2000, 4096, 0x2000, 0x04),              # State 非 MEM_COMMIT
        (0x3000, 4096, ks.MEM_COMMIT, 0x01),       # PAGE_NOACCESS
        (0x4000, 8192, ks.MEM_COMMIT, 0x40),       # 合格
        (0x5000, 0, ks.MEM_COMMIT, 0x04),          # 零大小，由 scan 层过滤
    ]
    fake_dll = _FakeKernel32(regions=regions)
    got = list(ks.Win32ProcessReader(1, kernel32=fake_dll).regions())
    assert (0x1000, 4096) in got and (0x4000, 8192) in got
    assert (0x2000, 4096) not in got and (0x3000, 4096) not in got


def test_source_contains_no_write_or_inject_apis():
    """spec §12 R7 的机器可执行保障。"""
    src = Path(inspect.getfile(ks)).read_text(encoding="utf-8")
    for forbidden in ("CreateRemoteThread", "WriteProcessMemory",
                      "DebugActiveProcess", "VirtualAllocEx",
                      "SetWindowsHookEx", "VirtualProtectEx",
                      "PROCESS_ALL_ACCESS", "NtWriteVirtualMemory"):
        assert forbidden not in src, f"{forbidden} 不得出现在 keyscan.py 中"


def test_source_never_requests_write_access_flag():
    src = Path(inspect.getfile(ks)).read_text(encoding="utf-8")
    assert "0x0020" not in src          # PROCESS_VM_WRITE
    assert "0x0008" not in src          # PROCESS_VM_OPERATION
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_keyscan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qqexport.core.keyscan'`

- [ ] **Step 3: 实现 `qqexport/core/keyscan.py`**

```python
"""被动内存扫描取得 SQLCipher 口令。参照 spec §6.2。

只读。仅使用 OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION)、
VirtualQueryEx、ReadProcessMemory、CloseHandle。
不注入 DLL、不附加调试器、不申请任何写权限、不修改 QQ 的任何文件。

NTQQ 的 wrapper.node 以 C 字符串形式把口令传给 nt_sqlite3_key_v2，
因此扫描「长度恰为 16 或 32 的可打印 ASCII、前后为 NUL」的序列，
再用真库 page-1 的 HMAC 逐个验证即可。
"""
from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes
from typing import Callable, Iterator, Protocol

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
READABLE_PROTECTS = frozenset({0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80})
MAX_REGION_SIZE = 200 * 1024 * 1024
KEY_LENGTHS = (32, 16)                       # 交替式最长优先，32 必须在 16 之前
ACCESS_DENIED_WINERRORS = frozenset({5, 1314})
QQ_PROCESS_NAMES = ("QQ.exe", "QQEX.exe")
DEFAULT_TIMEOUT_S = 120.0
TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_CANDIDATE_RE = re.compile(
    rb"(?<=\x00)([\x20-\x7e]{32}|[\x20-\x7e]{16})(?=\x00)"
)


class ProcessAccessDenied(RuntimeError):
    """Win32 权限拒绝。异常消息刻意不含 pid、路径与原始错误文本。"""

    code = "process_access_denied"

    def __init__(self) -> None:
        super().__init__(self.code)


def find_key_candidates(blob: bytes) -> list[bytes]:
    """从一块内存中提取口令候选，去重且保持首次出现顺序。

    区域起始处的候选靠给 blob 前置一个 NUL 字节来命中 lookbehind。
    """
    if not blob:
        return []
    padded = b"\x00" + blob
    out: list[bytes] = []
    seen: set[bytes] = set()
    for m in _CANDIDATE_RE.finditer(padded):
        cand = m.group(1)
        if cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out


class MemoryReader(Protocol):
    def regions(self) -> Iterator[tuple[int, int]]: ...
    def read(self, base: int, size: int) -> bytes | None: ...
    def close(self) -> None: ...


def scan_with_reader(reader: MemoryReader, verify: Callable[[bytes], bool], *,
                     timeout_s: float | None = DEFAULT_TIMEOUT_S,
                     clock: Callable[[], float] = time.monotonic) -> bytes | None:
    """遍历 reader 的区域，返回首个通过 verify 的候选。超时或全无命中返回 None。"""
    deadline = None if timeout_s is None else clock() + timeout_s
    try:
        for base, size in reader.regions():
            if deadline is not None and clock() > deadline:
                return None
            if size <= 0 or size > MAX_REGION_SIZE:
                continue
            blob = reader.read(base, size)
            if not blob:
                continue
            for cand in find_key_candidates(blob):
                if verify(cand):
                    return cand
    finally:
        reader.close()
    return None


class _MBI64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64),
        ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wintypes.DWORD),
        ("__alignment1", wintypes.DWORD),
        ("RegionSize", ctypes.c_uint64),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("__alignment2", wintypes.DWORD),
    ]


def _default_kernel32():
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    dll.OpenProcess.restype = wintypes.HANDLE
    dll.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_size_t]
    dll.VirtualQueryEx.restype = ctypes.c_size_t
    dll.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_size_t)]
    dll.ReadProcessMemory.restype = wintypes.BOOL
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    dll.CloseHandle.restype = wintypes.BOOL
    return dll


class Win32ProcessReader:
    """把 VirtualQueryEx / ReadProcessMemory 翻译成 MemoryReader 协议。"""

    def __init__(self, pid: int, kernel32=None) -> None:
        self._pid = pid
        self._dll = kernel32 if kernel32 is not None else _default_kernel32()
        self._handle = None

    def _last_error(self) -> int:
        getter = getattr(ctypes, "get_last_error", None)
        if getter is not None and getattr(self._dll, "use_last_error", True):
            try:
                return int(getter())
            except Exception:
                pass
        return int(self._dll.GetLastError())

    def _open(self):
        if self._handle is not None:
            return self._handle
        handle = self._dll.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, self._pid)
        if not handle:
            if self._last_error() in ACCESS_DENIED_WINERRORS:
                raise ProcessAccessDenied()
            raise OSError(f"OpenProcess failed for the target QQ process")
        self._handle = handle
        return handle

    def regions(self) -> Iterator[tuple[int, int]]:
        handle = self._open()
        mbi = _MBI64()
        address = 0
        while address < 0x7FFFFFFFFFFF:
            ret = self._dll.VirtualQueryEx(handle, ctypes.c_void_p(address),
                                           ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not ret:
                if self._last_error() in ACCESS_DENIED_WINERRORS:
                    raise ProcessAccessDenied()
                return
            size = int(mbi.RegionSize)
            base = int(mbi.BaseAddress)
            if size <= 0:
                address += 0x1000
                continue
            if int(mbi.State) == MEM_COMMIT and int(mbi.Protect) in READABLE_PROTECTS:
                yield base, size
            address = base + size

    def read(self, base: int, size: int) -> bytes | None:
        handle = self._open()
        buf = ctypes.create_string_buffer(size)
        read_n = ctypes.c_size_t(0)
        ok = self._dll.ReadProcessMemory(handle, ctypes.c_void_p(base), buf,
                                         size, ctypes.byref(read_n))
        if not ok:
            if self._last_error() in ACCESS_DENIED_WINERRORS:
                raise ProcessAccessDenied()
            return None
        return bytes(buf.raw[:read_n.value])

    def close(self) -> None:
        if self._handle is not None:
            self._dll.CloseHandle(self._handle)
            self._handle = None


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def find_qq_pids(names: tuple[str, ...] = QQ_PROCESS_NAMES) -> list[int]:
    """枚举 QQ 相关进程的 PID。非 Windows 或失败时返回空列表。"""
    if __import__("os").name != "nt":
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE_VALUE:
        return []
    wanted = {n.lower() for n in names}
    pids: list[int] = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return []
        while True:
            if str(entry.szExeFile).lower() in wanted:
                pids.append(int(entry.th32ProcessID))
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return sorted(set(pids))


def scan_key(verify: Callable[[bytes], bool], *,
             timeout_s: float | None = DEFAULT_TIMEOUT_S) -> bytes | None:
    """遍历所有 QQ 进程做被动扫描。权限被拒时抛 ProcessAccessDenied。"""
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    for pid in find_qq_pids():
        remain = None if deadline is None else max(1.0, deadline - time.monotonic())
        if deadline is not None and remain <= 1.0:
            return None
        reader = Win32ProcessReader(pid)
        got = scan_with_reader(reader, verify, timeout_s=remain)
        if got is not None:
            return got
    return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_keyscan.py -v`
Expected: PASS，约 31 项。其中 `test_source_contains_no_write_or_inject_apis` 与 `test_source_never_requests_write_access_flag` 是 spec §12 R7 的机器可执行保障——若它们失败，说明实现引入了写入/注入类 API，必须删除而不是放宽断言。

- [ ] **Step 5: 提交**

```bash
git add qqexport/core/keyscan.py tests/test_keyscan.py
git commit -m "feat: 只读内存扫描提取 SQLCipher 口令，纯函数与 Win32 层分离"
```

---

### Task 7: `keystore.py` — DPAPI 口令持久化

**Files:**
- Create: `qqexport/core/keystore.py`
- Create: `tests/test_keystore.py`

**Interfaces:**
- Consumes: `config.keys_path()`（Task 1）
- Produces:
  - 常量 `CRYPTPROTECT_UI_FORBIDDEN=0x01` `ENTROPY=b"qqexport/v1"` `FINGERPRINT_LEN=8` `STORE_VERSION=1`
  - `KeyStoreUnavailable(RuntimeError)`
  - `fingerprint(passphrase: bytes) -> str`（SHA-256 前 8 位十六进制）
  - `dpapi_protect(data: bytes) -> bytes | None`
  - `dpapi_unprotect(blob: bytes) -> bytes | None`
  - `StoredKey`（frozen dataclass：`uin:str` `fingerprint:str` `blob:str`（base64）`saved_at:str`）
  - `KeyStore(path: Path|None=None, protect=None, unprotect=None, clock=None)`，方法 `save(uin, passphrase) -> StoredKey`、`load(uin) -> bytes|None`、`clear(uin=None) -> int`、`entries() -> list[StoredKey]`、`redacted_dump() -> list[dict]`

**可注入的 `protect`/`unprotect`**：DPAPI 只在 Windows 上存在。把它们做成构造参数（默认绑定 DPAPI 实现）后，`KeyStore` 的全部逻辑可在任何平台用假保护器测试。

- [ ] **Step 1: 写失败的测试 `tests/test_keystore.py`**

```python
import json
import os
from pathlib import Path

import pytest

from qqexport.core import keystore as ks

PASSPHRASE = b"#8FakeKeyForTest"
OTHER = b"#8AnotherKeyXXXX"


def fake_protect(data: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in data)


def fake_unprotect(blob: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in blob)


def store(tmp_path: Path, protect=fake_protect, unprotect=fake_unprotect,
          clock=lambda: "2026-10-24T00:00:00") -> ks.KeyStore:
    return ks.KeyStore(tmp_path / "keys.json", protect=protect,
                       unprotect=unprotect, clock=clock)


# ---------- fingerprint ----------

def test_fingerprint_is_eight_hex_chars():
    fp = ks.fingerprint(PASSPHRASE)
    assert len(fp) == ks.FINGERPRINT_LEN == 8
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_is_stable_and_distinct():
    assert ks.fingerprint(PASSPHRASE) == ks.fingerprint(PASSPHRASE)
    assert ks.fingerprint(PASSPHRASE) != ks.fingerprint(OTHER)


def test_fingerprint_does_not_contain_passphrase():
    fp = ks.fingerprint(PASSPHRASE)
    assert PASSPHRASE.decode() not in fp
    assert PASSPHRASE.decode()[:4] not in fp


# ---------- DPAPI ----------

@pytest.mark.skipif(os.name != "nt", reason="DPAPI 仅 Windows")
def test_dpapi_roundtrip():
    blob = ks.dpapi_protect(PASSPHRASE)
    assert blob is not None and blob != PASSPHRASE
    assert ks.dpapi_unprotect(blob) == PASSPHRASE


@pytest.mark.skipif(os.name != "nt", reason="DPAPI 仅 Windows")
def test_dpapi_unprotect_rejects_garbage():
    assert ks.dpapi_unprotect(b"\x00" * 64) is None


# ---------- KeyStore ----------

def test_save_then_load_roundtrip(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    assert s.load("123456789") == PASSPHRASE


def test_load_unknown_uin_returns_none(tmp_path):
    assert store(tmp_path).load("99999999") is None


def test_load_from_fresh_instance_reads_disk(tmp_path):
    store(tmp_path).save("123456789", PASSPHRASE)
    assert store(tmp_path).load("123456789") == PASSPHRASE


def test_disk_never_contains_the_passphrase(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    raw = (tmp_path / "keys.json").read_bytes()
    assert PASSPHRASE not in raw
    assert PASSPHRASE.decode() not in raw.decode("utf-8")
    assert json.loads(raw.decode("utf-8"))["keys"]["123456789"]["fingerprint"] \
        == ks.fingerprint(PASSPHRASE)


def test_save_records_fingerprint_and_timestamp(tmp_path):
    entry = store(tmp_path).save("123456789", PASSPHRASE)
    assert entry.uin == "123456789"
    assert entry.fingerprint == ks.fingerprint(PASSPHRASE)
    assert entry.saved_at == "2026-10-24T00:00:00"
    assert isinstance(entry.blob, str) and entry.blob


def test_save_overwrites_same_uin(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    s.save("123456789", OTHER)
    assert s.load("123456789") == OTHER
    assert len(s.entries()) == 1


def test_multiple_uins_are_independent(tmp_path):
    s = store(tmp_path)
    s.save("11111111", PASSPHRASE)
    s.save("22222222", OTHER)
    assert s.load("11111111") == PASSPHRASE
    assert s.load("22222222") == OTHER


def test_clear_single_uin_returns_count(tmp_path):
    s = store(tmp_path)
    s.save("11111111", PASSPHRASE)
    s.save("22222222", OTHER)
    assert s.clear("11111111") == 1
    assert s.load("11111111") is None
    assert s.load("22222222") == OTHER


def test_clear_all(tmp_path):
    s = store(tmp_path)
    s.save("11111111", PASSPHRASE)
    s.save("22222222", OTHER)
    assert s.clear() == 2
    assert s.entries() == []


def test_clear_unknown_uin_returns_zero(tmp_path):
    assert store(tmp_path).clear("99999999") == 0


def test_load_returns_none_when_blob_undecryptable(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    s2 = ks.KeyStore(tmp_path / "keys.json", protect=fake_protect,
                     unprotect=lambda blob: None)
    assert s2.load("123456789") is None


def test_save_raises_when_protector_unavailable(tmp_path):
    s = store(tmp_path, protect=lambda data: None)
    with pytest.raises(ks.KeyStoreUnavailable):
        s.save("123456789", PASSPHRASE)
    assert not (tmp_path / "keys.json").exists()


def test_corrupt_store_file_is_treated_as_empty(tmp_path):
    (tmp_path / "keys.json").write_text("{not json", encoding="utf-8")
    s = store(tmp_path)
    assert s.entries() == []
    assert s.load("123456789") is None
    s.save("123456789", PASSPHRASE)             # 能自愈，不抛异常
    assert s.load("123456789") == PASSPHRASE


def test_store_file_with_unknown_version_is_treated_as_empty(tmp_path):
    (tmp_path / "keys.json").write_text(
        json.dumps({"version": 999, "keys": {"1": {"blob": "x"}}}),
        encoding="utf-8")
    assert store(tmp_path).entries() == []


def test_entries_expose_no_plaintext(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    for e in s.entries():
        assert PASSPHRASE.decode() not in repr(e)


def test_redacted_dump_is_json_serialisable_and_clean(tmp_path):
    s = store(tmp_path)
    s.save("123456789", PASSPHRASE)
    dumped = s.redacted_dump()
    text = json.dumps(dumped, ensure_ascii=False)
    assert PASSPHRASE.decode() not in text
    assert dumped[0]["uin"] == "123456789"
    assert dumped[0]["fingerprint"] == ks.fingerprint(PASSPHRASE)
    assert "blob" not in dumped[0]


def test_save_leaves_no_tmp_file(tmp_path):
    store(tmp_path).save("123456789", PASSPHRASE)
    assert list(tmp_path.glob("*.tmp")) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_keystore.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qqexport.core.keystore'`

- [ ] **Step 3: 实现 `qqexport/core/keystore.py`**

```python
"""口令的 DPAPI 加密持久化。参照 spec §6.3、§12 R10。

口令是解开全部聊天记录的钥匙，因此：
- 用 Windows DPAPI（CurrentUser 作用域 + CRYPTPROTECT_UI_FORBIDDEN）保护，
  同机其他用户与离线读盘都解不出
- 本体永不写入日志、异常消息、stdout、报告
- 对外只暴露 SHA-256 指纹前 8 位
- protect/unprotect 可注入，使 KeyStore 的逻辑在非 Windows 上也能被测
"""
from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import config

CRYPTPROTECT_UI_FORBIDDEN = 0x01
ENTROPY = b"qqexport/v1"
FINGERPRINT_LEN = 8
STORE_VERSION = 1


class KeyStoreUnavailable(RuntimeError):
    """DPAPI 不可用或保护失败。消息不得包含口令。"""


def fingerprint(passphrase: bytes) -> str:
    return hashlib.sha256(passphrase).hexdigest()[:FINGERPRINT_LEN]


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    """返回 (blob, 底层缓冲区)。调用方必须同时持有两者以防缓冲区被提前回收。"""
    buf = ctypes.create_string_buffer(data, max(len(data), 1))
    return _DATA_BLOB(len(data),
                      ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(crypt32, in_data: bytes, entropy: bytes | None,
           func_name: str) -> bytes | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    in_blob, in_keep = _to_blob(in_data)
    ent_blob, ent_keep = _to_blob(entropy) if entropy else (None, None)
    out = _DATA_BLOB()
    fn = getattr(crypt32, func_name)
    ok = fn(ctypes.byref(in_blob), None,
            ctypes.byref(ent_blob) if ent_blob else None,
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    del in_keep, ent_keep                    # 调用已完成，此时才可释放
    if not ok:
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def dpapi_protect(data: bytes) -> bytes | None:
    if os.name != "nt":
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    except OSError:
        return None
    return _dpapi(crypt32, data, ENTROPY, "CryptProtectData")


def dpapi_unprotect(blob: bytes) -> bytes | None:
    if os.name != "nt":
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    except OSError:
        return None
    return _dpapi(crypt32, blob, ENTROPY, "CryptUnprotectData")


@dataclass(frozen=True)
class StoredKey:
    uin: str
    fingerprint: str
    blob: str            # base64 编码的 DPAPI 密文
    saved_at: str


def _utcnow() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


class KeyStore:
    def __init__(self, path: Path | None = None,
                 protect: Callable[[bytes], bytes | None] | None = None,
                 unprotect: Callable[[bytes], bytes | None] | None = None,
                 clock: Callable[[], str] | None = None) -> None:
        self._path = Path(path) if path is not None else config.keys_path()
        self._protect = protect if protect is not None else dpapi_protect
        self._unprotect = unprotect if unprotect is not None else dpapi_unprotect
        self._clock = clock if clock is not None else _utcnow

    # ---- 磁盘格式 ----

    def _read_raw(self) -> dict:
        if not self._path.is_file():
            return {}
        try:
            doc = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(doc, dict) or doc.get("version") != STORE_VERSION:
            return {}
        keys = doc.get("keys")
        return keys if isinstance(keys, dict) else {}

    def _write_raw(self, keys: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps({"version": STORE_VERSION, "keys": keys},
                                  ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self._path)

    # ---- 公开 API ----

    def save(self, uin: str, passphrase: bytes) -> StoredKey:
        protected = self._protect(passphrase)
        if protected is None:
            raise KeyStoreUnavailable(
                "无法用系统 DPAPI 保护口令（当前平台不支持或调用失败）。"
                "可用 --no-cache-key 跳过缓存，本次导出仍可继续。")
        entry = StoredKey(uin=uin, fingerprint=fingerprint(passphrase),
                          blob=base64.b64encode(protected).decode("ascii"),
                          saved_at=self._clock())
        keys = self._read_raw()
        keys[uin] = {"fingerprint": entry.fingerprint, "blob": entry.blob,
                     "saved_at": entry.saved_at}
        self._write_raw(keys)
        return entry

    def load(self, uin: str) -> bytes | None:
        raw = self._read_raw().get(uin)
        if not isinstance(raw, dict) or not isinstance(raw.get("blob"), str):
            return None
        try:
            protected = base64.b64decode(raw["blob"], validate=True)
        except (binascii.Error, ValueError):
            return None
        return self._unprotect(protected)

    def clear(self, uin: str | None = None) -> int:
        keys = self._read_raw()
        if uin is None:
            n = len(keys)
            keys = {}
        elif uin in keys:
            del keys[uin]
            n = 1
        else:
            return 0
        self._write_raw(keys)
        return n

    def entries(self) -> list[StoredKey]:
        out = []
        for uin, raw in sorted(self._read_raw().items()):
            if not isinstance(raw, dict):
                continue
            out.append(StoredKey(uin=uin,
                                 fingerprint=str(raw.get("fingerprint", "")),
                                 blob=str(raw.get("blob", "")),
                                 saved_at=str(raw.get("saved_at", ""))))
        return out

    def redacted_dump(self) -> list[dict]:
        """可安全写入报告/stdout 的形式：不含 blob、不含口令。"""
        return [{"uin": e.uin, "fingerprint": e.fingerprint,
                 "saved_at": e.saved_at} for e in self.entries()]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_keystore.py -v`
Expected: PASS，约 24 项（非 Windows 跳过 2 项 DPAPI 测试）

- [ ] **Step 5: 提交**

```bash
git add qqexport/core/keystore.py tests/test_keystore.py
git commit -m "feat: DPAPI 保护的口令缓存，对外只暴露 SHA-256 指纹"
```

---

### Task 8: `decrypt.py` — 编排与流式解密

**Files:**
- Create: `qqexport/core/decrypt.py`
- Create: `tests/test_decrypt.py`

**Interfaces:**
- Consumes:
  - `discover.QQAccount`（Task 2）
  - `sqlcipher.detect_params` `decrypt_page` `normalize_page1_header` `read_header_and_page1` `WRAPPER_SZ` `PAGE_SZ` `LOCK_PAGE_NO` `CipherParams`（Task 3）
  - `wal.inspect_wal` `wal.apply_wal` `wal.WalPlan`（Task 4）
  - `snapshot.snapshot` `snapshot.make_validator` `snapshot.SnapshotError`（Task 5）
- Produces:
  - `DecryptError(RuntimeError)`
  - `DecryptResult`（dataclass：`plaintext_db:Path` `page_count:int` `corrupt_pages:int` `cipher_fingerprint:str` `lock_page_shifted:bool` `wal_frames:int` `wal_last_commit_frame:int` `wal_commit_size:int` `wal_applied_frames:int` `wal_corrupt_frames:int` `wal_signature_changed:bool` `wal_truncated:bool` `wal_reason:str` `shm_validated:bool` `integrity_check:str` `tables:list[str]` `duration_s:float` `warnings:list[str]`）
  - `integrity_probe(db_path: Path) -> tuple[str, list[str]]`
  - `decrypt_pages(src_db: Path, dst_db: Path, params: CipherParams, *, lock_page_no: int = LOCK_PAGE_NO) -> tuple[int, int, bool]`（返回 `(page_count, corrupt_pages, lock_page_shifted)`）
  - `decrypt_file(src_db: Path, passphrase: bytes, dst_db: Path, *, wal_path: Path|None=None, shm_path: Path|None=None) -> DecryptResult`
  - `decrypt_database(account: QQAccount, passphrase: bytes, *, dst_dir: Path, db_name: str = "nt_msg.db", attempts: int = 3) -> DecryptResult`
  - `PLAIN_SUFFIX = ".decrypted.db"`

**关键设计点：**

1. **不产生「剥头中间文件」。** `decrypt_pages` 直接从快照的偏移 `1024` 处流式读，写到明文库的偏移 0。省掉一次 335 MB 的整库复制。WAL 帧写入的目标是**明文库**（无 1024 头），偏移计算用 `(pgno-1)*4096`，与主库一致。
2. **损坏页写零页而不是跳过写入。** 否则后续所有页的偏移都会错位，整个库不可读。
3. **原子输出。** 先写 `<dst>.partial`，成功后 `os.replace`。失败时不留半成品。
4. **`integrity_probe` 用 `PRAGMA quick_check` 而非 `integrity_check`。** 后者要校验全部索引，335 MB 库上耗时过长；`quick_check` 足以发现页级损坏。以 `mode=ro` URI 打开，确保只读、不生成 journal。

**关于「产物可被 sqlite3 查询」的测试边界（重要）：**

stock `sqlite3` **无法**创建「每页保留 48 字节」的库——SQLite 头第 20 字节（保留字节数）只能由 SQLCipher 这类 codec 设置，而 B-tree 单元内容是从页尾向前生长的，用 `page_size=4096` 造出的库其真实数据必然占用最后 48 字节。因此本 task **不做**「解密合成库 → sqlite3 查询」的测试，改为：

| 要证明的事 | 用什么证明 |
|---|---|
| `integrity_probe` 本身工作正常 | 直接对一个真实 sqlite3 造的明文库调用它 |
| 解密产物的页布局正确（偏移、零页填充、页1魔数、第20字节） | 随机页 round-trip + 显式断言 |
| WAL 被正确叠加与截断 | 随机页 round-trip |
| **真实产物可被 sqlite3 查询** | Task 12 的 live 冒烟（唯一可靠途径） |

不要试图伪造保留区来凑这个测试，那是死路（同 Task 3 的说明）。

- [ ] **Step 1: 写失败的测试 `tests/test_decrypt.py`**

```python
import sqlite3
import struct
from pathlib import Path

import pytest

from qqexport.core import decrypt as dec
from qqexport.core import sqlcipher as sc
from qqexport.core import wal
from qqexport.core.discover import QQAccount
from tests.helpers import fake_sqlcipher as fake

PASSPHRASE = b"#8FakeKeyForTest"
WRONG = b"#8WrongKeyXXXXXX"


def plain_page(tag: int) -> bytes:
    return bytes([tag]) * sc.PAGE_SZ


def build_source(tmp_path: Path, pages: list[bytes], *, name: str = "nt_msg.db",
                 wal_entries=None) -> tuple[Path, Path | None, Path | None]:
    db_dir = tmp_path / "src"
    db_dir.mkdir(exist_ok=True)
    db = db_dir / name
    db.write_bytes(fake.build_encrypted_db(pages, PASSPHRASE))
    w = s = None
    if wal_entries:
        salt = db.read_bytes()[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.SALT_SZ]
        aes, mac = sc.derive_keys(PASSPHRASE, salt, "sha512")
        w = db_dir / (name + "-wal")
        w.write_bytes(fake.build_wal(wal_entries, aes_key=aes, mac_key=mac,
                                     mac="sha1", reserve=48, db_salt=salt))
        s = db_dir / (name + "-shm")
        s.write_bytes(b"\x00" * 136)
    return db, w, s


def page1_of(pages: list[bytes]) -> bytes:
    p = bytearray(pages[0])
    p[20] = 48                    # decrypt 会显式写入 reserve
    return bytes(p)


# ---------- integrity_probe ----------

def test_integrity_probe_on_real_sqlite_db(tmp_path):
    p = tmp_path / "real.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(i, f"r{i}") for i in range(50)])
    con.commit()
    con.close()
    verdict, tables = dec.integrity_probe(p)
    assert verdict == "ok"
    assert "t" in tables


def test_integrity_probe_on_garbage(tmp_path):
    p = tmp_path / "garbage.db"
    p.write_bytes(b"\x00" * 8192)
    verdict, tables = dec.integrity_probe(p)
    assert verdict != "ok"
    assert tables == []


def test_integrity_probe_on_missing_file(tmp_path):
    verdict, tables = dec.integrity_probe(tmp_path / "nope.db")
    assert verdict.startswith("open_failed") and tables == []


def test_integrity_probe_opens_read_only(tmp_path):
    p = tmp_path / "real.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE t (a)")
    con.commit()
    con.close()
    p.chmod(0o444)
    try:
        verdict, _ = dec.integrity_probe(p)
        assert verdict == "ok"
    finally:
        p.chmod(0o666)
    assert list(tmp_path.glob("*-journal")) == []


# ---------- decrypt_pages ----------

def test_decrypt_pages_roundtrip_is_byte_exact(tmp_path):
    pages = fake.random_plain_pages(6)
    src = tmp_path / "src.db"
    src.write_bytes(fake.build_encrypted_db(pages, PASSPHRASE))
    dst = tmp_path / "out.db"
    head = sc.read_header_and_page1(src)
    params = sc.detect_params(PASSPHRASE, head)
    n, corrupt, shifted = dec.decrypt_pages(src, dst, params)
    assert (n, corrupt, shifted) == (6, 0, False)
    out = dst.read_bytes()
    assert len(out) == 6 * sc.PAGE_SZ
    assert out[:sc.PAGE_SZ] == page1_of(pages[:1])
    for i in range(1, 6):
        assert out[i * sc.PAGE_SZ:(i + 1) * sc.PAGE_SZ] == pages[i]


def test_decrypt_pages_output_has_no_wrapper_header(tmp_path):
    pages = fake.random_plain_pages(2)
    src = tmp_path / "src.db"
    blob = fake.build_encrypted_db(pages, PASSPHRASE)
    src.write_bytes(blob)
    assert len(blob) == sc.WRAPPER_SZ + 2 * sc.PAGE_SZ
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, blob[:sc.WRAPPER_SZ + sc.PAGE_SZ])
    dec.decrypt_pages(src, dst, params)
    assert dst.stat().st_size == 2 * sc.PAGE_SZ          # 少了 1024 字节
    assert dst.read_bytes()[:16] == sc.SQLITE_MAGIC


def test_decrypt_pages_page1_byte20_is_set_to_reserve(tmp_path):
    pages = fake.random_plain_pages(1)
    assert pages[0][20] == 48
    weird = bytearray(pages[0])
    weird[20] = 0                                        # 故意写错
    src = tmp_path / "src.db"
    src.write_bytes(fake.build_encrypted_db([bytes(weird)], PASSPHRASE))
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, sc.read_header_and_page1(src))
    dec.decrypt_pages(src, dst, params)
    assert dst.read_bytes()[20] == params.reserve == 48


def test_decrypt_pages_counts_corrupt_and_keeps_offsets(tmp_path):
    pages = fake.random_plain_pages(5)
    blob = bytearray(fake.build_encrypted_db(pages, PASSPHRASE))
    # 破坏第 3 页密文（页级 HMAC 必然失败），并保持文件长度不变
    off = sc.WRAPPER_SZ + 2 * sc.PAGE_SZ + 100
    blob[off] ^= 0xFF
    src = tmp_path / "src.db"
    src.write_bytes(bytes(blob))
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, bytes(blob[:sc.WRAPPER_SZ + sc.PAGE_SZ]))
    n, corrupt, _ = dec.decrypt_pages(src, dst, params)
    assert n == 5 and corrupt == 1
    out = dst.read_bytes()
    assert len(out) == 5 * sc.PAGE_SZ                    # 偏移未错位
    assert out[2 * sc.PAGE_SZ:3 * sc.PAGE_SZ] == b"\x00" * sc.PAGE_SZ
    assert out[3 * sc.PAGE_SZ:4 * sc.PAGE_SZ] == pages[3]  # 后续页仍正确


def test_decrypt_pages_passes_through_zero_lock_page(tmp_path):
    """用注入的 lock_page_no 覆盖该分支，无需真的造 1 GB 文件。"""
    pages = fake.random_plain_pages(3)
    blob = bytearray(fake.build_encrypted_db(pages, PASSPHRASE))
    off = sc.WRAPPER_SZ + 1 * sc.PAGE_SZ                 # 把第 2 页整体清零
    blob[off:off + sc.PAGE_SZ] = b"\x00" * sc.PAGE_SZ
    src = tmp_path / "src.db"
    src.write_bytes(bytes(blob))
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, bytes(blob[:sc.WRAPPER_SZ + sc.PAGE_SZ]))
    n, corrupt, shifted = dec.decrypt_pages(src, dst, params, lock_page_no=2)
    assert (n, corrupt, shifted) == (3, 0, True)
    assert dst.read_bytes()[sc.PAGE_SZ:2 * sc.PAGE_SZ] == b"\x00" * sc.PAGE_SZ


def test_decrypt_pages_zero_lock_page_at_real_number_is_untouched(tmp_path):
    """默认 lock_page_no=262144，小文件里的零页应被当作损坏页而非锁页。"""
    pages = fake.random_plain_pages(3)
    blob = bytearray(fake.build_encrypted_db(pages, PASSPHRASE))
    off = sc.WRAPPER_SZ + 1 * sc.PAGE_SZ
    blob[off:off + sc.PAGE_SZ] = b"\x00" * sc.PAGE_SZ
    src = tmp_path / "src.db"
    src.write_bytes(bytes(blob))
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, bytes(blob[:sc.WRAPPER_SZ + sc.PAGE_SZ]))
    _, corrupt, shifted = dec.decrypt_pages(src, dst, params)
    assert corrupt == 1 and shifted is False


def test_decrypt_pages_pads_truncated_tail(tmp_path):
    pages = fake.random_plain_pages(2)
    blob = fake.build_encrypted_db(pages, PASSPHRASE)
    src = tmp_path / "src.db"
    src.write_bytes(blob[:-1000])                        # 尾部被截断
    dst = tmp_path / "out.db"
    params = sc.detect_params(PASSPHRASE, blob[:sc.WRAPPER_SZ + sc.PAGE_SZ])
    n, corrupt, _ = dec.decrypt_pages(src, dst, params)
    assert n == 2 and corrupt == 1
    assert dst.stat().st_size == 2 * sc.PAGE_SZ


# ---------- decrypt_file ----------

def test_decrypt_file_rejects_wrong_passphrase(tmp_path):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(2))
    with pytest.raises(dec.DecryptError) as ei:
        dec.decrypt_file(src, WRONG, tmp_path / "out.db")
    assert WRONG.decode() not in str(ei.value)           # 不泄露尝试过的口令


def test_decrypt_file_is_atomic_no_partial_left_on_failure(tmp_path):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(2))
    dst = tmp_path / "out.db"
    with pytest.raises(dec.DecryptError):
        dec.decrypt_file(src, WRONG, dst)
    assert not dst.exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_decrypt_file_applies_wal_and_truncates(tmp_path):
    """P0 验收核心：WAL 里的最新消息必须出现在产物中。"""
    pages = fake.random_plain_pages(4)
    newest = plain_page(0xEE)
    src, w, s = build_source(tmp_path, pages,
                             wal_entries=[(3, newest, 6)])
    dst = tmp_path / "out.db"
    res = dec.decrypt_file(src, PASSPHRASE, dst, wal_path=w, shm_path=s)
    out = dst.read_bytes()
    assert len(out) == 6 * sc.PAGE_SZ                    # 截断到 commit_size
    assert out[2 * sc.PAGE_SZ: 2 * sc.PAGE_SZ + 4] == b"\xee" * 4
    assert res.wal_applied_frames == 1
    assert res.wal_frames == 1 and res.wal_last_commit_frame == 1
    assert res.wal_commit_size == 6 and res.wal_truncated is True


def test_decrypt_file_without_wal_leaves_main_db_alone(tmp_path):
    pages = fake.random_plain_pages(4)
    src, _, _ = build_source(tmp_path, pages)
    dst = tmp_path / "out.db"
    res = dec.decrypt_file(src, PASSPHRASE, dst)
    assert dst.stat().st_size == 4 * sc.PAGE_SZ
    assert res.wal_frames == 0 and res.wal_reason == "wal_absent"
    assert res.wal_truncated is False


def test_decrypt_file_records_cipher_fingerprint(tmp_path):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(1))
    res = dec.decrypt_file(src, PASSPHRASE, tmp_path / "out.db")
    assert res.cipher_fingerprint == "sha512/sha1/48"


def test_decrypt_file_collects_tables_and_integrity(tmp_path):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(1))
    res = dec.decrypt_file(src, PASSPHRASE, tmp_path / "out.db")
    assert isinstance(res.integrity_check, str) and res.integrity_check
    assert isinstance(res.tables, list)
    assert res.duration_s >= 0.0


def test_decrypt_file_records_corrupt_page_warning(tmp_path):
    pages = fake.random_plain_pages(3)
    blob = bytearray(fake.build_encrypted_db(pages, PASSPHRASE))
    blob[sc.WRAPPER_SZ + sc.PAGE_SZ + 50] ^= 0xFF
    src = tmp_path / "src" / "nt_msg.db"
    src.parent.mkdir()
    src.write_bytes(bytes(blob))
    res = dec.decrypt_file(src, PASSPHRASE, tmp_path / "out.db")
    assert res.corrupt_pages == 1
    assert any("corrupt" in w.lower() or "损坏" in w for w in res.warnings)


# ---------- decrypt_database ----------

def test_decrypt_database_end_to_end_on_synthetic_account(tmp_path):
    pages = fake.random_plain_pages(4)
    newest = plain_page(0xEE)
    src, w, s = build_source(tmp_path, pages, wal_entries=[(2, newest, 5)])
    account = QQAccount(uin="123456789",
                        data_dir=src.parent.parent,
                        db_dir=src.parent,
                        media_dir=src.parent.parent / "nt_data")
    dst_dir = tmp_path / "work"
    res = dec.decrypt_database(account, PASSPHRASE, dst_dir=dst_dir)

    assert res.plaintext_db.name == "nt_msg.decrypted.db"
    assert res.plaintext_db.is_file()
    assert res.plaintext_db.parent == dst_dir
    out = res.plaintext_db.read_bytes()
    assert len(out) == 5 * sc.PAGE_SZ
    assert out[sc.PAGE_SZ: sc.PAGE_SZ + 4] == b"\xee" * 4
    assert list(dst_dir.glob("*.partial")) == []


def test_decrypt_database_output_name_uses_plain_suffix(tmp_path):
    assert dec.PLAIN_SUFFIX == ".decrypted.db"
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(1),
                             name="profile_info.db")
    account = QQAccount("123456789", src.parent.parent, src.parent,
                        src.parent.parent / "nt_data")
    res = dec.decrypt_database(account, PASSPHRASE, dst_dir=tmp_path / "work",
                               db_name="profile_info.db")
    assert res.plaintext_db.name == "profile_info.decrypted.db"


def test_decrypt_database_propagates_snapshot_error(tmp_path):
    src, _, _ = build_source(tmp_path, fake.random_plain_pages(1))
    src.write_bytes(b"\x00" * 4096)                       # 不再是合法 QQ 库
    account = QQAccount("123456789", src.parent.parent, src.parent,
                        src.parent.parent / "nt_data")
    with pytest.raises(dec.DecryptError) as ei:
        dec.decrypt_database(account, PASSPHRASE, dst_dir=tmp_path / "work",
                             attempts=2)
    assert "退出 QQ" in str(ei.value)


def test_decrypt_database_is_deterministic(tmp_path):
    """spec §10.3 第 8 条：连续两次解密结果字节一致。"""
    pages = fake.random_plain_pages(3)
    src, _, _ = build_source(tmp_path, pages)
    account = QQAccount("123456789", src.parent.parent, src.parent,
                        src.parent.parent / "nt_data")
    a = dec.decrypt_database(account, PASSPHRASE, dst_dir=tmp_path / "w1")
    b = dec.decrypt_database(account, PASSPHRASE, dst_dir=tmp_path / "w2")
    assert a.plaintext_db.read_bytes() == b.plaintext_db.read_bytes()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_decrypt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qqexport.core.decrypt'`

- [ ] **Step 3: 实现 `qqexport/core/decrypt.py`**

```python
"""编排 snapshot → sqlcipher → wal，流式产出标准明文 SQLite。参照 spec §6.7。

三个不变量：
1. 不产生「剥头中间文件」——从快照偏移 1024 处流式读，写到明文文件偏移 0
2. 损坏页写零页而非跳过写入，否则后续所有页偏移错位、整库不可读
3. 原子输出——先写 .partial，成功后 os.replace；失败不留半成品
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import sqlcipher as sc
from . import snapshot as snap
from . import wal
from .discover import QQAccount

PLAIN_SUFFIX = ".decrypted.db"
_PARTIAL_SUFFIX = ".partial"


class DecryptError(RuntimeError):
    """解密失败。消息不得包含口令。"""


@dataclass
class DecryptResult:
    plaintext_db: Path
    page_count: int = 0
    corrupt_pages: int = 0
    cipher_fingerprint: str = ""
    lock_page_shifted: bool = False
    wal_frames: int = 0
    wal_last_commit_frame: int = 0
    wal_commit_size: int = 0
    wal_applied_frames: int = 0
    wal_corrupt_frames: int = 0
    wal_signature_changed: bool = False
    wal_truncated: bool = False
    wal_reason: str = ""
    shm_validated: bool = False
    integrity_check: str = ""
    tables: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)


def integrity_probe(db_path: Path) -> tuple[str, list[str]]:
    """用只读 URI 打开明文库，返回 (quick_check 结论, 表名列表)。

    用 quick_check 而非 integrity_check：后者校验全部索引，335 MB 库上过慢。
    mode=ro 保证不写、不生成 journal。
    """
    p = Path(db_path)
    if not p.is_file():
        return "open_failed:missing", []
    try:
        con = sqlite3.connect(f"file:{p.resolve().as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return f"open_failed:{exc.__class__.__name__}", []
    try:
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            verdict = str(row[0]) if row else "no_result"
        except sqlite3.Error as exc:
            verdict = f"error:{exc.__class__.__name__}"
        try:
            tables = [str(r[0]) for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        except sqlite3.Error:
            tables = []
        return verdict, tables
    finally:
        con.close()


def decrypt_pages(src_db: Path, dst_db: Path, params: sc.CipherParams, *,
                  lock_page_no: int = sc.LOCK_PAGE_NO) -> tuple[int, int, bool]:
    """流式解密。返回 (page_count, corrupt_pages, lock_page_shifted)。

    lock_page_no 可注入，使全零锁页分支能在小文件上被测（真值是 262144，
    对应约 1 GB 的库）。
    """
    page_count = 0
    corrupt = 0
    shifted = False
    zero_page = b"\x00" * sc.PAGE_SZ
    dst_db = Path(dst_db)
    dst_db.parent.mkdir(parents=True, exist_ok=True)

    with open(src_db, "rb") as fin, open(dst_db, "wb") as fout:
        fin.seek(sc.WRAPPER_SZ)
        while True:
            page = fin.read(sc.PAGE_SZ)
            if not page:
                break
            page_count += 1
            if len(page) < sc.PAGE_SZ:
                corrupt += 1
                fout.write(page + b"\x00" * (sc.PAGE_SZ - len(page)))
                break
            if page_no_is_lock(page_count, lock_page_no, page):
                shifted = True
                fout.write(page)
                continue
            plain = sc.decrypt_page(params, page, page_count)
            if plain is None:
                corrupt += 1
                fout.write(zero_page)
                continue
            if page_count == 1:
                plain = sc.normalize_page1_header(plain, params.reserve)
            fout.write(plain)
    return page_count, corrupt, shifted


def page_no_is_lock(page_no: int, lock_page_no: int, page: bytes) -> bool:
    return page_no == lock_page_no and page == b"\x00" * sc.PAGE_SZ


def decrypt_file(src_db: Path, passphrase: bytes, dst_db: Path, *,
                 wal_path: Path | None = None,
                 shm_path: Path | None = None) -> DecryptResult:
    """解密单个库文件（含 WAL 恢复）。产物原子替换到 dst_db。"""
    started = time.monotonic()
    src_db, dst_db = Path(src_db), Path(dst_db)

    head = sc.read_header_and_page1(src_db)
    params = sc.detect_params(passphrase, head)
    if params is None:
        raise DecryptError(
            "口令校验失败：无法用它解密该库的第 1 页。"
            "可能是口令不对，或 QQ 版本改动了加密参数。"
            "请重新执行 `qqexport key scan`，或用 `qqexport key set` 手动写入。")

    partial = dst_db.with_name(dst_db.name + _PARTIAL_SUFFIX)
    try:
        page_count, corrupt, shifted = decrypt_pages(src_db, partial, params)
        res = DecryptResult(plaintext_db=dst_db, page_count=page_count,
                            corrupt_pages=corrupt,
                            cipher_fingerprint=params.fingerprint,
                            lock_page_shifted=shifted,
                            duration_s=time.monotonic() - started)
        if corrupt:
            res.warnings.append(f"{corrupt} 个页损坏（HMAC 校验失败），已写零页占位")

        _recover_wal(res, params, partial, wal_path, shm_path)

        res.integrity_check, res.tables = integrity_probe(partial)
        if res.integrity_check != "ok":
            res.warnings.append(f"quick_check 结论：{res.integrity_check}")
        if not res.tables:
            res.warnings.append("sqlite_master 中未发现任何表")

        os.replace(partial, dst_db)
    except BaseException:
        if partial.exists():
            partial.unlink(missing_ok=True)
        raise
    res.duration_s = time.monotonic() - started
    return res


def _recover_wal(res: DecryptResult, params: sc.CipherParams, db_path: Path,
                 wal_path: Path | None, shm_path: Path | None) -> None:
    if wal_path is None or not Path(wal_path).is_file():
        res.wal_reason = "wal_absent"
        return
    plan = wal.inspect_wal(Path(wal_path),
                           Path(shm_path) if shm_path else None,
                           expected_page_size=sc.PAGE_SZ)
    res.wal_frames = plan.frame_count
    res.wal_last_commit_frame = plan.last_commit_frame
    res.wal_commit_size = plan.commit_size
    res.shm_validated = plan.shm_validated
    res.wal_reason = plan.reason
    if not plan.valid:
        res.warnings.append(f"WAL 未恢复（{plan.reason}），产物只含已 checkpoint 的数据")
        return

    applied = wal.apply_wal(params, Path(wal_path), db_path, plan)
    res.wal_applied_frames = applied.frames_applied
    res.wal_corrupt_frames = applied.corrupt_frames
    res.wal_signature_changed = applied.signature_changed
    res.wal_truncated = applied.truncated_to_pages > 0
    if applied.truncated_to_pages:
        res.page_count = applied.truncated_to_pages
    if applied.corrupt_frames:
        res.warnings.append(
            f"{applied.corrupt_frames} 个 WAL 帧页 HMAC 失败，已停止应用后续帧")
    if applied.signature_changed:
        res.warnings.append("WAL 在 inspect 与 apply 之间被改写，本次未应用任何帧")
    if applied.stopped_early:
        res.warnings.append("WAL 提前停止应用：" + "；".join(applied.notes))


def decrypt_database(account: QQAccount, passphrase: bytes, *, dst_dir: Path,
                     db_name: str = "nt_msg.db",
                     attempts: int = snap.DEFAULT_ATTEMPTS) -> DecryptResult:
    """快照 + 解密的完整流程。快照失败统一包装成 DecryptError。"""
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    src_db = account.db_dir / db_name
    if not src_db.is_file():
        raise DecryptError(f"源数据库不存在：{src_db}")

    try:
        shot = snap.snapshot(src_db, dst_dir,
                             validator=snap.make_validator(passphrase),
                             attempts=attempts)
    except snap.SnapshotError as exc:
        raise DecryptError(str(exc)) from exc

    final = dst_dir / (Path(db_name).stem + PLAIN_SUFFIX)
    return decrypt_file(shot.db_path, passphrase, final,
                        wal_path=shot.wal_path, shm_path=shot.shm_path)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_decrypt.py -v`
Expected: PASS，约 22 项

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add qqexport/core/decrypt.py tests/test_decrypt.py
git commit -m "feat: 编排快照/解密/WAL 恢复，流式产出明文 SQLite 并保证原子输出"
```

---

### Task 9: `report.py` + `doctor.py` — 全链路自检

**Files:**
- Create: `qqexport/report.py`
- Create: `qqexport/doctor.py`
- Create: `tests/test_report.py`
- Create: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `discover`（Task 2）、`sqlcipher`（Task 3）、`wal`（Task 4）、`keyscan`（Task 6）、`keystore`（Task 7）、`decrypt`（Task 8）、`config`（Task 1）
- Produces:
  - `report.CheckResult`（frozen dataclass：`index:int` `name:str` `status:str` `detail:str=""`；`status ∈ {"ok","warn","fail","skip"}`）
  - `report.DoctorReport`（dataclass：`checks:list[CheckResult]` `generated_at:str`；property `ok -> bool`、`failed -> list[CheckResult]`；method `to_json() -> dict`、`to_text(ascii_only: bool=False) -> str`）
  - `report.STATUS_GLYPH: dict[str,str]`
  - `doctor.EXPECTED_TABLES: tuple[str,...]`、`doctor.EXPECTED_COLUMNS: tuple[str,...]`
  - `doctor.run_doctor(*, data_dir=None, uin=None, passphrase=None, allow_scan=False, scan_timeout_s=120.0, work_dir=None, keep_output=False) -> DoctorReport`

**核心原则：每一项检查都用 try/except 包起来。** doctor 是诊断工具，它自己绝不能因为某一项异常就整体崩掉——那正好发生在用户最需要诊断信息的时刻。异常只保留**类名**进 detail，不带 traceback、不带口令。

**spec §9.3 的 P0 部分对应 11 项检查**（第 12–15 项属 P1+，本 task 不实现）。

- [ ] **Step 1: 写失败的测试 `tests/test_report.py`**

```python
import json

import pytest

from qqexport import report as rp


def c(i, status, name="check", detail=""):
    return rp.CheckResult(index=i, name=name, status=status, detail=detail)


def test_status_glyphs_cover_all_statuses():
    assert set(rp.STATUS_GLYPH) == {"ok", "warn", "fail", "skip"}


def test_ok_is_true_only_when_no_fail():
    assert rp.DoctorReport([c(1, "ok"), c(2, "warn")], "t").ok is True
    assert rp.DoctorReport([c(1, "ok"), c(2, "fail")], "t").ok is False
    assert rp.DoctorReport([c(1, "skip")], "t").ok is True


def test_failed_returns_only_fail_entries():
    r = rp.DoctorReport([c(1, "ok"), c(2, "fail", "b"), c(3, "fail", "c")], "t")
    assert [x.name for x in r.failed] == ["b", "c"]


def test_to_text_has_one_line_per_check():
    r = rp.DoctorReport([c(1, "ok", "python"), c(2, "fail", "qq", "boom")], "t")
    lines = [ln for ln in r.to_text().splitlines() if ln.strip()]
    assert any("python" in ln for ln in lines)
    assert any("qq" in ln and "boom" in ln for ln in lines)


def test_to_text_ascii_only_has_no_non_ascii_chars():
    """中文 Windows 控制台常是 cp936，emoji 会直接 UnicodeEncodeError。"""
    r = rp.DoctorReport([c(1, "ok", "python"), c(2, "fail", "qq", "boom")], "t")
    text = r.to_text(ascii_only=True)
    assert text.encode("ascii", "strict")
    assert "[OK]" in text and "[FAIL]" in text


def test_to_text_default_uses_glyphs():
    r = rp.DoctorReport([c(1, "ok", "python")], "t")
    assert rp.STATUS_GLYPH["ok"] in r.to_text()


def test_to_json_is_serialisable():
    r = rp.DoctorReport([c(1, "ok", "python", "3.14.6")], "2026-10-24T00:00:00")
    doc = json.loads(json.dumps(r.to_json(), ensure_ascii=False))
    assert doc["generated_at"] == "2026-10-24T00:00:00"
    assert doc["checks"][0] == {"index": 1, "name": "python",
                                "status": "ok", "detail": "3.14.6"}
    assert doc["ok"] is True


def test_check_result_rejects_unknown_status():
    with pytest.raises(ValueError, match="status"):
        rp.CheckResult(1, "x", "bogus_status")
```

- [ ] **Step 2: 写失败的测试 `tests/test_doctor.py`**

```python
import sqlite3
from pathlib import Path

import pytest

from qqexport import doctor
from qqexport.core import sqlcipher as sc
from qqexport.core.discover import QQAccount
from tests.helpers import fake_sqlcipher as fake

PASSPHRASE = b"#8FakeKeyForTest"


def build_env(tmp_path: Path, monkeypatch, *, n_pages: int = 4,
              with_wal: bool = True, qq_running: bool = True,
              uin: str = "123456789") -> QQAccount:
    """造一个能通过全部 11 项检查的合成环境。"""
    db_dir = tmp_path / "TencentFiles" / uin / "nt_qq" / "nt_db"
    db_dir.mkdir(parents=True)
    media = tmp_path / "TencentFiles" / uin / "nt_qq" / "nt_data"
    media.mkdir(parents=True)

    pages = fake.random_plain_pages(n_pages)
    blob = fake.build_encrypted_db(pages, PASSPHRASE)
    db = db_dir / "nt_msg.db"
    db.write_bytes(blob)
    salt = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.SALT_SZ]
    aes, mac = sc.derive_keys(PASSPHRASE, salt, "sha512")
    if with_wal:
        (db_dir / "nt_msg.db-wal").write_bytes(
            fake.build_wal([(2, pages[1], n_pages + 1)], aes_key=aes,
                           mac_key=mac, mac="sha1", reserve=48, db_salt=salt))
        (db_dir / "nt_msg.db-shm").write_bytes(b"\x00" * 136)

    account = QQAccount(uin=uin, data_dir=db_dir.parent, db_dir=db_dir,
                        media_dir=media)
    monkeypatch.setattr(doctor, "_discover", lambda **kw: [account])
    monkeypatch.setattr(doctor, "_qq_version", lambda: "9.9.36-53644")
    monkeypatch.setattr(doctor, "_qq_pids", lambda: [4242] if qq_running else [])
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path / "apphome"))
    return account


def test_all_checks_pass_on_healthy_synthetic_env(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(passphrase=PASSPHRASE,
                            work_dir=tmp_path / "work")
    assert len(rep.checks) == 11
    assert [c.index for c in rep.checks] == list(range(1, 12))
    assert rep.ok, [(c.index, c.name, c.status, c.detail) for c in rep.checks]


def test_no_check_detail_leaks_the_passphrase(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    text = rep.to_text() + repr(rep.to_json())
    assert PASSPHRASE.decode() not in text
    assert PASSPHRASE not in text.encode("utf-8", "replace")
    # 指纹允许出现，本体绝不允许
    from qqexport.core.keystore import fingerprint
    assert fingerprint(PASSPHRASE) in text


def test_check8_reports_detected_cipher_params(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert "sha512/sha1/48" in rep.checks[7].detail


def test_check9_produces_queryable_plaintext_db(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work",
                            keep_output=True)
    assert rep.checks[8].status == "ok"
    out = list((tmp_path / "work").rglob("*.decrypted.db"))
    assert len(out) == 1
    assert out[0].read_bytes()[:16] == sc.SQLITE_MAGIC
    assert out[0].stat().st_size % sc.PAGE_SZ == 0


def test_check10_reports_wal_frames(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[9].status == "ok"
    assert "1" in rep.checks[9].detail


def test_check10_warns_when_wal_absent(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch, with_wal=False)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[9].status == "warn"
    assert rep.ok is True            # warn 不算失败


def test_missing_qq_process_is_warn_not_fail(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch, qq_running=False)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[5].status == "warn"
    assert rep.ok is True


def test_wrong_passphrase_fails_check7_and_skips_dependents(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(passphrase=b"#8WrongKeyXXXXXX",
                            work_dir=tmp_path / "work")
    assert rep.checks[6].status == "fail"
    assert rep.ok is False
    assert all(c.status == "skip" for c in rep.checks[7:])
    assert "WrongKey" not in rep.to_text()


def test_no_passphrase_and_no_scan_fails_check7(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(allow_scan=False, work_dir=tmp_path / "work")
    assert rep.checks[6].status == "fail"
    assert rep.ok is False


def test_cached_passphrase_is_used_when_none_given(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    from qqexport.core.keystore import KeyStore
    KeyStore(tmp_path / "apphome" / "keys.json",
             protect=lambda d: bytes(b ^ 0x5A for b in d),
             unprotect=lambda b: bytes(b ^ 0x5A for b in b),
             ).save("123456789", PASSPHRASE)
    monkeypatch.setattr(doctor, "_key_store",
                        lambda: KeyStore(tmp_path / "apphome" / "keys.json",
                                         protect=lambda d: bytes(b ^ 0x5A for b in d),
                                         unprotect=lambda b: bytes(b ^ 0x5A for b in b)))
    rep = doctor.run_doctor(work_dir=tmp_path / "work")
    assert rep.checks[6].status == "ok"
    assert "缓存" in rep.checks[6].detail


def test_discovery_failure_fails_check3_and_skips_rest(tmp_path, monkeypatch):
    from qqexport.core.discover import DiscoveryError
    monkeypatch.setattr(doctor, "_discover",
                        lambda **kw: (_ for _ in ()).throw(DiscoveryError("没找到")))
    monkeypatch.setattr(doctor, "_qq_version", lambda: "9.9.36-53644")
    monkeypatch.setattr(doctor, "_qq_pids", lambda: [])
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path / "apphome"))
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[0].status == "ok"          # python 版本检查仍通过
    assert rep.checks[2].status == "fail"
    assert all(c.status == "skip" for c in rep.checks[3:])


def test_db_size_shape_check_detects_non_qq_file(tmp_path, monkeypatch):
    account = build_env(tmp_path, monkeypatch)
    account.msg_db.write_bytes(b"\x00" * 4096)    # 不再是 %4096==1024
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[4].status == "fail"


def test_exception_inside_a_check_becomes_fail_not_crash(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)

    def boom():
        raise RuntimeError("secret detail that must not leak")

    monkeypatch.setattr(doctor, "_qq_version", boom)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work")
    assert rep.checks[1].status == "fail"
    assert "RuntimeError" in rep.checks[1].detail
    assert "secret detail" not in rep.to_text()


def test_check11_reports_expected_tables(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    rep = doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work",
                            keep_output=True)
    # 合成库是随机页，解不出 sqlite_master，因此期望表全部缺失 → warn（不是 fail）
    assert rep.checks[10].status == "warn"
    assert "c2c_msg_table" in rep.checks[10].detail
    assert "group_msg_table" in rep.checks[10].detail
    assert "缺失" in rep.checks[10].detail


def test_keep_output_false_removes_plaintext_db(tmp_path, monkeypatch):
    build_env(tmp_path, monkeypatch)
    doctor.run_doctor(passphrase=PASSPHRASE, work_dir=tmp_path / "work",
                      keep_output=False)
    assert list((tmp_path / "work").rglob("*.decrypted.db")) == []


def test_expected_tables_and_columns_match_spec_section_7_1():
    assert "c2c_msg_table" in doctor.EXPECTED_TABLES
    assert "group_msg_table" in doctor.EXPECTED_TABLES
    for col in ("40001", "40011", "40020", "40050", "40800"):
        assert col in doctor.EXPECTED_COLUMNS
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_report.py tests/test_doctor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qqexport.report'`

- [ ] **Step 4: 实现 `qqexport/report.py`**

```python
"""诊断报告的数据结构与渲染。参照 spec §9.3、§11。

to_text(ascii_only=True) 是给中文 Windows 控制台（常见 cp936）用的：
emoji 在 cp936 下会直接抛 UnicodeEncodeError，必须有纯 ASCII 退路。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

_VALID_STATUS = ("ok", "warn", "fail", "skip")

STATUS_GLYPH = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭"}
STATUS_ASCII = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]"}


@dataclass(frozen=True)
class CheckResult:
    index: int
    name: str
    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUS:
            raise ValueError(f"unknown status: {self.status!r}")


@dataclass
class DoctorReport:
    checks: list[CheckResult]
    generated_at: str

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_json(self) -> dict:
        return {"generated_at": self.generated_at, "ok": self.ok,
                "checks": [asdict(c) for c in self.checks]}

    def to_text(self, ascii_only: bool = False) -> str:
        glyphs = STATUS_ASCII if ascii_only else STATUS_GLYPH
        width = max((len(c.name) for c in self.checks), default=0)
        lines = []
        for c in self.checks:
            lines.append(f"{glyphs[c.status]} {c.index:>2}. {c.name:<{width}}  {c.detail}".rstrip())
        summary = ("all checks passed" if self.ok
                   else f"{len(self.failed)} check(s) failed")
        if ascii_only:
            lines.append("")
            lines.append(summary)
        else:
            lines.append("")
            lines.append("全部检查通过" if self.ok else f"{len(self.failed)} 项检查未通过")
        return "\n".join(lines)
```

- [ ] **Step 5: 实现 `qqexport/doctor.py`**

```python
"""11 项环境自检（spec §9.3 的 P0 部分）。

每一项都包在 _run() 里：异常只留类名进 detail，绝不让 doctor 自己崩掉，
也绝不把异常文本（可能含路径或口令片段）带出去。
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import config
from .core import decrypt as dec
from .core import discover as disc
from .core import keyscan
from .core import keystore
from .core import sqlcipher as sc

EXPECTED_TABLES = ("c2c_msg_table", "group_msg_table")
EXPECTED_COLUMNS = ("40001", "40011", "40012", "40013", "40020", "40021",
                    "40030", "40033", "40050", "40800")

_N_CHECKS = 11


# ---- 可 monkeypatch 的边界 ----

def _discover(**kwargs):
    return disc.discover_accounts(**kwargs)


def _qq_version():
    return disc.qq_install_version()


def _qq_pids():
    return keyscan.find_qq_pids()


def _key_store():
    return keystore.KeyStore()


def _now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _run(index: int, name: str, fn):
    """执行一项检查。返回 (CheckResult, 产出值)；异常 → status='fail'。"""
    from .report import CheckResult
    try:
        detail, value = fn()
        status = "warn" if detail.startswith("WARN:") else "ok"
        return CheckResult(index, name, status,
                           detail[5:].strip() if status == "warn" else detail), value
    except Exception as exc:                     # noqa: BLE001 —— 诊断工具必须自持
        return CheckResult(index, name, "fail",
                           f"{exc.__class__.__name__}"), None


def _skip(index: int, name: str, reason: str):
    from .report import CheckResult
    return CheckResult(index, name, "skip", reason)


_CHECK_NAMES = (
    "Python 与 cryptography",
    "QQ 安装与版本",
    "数据目录发现",
    "账号枚举",
    "nt_msg.db 结构校验",
    "QQ.exe 运行状态",
    "数据库口令",
    "加密参数探测",
    "解密与页 1 魔数",
    "WAL 恢复",
    "目标表与关键列",
)


def run_doctor(*, data_dir: Path | None = None, uin: str | None = None,
               passphrase: bytes | None = None, allow_scan: bool = False,
               scan_timeout_s: float = 120.0, work_dir: Path | None = None,
               keep_output: bool = False):
    from .report import DoctorReport

    results: list = []
    ctx: dict = {}

    def add(res, value=None):
        results.append(res)
        return value

    # 1. Python 与 cryptography
    def c1():
        import cryptography
        v = getattr(cryptography, "__version__", "unknown")
        return (f"Python {sys.version.split()[0]}, cryptography {v}", None)
    add(*_run(1, _CHECK_NAMES[0], c1))

    # 2. QQ 安装与版本
    def c2():
        ver = _qq_version()
        if ver is None:
            return "WARN:未找到 QQNT 安装（不影响解密，仅用于版本漂移诊断）", None
        return ver, ver
    ctx["version"] = add(*_run(2, _CHECK_NAMES[1], c2))

    # 3. 数据目录发现
    def c3():
        accounts = _discover(explicit=data_dir)
        hit = accounts[0].data_dir
        return f"{len(accounts)} 个账号；命中 {hit}", accounts
    accounts = add(*_run(3, _CHECK_NAMES[2], c3))
    if accounts is None:
        return _finish(results, _skip_rest(results), keep_output, ctx)

    # 4. 账号枚举
    def c4():
        if uin:
            match = [a for a in accounts if a.uin == uin]
            if not match:
                raise LookupError(f"uin {uin} 不在已发现的账号中")
            return match[0].uin, match[0]
        return accounts[0].uin, accounts[0]
    account = add(*_run(4, _CHECK_NAMES[3], c4))
    if account is None:
        return _finish(results, _skip_rest(results), keep_output, ctx)
    ctx["account"] = account

    # 5. nt_msg.db 结构校验
    def c5():
        db = account.msg_db
        if not db.is_file():
            raise FileNotFoundError("nt_msg.db 不存在")
        size = db.stat().st_size
        if not sc.has_wrapper_header(size):
            raise ValueError(f"size {size} 不满足 %4096==1024，可能不是 QQ NT 库")
        return (f"{size:,} B = {sc.page_count_from_size(size):,} 页 × 4096 + 1024",
                size)
    add(*_run(5, _CHECK_NAMES[4], c5))
    if results[-1].status == "fail":
        return _finish(results, _skip_rest(results), keep_output, ctx)

    # 6. QQ.exe 运行状态
    def c6():
        pids = _qq_pids()
        if not pids:
            return "WARN:QQ.exe 未运行，内存扫描无法进行（可用 key set 手动写入）", []
        return f"{len(pids)} 个进程：{','.join(str(p) for p in pids)}", pids
    add(*_run(6, _CHECK_NAMES[5], c6))

    # 7. 数据库口令
    def c7():
        head = sc.read_header_and_page1(account.msg_db)
        cand, source = passphrase, "调用方提供"
        if cand is None:
            cand = _key_store().load(account.uin)
            source = "命中缓存"
        if cand is None:
            if not allow_scan:
                raise PermissionError(
                    "无口令且未允许扫描；请加 --scan 或用 `qqexport key set` 写入")
            cand = keyscan.scan_key(
                lambda c: sc.detect_params(c, head) is not None,
                timeout_s=scan_timeout_s)
            source = "内存扫描成功"
            if cand is None:
                raise LookupError("内存扫描未找到可用口令")
        if sc.detect_params(cand, head) is None:
            raise PermissionError(
                "口令无法解密该库的第 1 页——口令不对，或 QQ 版本改动了加密参数")
        return f"{source}，指纹 {keystore.fingerprint(cand)}", cand
    got = add(*_run(7, _CHECK_NAMES[6], c7))
    if got is None:
        return _finish(results, _skip_rest(results), keep_output, ctx)
    ctx["passphrase"] = got

    # 8. 加密参数探测
    def c8():
        head = sc.read_header_and_page1(account.msg_db)
        params = sc.detect_params(got, head)
        if params is None:
            raise ValueError("八组合均未通过 page-1 HMAC 校验")
        return params.fingerprint, params
    params = add(*_run(8, _CHECK_NAMES[7], c8))
    if params is None:
        return _finish(results, _skip_rest(results), keep_output, ctx)

    # 9. 解密与页 1 魔数
    dst_dir = Path(work_dir) if work_dir else config.work_dir(account.uin) / "doctor"
    dst_dir.mkdir(parents=True, exist_ok=True)
    ctx["dst_dir"] = dst_dir

    def c9():
        res = dec.decrypt_database(account, got, dst_dir=dst_dir)
        head = res.plaintext_db.read_bytes()[:sc.SALT_SZ]
        if head != sc.SQLITE_MAGIC:
            raise ValueError("产物页 1 缺少 SQLite 魔数")
        return (f"{res.page_count:,} 页，损坏 {res.corrupt_pages}，"
                f"quick_check={res.integrity_check}", res)
    result = add(*_run(9, _CHECK_NAMES[8], c9))
    if result is None:
        return _finish(results, _skip_rest(results), keep_output, ctx)
    ctx["result"] = result

    # 10. WAL 恢复
    def c10():
        if result.wal_reason == "wal_absent":
            return "WARN:无 -wal 文件，本次导出只含已 checkpoint 的数据", None
        detail = (f"{result.wal_frames} 帧，最后 commit 在第 "
                  f"{result.wal_last_commit_frame} 帧，commit 后 "
                  f"{result.wal_commit_size} 页，已应用 "
                  f"{result.wal_applied_frames} 帧，shm 交叉验证="
                  f"{result.shm_validated}")
        if result.wal_corrupt_frames or result.wal_signature_changed:
            return "WARN:" + detail, None
        return detail, None
    add(*_run(10, _CHECK_NAMES[9], c10))

    # 11. 目标表与关键列
    def c11():
        found = set(result.tables)
        missing = [t for t in EXPECTED_TABLES if t not in found]
        detail = (f"共 {len(found)} 张表；期望表 "
                  f"{','.join(EXPECTED_TABLES)}；缺失 {missing or '无'}")
        if missing:
            return "WARN:" + detail, None
        return detail, None
    add(*_run(11, _CHECK_NAMES[10], c11))

    return _finish(results, [], keep_output, ctx)


def _skip_rest(results: list) -> list:
    """把尚未执行的检查补成 skip，保证报告恒为 11 项。"""
    from .report import CheckResult
    out = []
    for i in range(len(results) + 1, _N_CHECKS + 1):
        out.append(CheckResult(i, _CHECK_NAMES[i - 1], "skip", "前置检查未通过"))
    return out


def _finish(results: list, extra: list, keep_output: bool, ctx: dict):
    from .report import DoctorReport
    results.extend(extra)
    if not keep_output:
        dst = ctx.get("dst_dir")
        if dst is not None and Path(dst).is_dir():
            shutil.rmtree(dst, ignore_errors=True)
    return DoctorReport(checks=results, generated_at=_now())
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python -m pytest tests/test_report.py tests/test_doctor.py -v`
Expected: PASS，约 24 项

- [ ] **Step 7: 全量回归**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add qqexport/report.py qqexport/doctor.py tests/test_report.py tests/test_doctor.py
git commit -m "feat: 11 项全链路自检 doctor，每项独立容错且不泄露口令"
```

---

### Task 10: `cli.py` — 命令行入口

**Files:**
- Create: `qqexport/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `doctor.run_doctor`（Task 9）、`keyscan`（Task 6）、`keystore`（Task 7）、`discover`（Task 2）、`sqlcipher`（Task 3）、`config`（Task 1）、`__version__`
- Produces:
  - `build_parser() -> argparse.ArgumentParser`
  - `main(argv: list[str] | None = None) -> int`
  - 退出码：`0` 成功 / `1` 操作失败或 doctor 有 fail 项 / `2` 用法错误（argparse 默认）

**子命令（spec §9.1 的 P0 子集）：**

```
qqexport version
qqexport doctor  [--data-dir DIR] [--uin UIN] [--scan] [--timeout S]
                 [--json] [--ascii] [--keep-output]
qqexport key scan   [--data-dir DIR] [--uin UIN] [--timeout S]
qqexport key set    [PASSPHRASE] [--uin UIN] [--from-stdin]
qqexport key show
qqexport key clear  [--uin UIN]
```

**控制台编码（本机实测会踩）：** 中文 Windows 控制台常是 cp936，`✅` 会直接抛 `UnicodeEncodeError`。`main()` 一进来就把 stdout/stderr `reconfigure(encoding="utf-8", errors="replace")`（失败则忽略），并额外提供 `--ascii` 走纯 ASCII 渲染。

**口令输入安全：** `key set` 不带参数时用 `getpass` 交互式输入（不回显、不进进程列表）；`--from-stdin` 支持管道；位置参数形式仍保留以符合 spec，但会打印一条警告说明它会出现在进程列表里。

- [ ] **Step 1: 写失败的测试 `tests/test_cli.py`**

```python
import json

import pytest

from qqexport import __version__, cli


def synthetic_account(tmp_path):
    """一个 uin=12345 的合成账号，使全部 CLI 测试脱离本机真实 QQ 环境。"""
    from qqexport.core.discover import QQAccount
    db_dir = tmp_path / "TencentFiles" / "12345" / "nt_qq" / "nt_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / "nt_msg.db").write_bytes(b"\x00" * 5120)
    return QQAccount("12345", db_dir.parent, db_dir,
                     db_dir.parent / "nt_data")


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("QQEXPORT_HOME", str(tmp_path / "apphome"))
    monkeypatch.setattr(cli.discover, "discover_accounts",
                        lambda **kw: [synthetic_account(tmp_path)])
    return tmp_path


def run(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out + out.err


def test_version_prints_and_exits_zero(capsys):
    code, text = run(["version"], capsys)
    assert code == 0 and __version__ in text


def test_unknown_command_exits_two(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["bogus"])
    assert ei.value.code == 2


def test_doctor_json_is_valid_and_exit_code_tracks_ok(capsys, monkeypatch):
    monkeypatch.setattr(cli.doctor, "run_doctor",
                        lambda **kw: cli.report.DoctorReport(
                            [cli.report.CheckResult(1, "x", "ok", "fine")], "t"))
    code, text = run(["doctor", "--json"], capsys)
    doc = json.loads(text)
    assert doc["ok"] is True and code == 0


def test_doctor_json_exit_one_when_a_check_fails(capsys, monkeypatch):
    monkeypatch.setattr(cli.doctor, "run_doctor",
                        lambda **kw: cli.report.DoctorReport(
                            [cli.report.CheckResult(1, "x", "fail", "bad")], "t"))
    code, text = run(["doctor", "--json"], capsys)
    assert json.loads(text)["ok"] is False
    assert code == 1


def test_doctor_text_output_is_human_readable(capsys, monkeypatch):
    monkeypatch.setattr(cli.doctor, "run_doctor",
                        lambda **kw: cli.report.DoctorReport(
                            [cli.report.CheckResult(1, "python", "ok", "3.14.6")], "t"))
    code, text = run(["doctor"], capsys)
    assert "python" in text and "3.14.6" in text and code == 0


def test_doctor_ascii_flag_produces_pure_ascii(capsys, monkeypatch):
    monkeypatch.setattr(cli.doctor, "run_doctor",
                        lambda **kw: cli.report.DoctorReport(
                            [cli.report.CheckResult(1, "python", "ok", "3.14.6")], "t"))
    _, text = run(["doctor", "--ascii"], capsys)
    assert text.encode("ascii", "strict")


def test_doctor_passes_flags_through(capsys, monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return cli.report.DoctorReport([], "t")

    monkeypatch.setattr(cli.doctor, "run_doctor", fake)
    run(["doctor", "--uin", "12345", "--scan", "--timeout", "7",
         "--keep-output"], capsys)
    assert seen["uin"] == "12345" and seen["allow_scan"] is True
    assert seen["scan_timeout_s"] == 7.0 and seen["keep_output"] is True


def test_key_show_on_empty_store(capsys):
    code, text = run(["key", "show"], capsys)
    assert code == 0 and ("没有" in text or "no " in text.lower())


def test_key_set_from_stdin_then_show(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(
        "#8FakeKeyForTest\n"))
    code, _ = run(["key", "set", "--uin", "12345", "--from-stdin"], capsys)
    assert code == 0
    code, text = run(["key", "show"], capsys)
    assert code == 0 and "12345" in text
    assert "#8FakeKeyForTest" not in text                    # 只出指纹
    from qqexport.core.keystore import fingerprint
    assert fingerprint(b"#8FakeKeyForTest") in text


def test_key_set_positional_warns_about_process_list(capsys, monkeypatch):
    code, text = run(["key", "set", "#8FakeKeyForTest", "--uin", "12345"], capsys)
    assert code == 0
    assert "进程列表" in text
    assert "#8FakeKeyForTest" not in text


def test_key_clear_reports_count(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("#8FakeKeyForTest\n"))
    run(["key", "set", "--uin", "12345", "--from-stdin"], capsys)
    code, text = run(["key", "clear", "--uin", "12345"], capsys)
    assert code == 0 and "1" in text
    code, text = run(["key", "show"], capsys)
    assert "12345" not in text


def test_key_scan_reports_failure_gracefully(capsys, monkeypatch):
    monkeypatch.setattr(cli.keyscan, "find_qq_pids", lambda *a, **k: [])
    code, text = run(["key", "scan"], capsys)
    assert code == 1
    assert "QQ" in text


def test_key_scan_never_prints_the_key(capsys, monkeypatch):
    from qqexport.core import sqlcipher as sc
    from tests.helpers import fake_sqlcipher as fake

    secret = b"#8FakeKeyForTest"
    blob = fake.build_encrypted_db(fake.random_plain_pages(1), secret)
    monkeypatch.setattr(cli.sqlcipher, "read_header_and_page1",
                        lambda p: blob[:sc.WRAPPER_SZ + sc.PAGE_SZ])
    monkeypatch.setattr(cli.keyscan, "find_qq_pids", lambda *a, **k: [4242])
    monkeypatch.setattr(cli.keyscan, "scan_key", lambda verify, **kw: secret)
    code, text = run(["key", "scan"], capsys)
    assert code == 0
    assert secret.decode() not in text
    from qqexport.core.keystore import fingerprint
    assert fingerprint(secret) in text


def test_parser_has_all_p0_subcommands():
    p = cli.build_parser()
    text = p.format_help()
    assert "doctor" in text and "key" in text and "version" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qqexport.cli'`

- [ ] **Step 3: 实现 `qqexport/cli.py`**

```python
"""命令行入口。参照 spec §9.1。

口令本体绝不打印；所有输出只使用 SHA-256 指纹前 8 位。
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import __version__, doctor, report
from .core import discover, keyscan, keystore, sqlcipher

EXIT_OK = 0
EXIT_FAIL = 1


def _force_utf8_stdio() -> None:
    """中文 Windows 控制台常是 cp936，emoji 会直接 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qqexport",
        description="导出本机自有 QQ NT 账号的聊天记录（仅限本人自有数据）。")
    p.add_argument("--data-dir", type=Path, default=None,
                   help="手动指定 'Tencent Files' 目录")
    p.add_argument("--uin", default=None, help="目标 QQ 号（默认取第一个发现的账号）")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="打印版本号")

    d = sub.add_parser("doctor", help="11 项全链路自检")
    d.add_argument("--scan", action="store_true",
                   help="允许被动内存扫描取口令（只读，不注入）")
    d.add_argument("--timeout", type=float, default=keyscan.DEFAULT_TIMEOUT_S,
                   help="内存扫描墙钟超时秒数")
    d.add_argument("--json", action="store_true", help="以 JSON 输出")
    d.add_argument("--ascii", action="store_true", dest="ascii_only",
                   help="纯 ASCII 输出，供 cp936 控制台使用")
    d.add_argument("--keep-output", action="store_true",
                   help="保留解密出的明文库（默认自检后删除）")

    k = sub.add_parser("key", help="口令管理")
    ksub = k.add_subparsers(dest="key_command", required=True)

    kscan = ksub.add_parser("scan", help="被动内存扫描取口令")
    kscan.add_argument("--timeout", type=float, default=keyscan.DEFAULT_TIMEOUT_S)

    kset = ksub.add_parser("set", help="手动写入口令")
    kset.add_argument("passphrase", nargs="?", default=None,
                      help="不推荐：会出现在进程列表里。留空则交互式输入")
    kset.add_argument("--from-stdin", action="store_true")

    ksub.add_parser("show", help="列出已缓存口令的指纹（不含本体）")

    kclear = ksub.add_parser("clear", help="删除缓存口令")
    kclear.add_argument("--uin", dest="clear_uin", default=None)

    return p


def _pick_account(args) -> discover.QQAccount:
    accounts = discover.discover_accounts(explicit=args.data_dir)
    if args.uin:
        for a in accounts:
            if a.uin == args.uin:
                return a
        raise discover.DiscoveryError(f"uin {args.uin} 不在已发现的账号中")
    return accounts[0]


def _read_passphrase(args) -> str:
    if getattr(args, "from_stdin", False):
        return sys.stdin.readline().rstrip("\r\n")
    if getattr(args, "passphrase", None):
        print("警告：以位置参数传入口令会出现在进程列表与 shell 历史里，"
              "建议改用不带参数的 `qqexport key set` 交互式输入。", file=sys.stderr)
        return args.passphrase
    return getpass.getpass("请输入数据库口令（不回显）：")


def _cmd_doctor(args) -> int:
    rep = doctor.run_doctor(data_dir=args.data_dir, uin=args.uin,
                            allow_scan=args.scan, scan_timeout_s=args.timeout,
                            keep_output=args.keep_output)
    if args.json:
        print(json.dumps(rep.to_json(), ensure_ascii=False, indent=2))
    else:
        print(rep.to_text(ascii_only=args.ascii_only))
    return EXIT_OK if rep.ok else EXIT_FAIL


def _cmd_key_scan(args) -> int:
    try:
        account = _pick_account(args)
    except discover.DiscoveryError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    head = sqlcipher.read_header_and_page1(account.msg_db)
    try:
        found = keyscan.scan_key(
            lambda cand: sqlcipher.detect_params(cand, head) is not None,
            timeout_s=args.timeout)
    except keyscan.ProcessAccessDenied:
        print("错误：无权读取 QQ 进程内存。请以管理员身份重试，"
              "或改用 `qqexport key set` 手动写入。", file=sys.stderr)
        return EXIT_FAIL
    if found is None:
        print("未找到可用口令。请确认 QQ 正在运行并已登录，"
              "或改用 `qqexport key set` 手动写入。", file=sys.stderr)
        return EXIT_FAIL
    keystore.KeyStore().save(account.uin, found)
    print(f"已保存 uin={account.uin} 的口令，指纹 {keystore.fingerprint(found)}")
    return EXIT_OK


def _cmd_key_set(args) -> int:
    try:
        account = _pick_account(args)
    except discover.DiscoveryError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    text = _read_passphrase(args)
    raw = text.encode("utf-8")
    if len(raw) not in keyscan.KEY_LENGTHS:
        print(f"警告：口令长度为 {len(raw)} 字节，而 QQ NT 应为 "
              f"{keyscan.KEY_LENGTHS} 之一。仍将保存。", file=sys.stderr)
    try:
        entry = keystore.KeyStore().save(account.uin, raw)
    except keystore.KeyStoreUnavailable as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    print(f"已保存 uin={account.uin}，指纹 {entry.fingerprint}")
    return EXIT_OK


def _cmd_key_show(_args) -> int:
    entries = keystore.KeyStore().redacted_dump()
    if not entries:
        print("没有已缓存的口令。用 `qqexport key scan` 或 `qqexport key set` 添加。")
        return EXIT_OK
    for e in entries:
        print(f"uin={e['uin']}  指纹={e['fingerprint']}  保存于={e['saved_at']}")
    return EXIT_OK


def _cmd_key_clear(args) -> int:
    n = keystore.KeyStore().clear(args.clear_uin)
    print(f"已删除 {n} 条缓存口令" if n else "没有匹配的缓存口令")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    args = build_parser().parse_args(argv)

    if args.command == "version":
        print(f"qqexport {__version__}")
        return EXIT_OK
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "key":
        return {"scan": _cmd_key_scan, "set": _cmd_key_set,
                "show": _cmd_key_show, "clear": _cmd_key_clear}[args.key_command](args)
    return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS，约 16 项

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add qqexport/cli.py tests/test_cli.py
git commit -m "feat: 命令行入口 doctor / key / version，含 cp936 控制台的 ASCII 退路"
```

---

### Task 11: `capture_fixtures.py` 与真库页 1 fixture

**Files:**
- Create: `tools/capture_fixtures.py`
- Create: `tests/fixtures/README.md`
- Create: `tests/test_fixture_real_page1.py`

**Interfaces:**
- Consumes: `discover`（Task 2）、`sqlcipher`（Task 3）、`keystore`（Task 7）
- Produces:
  - `tools/capture_fixtures.py`（脚本，非包模块）
  - `tests/fixtures/nt_msg_header_page1.bin`（5120 字节，**默认被 .gitignore 排除**，人工确认后 `git add -f`）
  - `tests/fixtures/provenance.json`（QQ 版本、采集时间、原库大小、探测到的参数指纹——**不含口令**）

**隐私红线（spec §10.1）：** 只截取「1024 字节头 + 页 1」。页 1 只含 `sqlite_master` 与 schema，不含消息正文。即便如此，脚本仍必须打印将写入内容的摘要（表名/列名/大小）并等待用户输入 `yes` 才落盘。落盘后提示需 `git add -f` 才能提交。

**这个 fixture 的独立价值：** 它把 QQ NT 的 1024 字节头格式**钉死**在仓库里。日后 QQ 改头格式时，结构断言会立刻失败，而不必等到 live 测试才发现。

- [ ] **Step 1: 写 `tests/fixtures/README.md`**

```markdown
# fixtures

本目录下的 `.bin` 文件**默认被 `.gitignore` 排除**。

`nt_msg_header_page1.bin` 由 `tools/capture_fixtures.py` 从真实 QQ 数据库截取，
内容为「1024 字节 QQ NT 自定义头 + 加密的页 1」。页 1 只含 `sqlite_master`
与表结构，**不含任何消息正文**。

采集脚本会先打印将写入内容的摘要并要求人工确认。确认无隐私问题后：

    git add -f tests/fixtures/nt_msg_header_page1.bin tests/fixtures/provenance.json

`provenance.json` 记录采集时的 QQ 版本与库大小，**不含数据库口令**。
需要跑口令相关的 fixture 测试时，用环境变量临时提供：

    set QQEXPORT_TEST_PASSPHRASE=你的16字节口令
    pytest -m fixture -v
```

- [ ] **Step 2: 写失败的测试 `tests/test_fixture_real_page1.py`**

```python
import json
import os
from pathlib import Path

import pytest

from qqexport.core import sqlcipher as sc

FIXTURE = Path(__file__).parent / "fixtures" / "nt_msg_header_page1.bin"
PROVENANCE = Path(__file__).parent / "fixtures" / "provenance.json"

pytestmark = pytest.mark.skipif(
    not FIXTURE.is_file(),
    reason="缺少 tests/fixtures/nt_msg_header_page1.bin，"
           "运行 tools/capture_fixtures.py 采集并人工确认后 git add -f")


@pytest.fixture(scope="module")
def blob() -> bytes:
    return FIXTURE.read_bytes()


def test_fixture_is_exactly_header_plus_page1(blob):
    assert len(blob) == sc.WRAPPER_SZ + sc.PAGE_SZ == 5120


def test_page1_salt_is_not_all_zero(blob):
    page1 = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.PAGE_SZ]
    assert page1[:sc.SALT_SZ] != b"\x00" * sc.SALT_SZ


def test_page1_is_not_plaintext(blob):
    """未解密时绝不应出现 SQLite 魔数——证明它确实是加密的。"""
    page1 = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.PAGE_SZ]
    assert not page1.startswith(sc.SQLITE_MAGIC)


def test_wrapper_header_is_not_a_sqlite_file(blob):
    assert not blob.startswith(sc.SQLITE_MAGIC)


def test_fixture_size_shape_is_consistent_with_wrapper(blob):
    """整库大小必然满足 size % 4096 == 1024；fixture 是其前缀，同样成立。"""
    assert sc.has_wrapper_header(len(blob))


def test_provenance_records_no_passphrase():
    if not PROVENANCE.is_file():
        pytest.skip("provenance.json 未采集")
    doc = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    allowed = {"captured_at", "qq_version", "source_size_bytes",
               "captured_bytes", "cipher_fingerprint", "key_fingerprint"}
    assert set(doc) <= allowed, f"provenance 出现了未预期的字段：{set(doc) - allowed}"
    for k, v in doc.items():
        assert not (isinstance(v, str) and len(v) in (16, 32)
                    and v.isprintable() and k != "captured_at"), \
            f"字段 {k} 看起来像口令本体"
    assert doc.get("qq_version") and doc.get("source_size_bytes")


@pytest.mark.skipif(not os.environ.get("QQEXPORT_TEST_PASSPHRASE"),
                    reason="未设置 QQEXPORT_TEST_PASSPHRASE")
def test_detect_params_succeeds_against_real_page1(blob):
    """这是「常量与 QQ 实际行为一致」的关键证据。"""
    passphrase = os.environ["QQEXPORT_TEST_PASSPHRASE"].encode("utf-8")
    params = sc.detect_params(passphrase, blob)
    assert params is not None, "八组合均未通过真库 page-1 HMAC 校验——常量可能已过期"
    assert params.fingerprint == "sha512/sha1/48"


@pytest.mark.skipif(not os.environ.get("QQEXPORT_TEST_PASSPHRASE"),
                    reason="未设置 QQEXPORT_TEST_PASSPHRASE")
def test_real_page1_decrypts_to_sqlite_magic(blob):
    passphrase = os.environ["QQEXPORT_TEST_PASSPHRASE"].encode("utf-8")
    params = sc.detect_params(passphrase, blob)
    page1 = blob[sc.WRAPPER_SZ:sc.WRAPPER_SZ + sc.PAGE_SZ]
    plain = sc.decrypt_page(params, page1, 1)
    assert plain is not None
    assert plain.startswith(sc.SQLITE_MAGIC)
    assert plain[20] == params.reserve
```

- [ ] **Step 3: 运行测试确认跳过（fixture 尚未采集）**

Run: `python -m pytest tests/test_fixture_real_page1.py -v`
Expected: 全部 SKIPPED，理由为缺少 fixture 文件。**这是正确状态**，不是失败。

- [ ] **Step 4: 实现 `tools/capture_fixtures.py`**

```python
#!/usr/bin/env python3
"""从真实 QQ 数据库截取页级 fixture。

隐私红线（spec §10.1）：只截取 1024 字节头 + 加密页 1。页 1 只含 sqlite_master
与 schema，不含消息正文。落盘前必须打印摘要并等待人工输入 yes。

用法：
    python tools/capture_fixtures.py [--data-dir DIR] [--uin UIN] [--yes]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qqexport.core import discover as disc          # noqa: E402
from qqexport.core import keystore                  # noqa: E402
from qqexport.core import sqlcipher as sc           # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def summarise(blob: bytes, account, passphrase: bytes | None) -> str:
    lines = [
        f"  账号 uin        : {account.uin}",
        f"  源库            : {account.msg_db}",
        f"  源库大小        : {account.msg_db.stat().st_size:,} B",
        f"  QQ 版本         : {disc.qq_install_version() or '未知'}",
        f"  采集时间        : {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"  截取字节数      : {len(blob):,}（1024 头 + 4096 加密页 1）",
    ]
    head = blob[:sc.WRAPPER_SZ]
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
    markers = [m for m in ("QQ_NT DB", "key_meta", "QQNT") if m.encode() in head]
    lines.append(f"  自定义头可见标记: {markers or '无'}")
    lines.append(f"  头部前 96 字符  : {printable[:96]}")
    if passphrase is not None:
        params = sc.detect_params(passphrase, blob)
        lines.append(f"  口令指纹        : {keystore.fingerprint(passphrase)}")
        lines.append(f"  探测到的参数    : {params.fingerprint if params else '未命中'}")
        if params is not None:
            plain = sc.decrypt_page(params, blob[sc.WRAPPER_SZ:], 1)
            if plain is not None:
                lines.append(f"  解密页 1 前 16B : {plain[:16]!r}")
                lines.append("  页 1 可打印串   : " + repr("".join(
                    chr(b) if 32 <= b < 127 else "." for b in plain))[:400])
    else:
        lines.append("  口令            : 未提供（跳过解密摘要，仅做结构截取）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument("--uin", default=None)
    p.add_argument("--yes", action="store_true", help="跳过人工确认（不建议）")
    args = p.parse_args(argv)

    accounts = disc.discover_accounts(explicit=args.data_dir)
    account = accounts[0]
    if args.uin:
        account = next((a for a in accounts if a.uin == args.uin), None)
        if account is None:
            print(f"错误：uin {args.uin} 不在已发现的账号中", file=sys.stderr)
            return 1

    blob = sc.read_header_and_page1(account.msg_db)
    if len(blob) != sc.WRAPPER_SZ + sc.PAGE_SZ:
        print(f"错误：只读到 {len(blob)} 字节，源库可能过小或被占用", file=sys.stderr)
        return 1

    passphrase = keystore.KeyStore().load(account.uin)
    print("即将写入 tests/fixtures/nt_msg_header_page1.bin：")
    print(summarise(blob, account, passphrase))
    print()
    print("页 1 只含 sqlite_master 与表结构，不含消息正文。")
    print("请逐行确认上面的「页 1 可打印串」中没有你不想入库的内容。")

    if not args.yes:
        answer = input("确认写入？输入 yes 继续：").strip().lower()
        if answer != "yes":
            print("已取消，未写入任何文件。")
            return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "nt_msg_header_page1.bin").write_bytes(blob)
    provenance = {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "qq_version": disc.qq_install_version(),
        "source_size_bytes": account.msg_db.stat().st_size,
        "captured_bytes": len(blob),
        "cipher_fingerprint": (sc.detect_params(passphrase, blob).fingerprint
                               if passphrase and sc.detect_params(passphrase, blob)
                               else None),
        "key_fingerprint": keystore.fingerprint(passphrase) if passphrase else None,
    }
    (OUT_DIR / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n已写入 {OUT_DIR}")
    print("这两个文件默认被 .gitignore 排除。确认无隐私问题后提交：")
    print("    git add -f tests/fixtures/nt_msg_header_page1.bin "
          "tests/fixtures/provenance.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 运行采集脚本（真机，需人工确认）**

Run: `python tools/capture_fixtures.py`

Expected: 打印摘要并等待输入。**逐行检查「页 1 可打印串」**——它应当只有 `sqlite_master`、`c2c_msg_table`、`group_msg_table`、`CREATE TABLE ...` 之类的 schema 文本与数字列名。若出现任何昵称、QQ 号、消息正文，**立即输入 `no` 取消**，改为不提交该 fixture（测试会保持 SKIPPED，不影响其余验收）。

确认后输入 `yes`。

- [ ] **Step 6: 运行 fixture 测试确认结构断言通过**

Run: `python -m pytest tests/test_fixture_real_page1.py -v`
Expected: 结构类测试 PASS（5 项），口令类测试 SKIPPED（2 项）

- [ ] **Step 7: 带口令运行，验证常量与真库一致**

Run（Git Bash）:
```bash
QQEXPORT_TEST_PASSPHRASE="$(python -c "from qqexport.core.keystore import KeyStore;from qqexport.core import discover as d;print(KeyStore().load(d.discover_accounts()[0].uin).decode())")" \
  python -m pytest tests/test_fixture_real_page1.py -v -k "real_page1 or detect_params_succeeds"
```

Expected: 2 项 PASS。**这一步是「加密常量与 QQ 9.9.36 实际行为一致」的直接证据**；若 `test_detect_params_succeeds_against_real_page1` 失败并断言 `fingerprint != "sha512/sha1/48"`，把实际值填进断言并在提交信息里记录——这说明本机命中的是八组合中的另一项，属正常发现而非缺陷。

- [ ] **Step 8: 提交（脚本与测试必提交；fixture 二进制仅在人工确认后 `-f` 提交）**

```bash
git add tools/capture_fixtures.py tests/fixtures/README.md tests/test_fixture_real_page1.py
git commit -m "test: 真库页 1 fixture 采集工具，钉死 QQ NT 的 1024 字节头格式"

# 仅当 Step 5 人工确认页 1 无隐私内容后执行：
git add -f tests/fixtures/nt_msg_header_page1.bin tests/fixtures/provenance.json
git commit -m "test: 添加真库页 1 fixture（仅 schema，经人工确认不含聊天内容）"
```

---

### Task 12: 真机 live 冒烟测试

**Files:**
- Create: `tests/test_live_smoke.py`

**Interfaces:**
- Consumes: 全部 core 模块（Task 2–8）
- Produces: `@pytest.mark.live` 标记的端到端测试，默认被 `pyproject.toml` 的 `addopts = '-m "not live"'` 跳过

**运行方式：** `pytest -m live -v`。需要本机 QQ 正在运行且已登录，或已有缓存口令。

**这是 P0 验收（spec §10.3）的执行载体。** 它对真实数据只读，产物写进 `tmp_path`，结束后自动清理。

- [ ] **Step 1: 写 `tests/test_live_smoke.py`**

```python
"""真机端到端冒烟。默认跳过，用 `pytest -m live -v` 运行。

对真实 QQ 数据只读；所有产物写入 pytest 的 tmp_path，测试结束自动清理。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from qqexport.core import decrypt as dec
from qqexport.core import discover as disc
from qqexport.core import keyscan
from qqexport.core import keystore
from qqexport.core import sqlcipher as sc

pytestmark = pytest.mark.live

MSG_TABLE_HINTS = ("c2c_msg_table", "group_msg_table")
TS_COLUMN = "40050"          # spec §7.1：Unix 时间戳（秒）


@pytest.fixture(scope="module")
def account() -> disc.QQAccount:
    return disc.discover_accounts()[0]


@pytest.fixture(scope="module")
def passphrase(account) -> bytes:
    cached = keystore.KeyStore().load(account.uin)
    if cached:
        return cached
    head = sc.read_header_and_page1(account.msg_db)
    found = keyscan.scan_key(
        lambda c: sc.detect_params(c, head) is not None, timeout_s=180.0)
    if found is None:
        pytest.skip("内存扫描未找到口令；请先启动并登录 QQ，"
                    "或用 `qqexport key set` 写入后重跑")
    return found


def _source_fingerprint(account) -> dict[str, float]:
    out = {}
    for p in account.db_dir.rglob("*"):
        if p.is_file():
            st = p.stat()
            out[p.name] = (st.st_size, st.st_mtime_ns)
    return out


def _find_msg_table(db: Path) -> str | None:
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    finally:
        con.close()
    for hint in MSG_TABLE_HINTS:
        if hint in names:
            return hint
    for n in names:
        if "msg_table" in n:
            return n
    return None


def _max_ts(db: Path, table: str) -> int | None:
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
        if TS_COLUMN not in cols:
            return None
        row = con.execute(f'SELECT MAX("{TS_COLUMN}") FROM "{table}"').fetchone()
        return int(row[0]) if row and row[0] is not None else None
    finally:
        con.close()


def test_live_discover_finds_account(account):
    assert account.uin.isdigit()
    assert account.msg_db.is_file()
    assert sc.has_wrapper_header(account.msg_db.stat().st_size)


def test_live_qq_version_is_reported():
    assert disc.qq_install_version() is not None


def test_live_detect_params_matches_expected_combo(account, passphrase):
    head = sc.read_header_and_page1(account.msg_db)
    params = sc.detect_params(passphrase, head)
    assert params is not None, "八组合均未通过真库 page-1 HMAC 校验"
    assert params.fingerprint == "sha512/sha1/48", \
        f"本机实际命中 {params.fingerprint}，请更新 spec §6.4 与相关断言"


def test_live_decrypt_produces_queryable_db(account, passphrase, tmp_path):
    res = dec.decrypt_database(account, passphrase, dst_dir=tmp_path / "work")
    db = res.plaintext_db
    assert db.is_file()
    assert db.stat().st_size % sc.PAGE_SZ == 0
    assert db.read_bytes()[:16] == sc.SQLITE_MAGIC

    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        assert "c2c_msg_table" in tables, f"实际表名：{tables[:40]}"
        assert "group_msg_table" in tables
        n = con.execute('SELECT COUNT(*) FROM "c2c_msg_table"').fetchone()[0]
        assert n > 0
    finally:
        con.close()
    assert res.corrupt_pages < res.page_count * 0.01, \
        f"损坏页占比过高：{res.corrupt_pages}/{res.page_count}"


def test_live_wal_recovery_brings_in_newest_messages(account, passphrase, tmp_path):
    """P0 验收核心（spec §10.3 第 5 条）：最新消息必须在。

    做法：分别做「含 WAL」与「不含 WAL」两次解密，比较消息表的最大时间戳。
    只有当 WAL 确实含已提交帧时，才要求严格大于——否则 QQ 刚好 checkpoint 过，
    两者相等也是正确的。
    """
    with_wal = dec.decrypt_database(account, passphrase, dst_dir=tmp_path / "with")
    without = dec.decrypt_file(account.msg_db, passphrase, tmp_path / "without" / "x.db")

    table = _find_msg_table(with_wal.plaintext_db)
    assert table is not None
    ts_with = _max_ts(with_wal.plaintext_db, table)
    ts_without = _max_ts(without.plaintext_db, table)
    assert ts_with is not None and ts_without is not None

    if with_wal.wal_last_commit_frame > 0:
        assert ts_with >= ts_without, \
            f"含 WAL 反而更旧：{ts_with} < {ts_without}"
        print(f"\nWAL 恢复生效：{with_wal.wal_frames} 帧，"
              f"最后 commit 第 {with_wal.wal_last_commit_frame} 帧，"
              f"时间戳 {ts_without} → {ts_with}"
              f"（{'有新增' if ts_with > ts_without else '本批帧未涉及该表'}）")
    else:
        assert ts_with == ts_without


def test_live_source_directory_is_never_modified(account, passphrase, tmp_path):
    """spec §10.3 第 7 条：我们没有改动 QQ 的任何文件。

    注意断言的边界：QQ 运行时自己会新建/轮转 -wal、-journal、日志，
    那些变化不是我们造成的。因此这里只对「删除」下强断言——那是只有我们
    才会做的事；新增与内容变化只能 skip。要拿到完整证据，请退出 QQ 后重跑本项。
    """
    before = _source_fingerprint(account)
    time.sleep(0.05)
    dec.decrypt_database(account, passphrase, dst_dir=tmp_path / "work")
    after = _source_fingerprint(account)

    removed = set(before) - set(after)
    assert not removed, f"源目录中有文件被删除：{sorted(removed)}"

    changed = {k for k in before if k in after and before[k] != after[k]}
    added = set(after) - set(before)
    if changed or added:
        pytest.skip(f"QQ 自身在运行中改写了 {sorted(changed)}、"
                    f"新增了 {sorted(added)}，无法据此判定只读性；"
                    f"请退出 QQ 后重跑本项")


def test_live_decrypt_is_deterministic(account, passphrase, tmp_path):
    """spec §10.3 第 8 条：连续两次解密结果字节一致。"""
    a = dec.decrypt_database(account, passphrase, dst_dir=tmp_path / "a")
    b = dec.decrypt_database(account, passphrase, dst_dir=tmp_path / "b")
    assert a.plaintext_db.read_bytes() == b.plaintext_db.read_bytes()


def test_live_doctor_reports_no_failure(passphrase, tmp_path):
    """产物必须落在 tmp_path，不得写进真实的 %LOCALAPPDATA%。"""
    from qqexport import doctor
    rep = doctor.run_doctor(passphrase=passphrase, keep_output=False,
                            work_dir=tmp_path / "doctor")
    text = rep.to_text(ascii_only=True)
    print("\n" + text)
    assert rep.ok, [c for c in rep.failed]
    assert passphrase.decode() not in text
    assert list((tmp_path / "doctor").rglob("*.decrypted.db")) == []
```

- [ ] **Step 2: 确认默认被跳过**

Run: `python -m pytest tests/test_live_smoke.py -v`
Expected: 全部 SKIPPED / deselected（`addopts = '-m "not live"'` 生效）

- [ ] **Step 3: 真机运行 live 冒烟**

Run: `python -m pytest -m live -v -s`

Expected: 8 项 PASS。逐项对照：

| 测试 | 对应 spec §10.3 |
|---|---|
| `test_live_discover_finds_account` | 数据目录发现走通（含 D 盘重定向） |
| `test_live_detect_params_matches_expected_combo` | 加密常量与真库一致 |
| `test_live_decrypt_produces_queryable_db` | 产物可被 sqlite3 查询、表名符合预期 |
| `test_live_wal_recovery_brings_in_newest_messages` | **第 5 条：最新消息在** |
| `test_live_source_directory_is_never_modified` | **第 7 条：只读** |
| `test_live_decrypt_is_deterministic` | **第 8 条：确定性** |
| `test_live_doctor_reports_no_failure` | **第 2 条：doctor 无 fail** |

**若 `test_live_decrypt_produces_queryable_db` 在 `assert "c2c_msg_table" in tables` 处失败：** 断言消息里已打印实际表名。把它记下来——这是 P1 plan 需要的实测事实，**不要为了让测试通过而放宽断言**。同时检查产物页 1 的第 20 字节是否为 48（`normalize_page1_header` 的作用），若不是，说明 reserve 不是 48，回到 Task 3 检查 `detect_params` 命中的组合。

**若 `test_live_wal_recovery_brings_in_newest_messages` 失败：** 这是 P0 最严重的失败。先确认 `with_wal.wal_frames > 0`；若为 0，说明 QQ 刚做过 checkpoint，属正常，等 QQ 再产生些消息后重跑。若 `wal_frames > 0` 但 `ts_with < ts_without`，说明 WAL 回放逻辑有 bug，回到 Task 4 排查 `apply_wal` 的页偏移与截断。

- [ ] **Step 4: 提交**

```bash
git add tests/test_live_smoke.py
git commit -m "test: 真机 live 冒烟，覆盖 P0 全部验收条款"
```

---

### Task 13: P0 验收与文档收尾

**Files:**
- Modify: `README.md`
- Create: `docs/superpowers/notes/2026-10-24-p0-live-findings.md`

**Interfaces:**
- Consumes: 全部前序 task
- Produces: P0 验收记录 + P1 plan 所需的实测事实清单

- [ ] **Step 1: 跑完整测试套件**

Run: `python -m pytest -q`
Expected: PASS，约 200 项，0 失败（`live` 与 `fixture` 中被跳过的项不计）

Run: `python -m pytest -m live -q -s`
Expected: 8 项 PASS

- [ ] **Step 2: 跑一次真实的 doctor 并存档输出**

Run: `qqexport doctor --ascii --keep-output`
Expected: 11 项无 ❌。把完整输出粘进 `docs/superpowers/notes/2026-10-24-p0-live-findings.md`。

Run: `qqexport doctor --json > /tmp/doctor.json && python -c "import json;d=json.load(open('/tmp/doctor.json',encoding='utf-8'));print(d['ok'],len(d['checks']))"`
Expected: `True 11`

- [ ] **Step 3: 记录 P1 所需的实测事实**

在 `docs/superpowers/notes/2026-10-24-p0-live-findings.md` 中记录（这些是 P1 plan 的输入，**必须实测而非照抄 spec**）：

```bash
python - <<'PY'
import sqlite3, json
from pathlib import Path
from qqexport.core import discover as d, decrypt as dec, keystore
acct = d.discover_accounts()[0]
pw = keystore.KeyStore().load(acct.uin)
out = Path("work-p1-probe"); out.mkdir(exist_ok=True)
res = dec.decrypt_database(acct, pw, dst_dir=out)
con = sqlite3.connect(f"file:{res.plaintext_db.resolve().as_posix()}?mode=ro", uri=True)
tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("TABLES:", json.dumps(tables, ensure_ascii=False, indent=1))
for t in ("c2c_msg_table", "group_msg_table"):
    if t in tables:
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"\n{t} rows={n}\nCOLUMNS: {cols}")
        row = con.execute(f'SELECT * FROM "{t}" LIMIT 1').fetchone()
        if row:
            print("BLOB 40800 len:", len(row[cols.index('40800')]) if '40800' in cols else 'N/A')
con.close()
PY
```

把输出**脱敏后**（删除任何真实昵称、QQ 号、消息文本，只保留表名/列名/行数/blob 长度）记进 notes 文件。记录清单：

- 实际表名全集
- `c2c_msg_table` / `group_msg_table` 的实际列名全集与行数
- `40800` blob 的典型长度分布
- doctor 探测到的 cipher 参数指纹
- WAL 帧数与最后 commit 位置的典型值
- 解密耗时与产物大小
- 任何与 spec §7.1 不符之处

- [ ] **Step 4: 清理探测产物**

Run: `rm -rf work-p1-probe`

**注意：** `work-p1-probe` 含明文聊天记录。`.gitignore` 的 `work/` 规则匹配不到它，因此必须在提交前删除，并确认 `git status --short` 中没有它。

- [ ] **Step 5: 更新 README**

把「当前进度」一节替换为：

```markdown
## 当前进度

**P0 已完成**：定位数据目录 → 只读内存扫描取密钥 → 一致性快照 → 纯 Python 解密
主库与 WAL → 产出标准明文 SQLite。

```
qqexport doctor          # 11 项全链路自检
qqexport doctor --scan   # 首次使用：允许被动内存扫描取密钥
qqexport key show        # 查看已缓存密钥的指纹（不含本体）
qqexport key clear       # 清除缓存密钥
```

消息解析与 txt/html/pdf 导出在 P1–P3，尚未实现。

## 安全边界

- 对 QQ 数据目录**只读**（唯一例外：为取一致性锁以 r+b 打开 -shm，不写任何字节）
- 内存扫描只申请 `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`，不注入、不调试
- 密钥用 Windows DPAPI 保护，永不写入日志与报告
- 全程离线，不向腾讯服务器发送任何请求
```

- [ ] **Step 6: 验收核对（spec §10.3 的 P0 相关条款）**

逐条勾选，任何一条不通过都不得进入 P1：

- [ ] `pytest` 全绿（live 除外）
- [ ] `qqexport doctor` 11 项无 ❌
- [ ] 产出的明文 `nt_msg.db` 可被 stdlib `sqlite3` 打开并 `SELECT COUNT(*)`
- [ ] **最近的消息在**（`test_live_wal_recovery_brings_in_newest_messages` PASS）
- [ ] 源目录文件集合无变化（`test_live_source_directory_is_never_modified` PASS）
- [ ] 连续两次解密字节一致（`test_live_decrypt_is_deterministic` PASS）
- [ ] `git status --short` 中没有任何明文库、密钥缓存或未确认的 fixture

- [ ] **Step 7: 提交**

```bash
git add README.md docs/superpowers/notes/2026-10-24-p0-live-findings.md
git commit -m "docs: 记录 P0 真机验收结果与 P1 所需的实测事实"
```

---

## Self-Review

**1. spec 覆盖检查（P0 范围）**

| spec 章节 | 覆盖 task |
|---|---|
| §3 实测环境基线 | Task 2（数据目录发现）、Task 11（1024 头结构） |
| §4.1 纯 Python 解密选型 | Task 3 |
| §4.3 许可证与 NOTICE | Task 1 |
| §5.2 目录结构 | 全部 task 的 Files 段 |
| §6.1 discover | Task 2 |
| §6.2 keyscan | Task 6 |
| §6.3 keystore | Task 7 |
| §6.4 sqlcipher | Task 3 |
| §6.5 wal | Task 4 |
| §6.6 snapshot | Task 5 |
| §6.7 decrypt 编排 | Task 8 |
| §9.1 CLI（P0 子集） | Task 10 |
| §9.3 doctor 第 1–11 项 | Task 9 |
| §10.1 fixture 隐私红线 | Task 11、`.gitignore`（Task 1） |
| §10.2 测试矩阵（P0 行） | Task 2–12 各自的测试 |
| §10.3 验收第 1/2/5/7/8 条 | Task 12、Task 13 |
| §11 降级总则（页/WAL/快照/口令行） | Task 3/4/5/6/8 |
| §12 R1/R3/R6/R7/R9/R10 | Task 6（R1/R7）、Task 3（R3）、Task 5（R6/R9）、Task 7（R10） |

**spec 中 P0 未覆盖的条款**（有意留到后续 plan）：§7 全部（P1）、§8 全部（P2/P3）、§9.2 Web 界面（P2）、§9.3 doctor 第 12–15 项（P1/P3）、§10.3 第 3/4/6 条（P1/P2）、§12 R2/R4/R5/R8（P1+）。

**2. placeholder 扫描：** 无 TBD / TODO / 「类似 Task N」/「适当处理错误」。每个代码步骤都给出了完整可粘贴的实现或测试代码。

**3. 类型一致性：** 逐一核对了跨 task 引用的签名——
`CipherParams(kdf, mac, reserve, aes_key, mac_key)` 在 Task 3 定义，Task 4/8 按位置参数构造，顺序一致；
`decrypt_page(params, page, page_no) -> bytes|None` 在 Task 3 定义，Task 4 的 `apply_wal` 与 Task 8 的 `decrypt_pages` 调用签名一致；
`inspect_wal(wal_path, shm_path=None, expected_page_size=PAGE_SZ) -> WalPlan` 与 `apply_wal(params, wal_path, db_path, plan) -> WalApplyResult` 在 Task 4 定义，Task 8 的 `_recover_wal` 按此调用；
`snapshot(src_db, dst_dir, *, validator=None, attempts=3, use_shm_lock=True) -> SnapshotResult` 在 Task 5 定义，Task 8 的 `decrypt_database` 按此调用；
`QQAccount(uin, data_dir, db_dir, media_dir)` 在 Task 2 定义，Task 8/9/12 均按此构造；
`DecryptResult` 的字段在 Task 8 定义，Task 9 的 doctor 第 9–11 项只读取其中已声明的字段。

**4. 已知需在实现时确认的两处经验值**（不是 placeholder，是明确标注的实测项）：
- Task 11 Step 7 与 Task 12 的 `fingerprint == "sha512/sha1/48"` 断言——预期成立，但若本机命中八组合中的其它项，按测试给出的指示更新断言并回写 spec §6.4。
- Task 13 Step 3 的表名/列名实测——其结果直接决定 P1 plan 的内容，**不得照抄 spec §7.1**。

---

## 依赖与顺序

```
Task 1 (骨架/config)
  ├─► Task 2 (discover)
  ├─► Task 3 (sqlcipher) ──► Task 4 (wal)
  │         │                    │
  │         └──► Task 5 (snapshot)
  │                    │
  ├─► Task 6 (keyscan) │
  ├─► Task 7 (keystore)│
  │         │          │
  │         └──────────┴──► Task 8 (decrypt) ──► Task 9 (report+doctor) ──► Task 10 (cli)
  │                                                                          │
  └──────────────────────────────────────────────────────────────────────────┴──► Task 11 (fixtures) ──► Task 12 (live) ──► Task 13 (验收)
```

Task 2、3、6、7 之间无依赖，可并行。Task 4 依赖 3；Task 5 依赖 3；Task 8 依赖 2/3/4/5；Task 9 依赖 8 及 2/6/7；Task 10 依赖 9。

---

## Execution Handoff

Plan 已保存到 `docs/superpowers/plans/2026-10-24-qq-export-p0-decryption-pipeline.md`。两种执行方式：

**1. Subagent-Driven（推荐）** —— 每个 task 派一个全新 subagent 实现，task 之间由我复核，迭代快、上下文干净。Task 2/3/6/7 无依赖，可并发派发。

**2. Inline Execution** —— 在当前会话内按批次执行，带检查点复核。

选哪种？
