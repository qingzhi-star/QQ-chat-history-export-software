# QQ 聊天记录导出工具 — 设计文档

日期：2026-10-23
状态：已确认，待实现
目标平台：Windows 10/11 x64，QQ NT 9.9.x

---

## 1. 目标与范围

### 1.1 要解决的问题

把用户**自己账号**在本机 QQ NT 客户端里的聊天记录导出成可长期保存、可离线查看的文件，支持 txt / html / pdf 三种格式任选。

### 1.2 v1 范围

**包含：**

- 单账号（本机已登录的那个 QQ 号）
- 一对一聊天（C2C）
- 群聊（Group）
- 消息类型：文本、图片、表情（QQ 面部/商城表情）、@、回复引用、文件、语音、视频、戳一戳、ARK/JSON 卡片、灰条系统提示、红包提示、通话记录、Markdown
- 老式转发消息（`40011=8`）的预览文本
- 媒体文件（图片/视频/文件）复制导出
- 本地网页界面选择会话与格式
- 导出报告（成功数、降级数、未解析项清单）

**不包含（明确划出 v1）：**

- 频道消息（`guild_msg.db` / `guild1.db`）
- 收藏（`collection.db`）
- 讨论组（`discuss_msg_fts.db` 对应的会话）
- 合并转发的在线展开（见 §2.1，硬限制）
- 语音转码为可播放格式（见 §2.2，做成可选开关，默认关闭）
- 多账号 / 远程机器
- 导出到 Word、CSV、JSON（内部有 JSON 中间模型，但不作为面向用户的格式）
- PyInstaller 单文件打包（列为 P4 可选阶段）

### 1.3 非目标

- 不做实时监听、不做增量同步、不做云端备份
- 不做任何写入 QQ 数据目录的操作
- 不做任何向腾讯服务器发起请求的操作（v1 全程离线）

---

## 2. 已确认的硬限制

这两条在方案确认阶段已向用户说明并被接受。

### 2.1 合并转发无法离线展开

QQ NT 的合并转发消息（外层 `40011=11`）在本地 `nt_msg.db` 中**只存储元数据**：

| 字段 | 内容 |
|---|---|
| `47901` | JSON 摘要（`app="com.tencent.structmsg"`；`40012=0x7` 时为 `com.tencent.multimsg` 批量转发） |
| `47902` | base64 下载令牌 |
| `47904` | uuid |

正文需要持登录态向腾讯服务器拉取 MultiMsg 才能取得。本地磁盘上**不存在**这些内容。

**v1 处理方式：** 渲染为一张卡片，展示从 `47901` JSON 里能解析出的发送者、时间、消息条数与文字摘要，并明确标注「合并转发 · 本地无缓存，未展开」。同时把 uuid 与令牌写进导出报告的「未展开清单」，为将来可能的在线模式留接口。

**对比：老式转发（`40011=8`）** 存的是 `48601` resid + `48602` XML + `48603` uuid，其中 XML 内含每条子消息的预览文本（`dataitem/sourcename` + `datadesc`）。这部分**可以**离线渲染，v1 会解析并以缩进块展示预览文字（不含其中的媒体）。

**为什么不做在线模式：** 需要复用会话令牌发协议请求，与用户「绝不能被平台检测」的底线冲突。

### 2.2 语音是 SILK 编码伪装成 .amr

实测 `nt_data\Ptt\<YYYY-MM>\Ori\<32位md5>.amr` 的文件头为：

```
02 23 21 53 49 4C 4B 5F 56 33    →  0x02 + "#!SILK_V3"
```

即 SILK v3 编码，前缀一个 `0x02` 字节，扩展名 `.amr` 是误导性的（与数据库字段 `45907=1` 表示 "amr" 一致）。浏览器 `<audio>` 无法播放 SILK，系统 ffmpeg 也不带 SILK 解码器。

**v1 处理方式：** 原样复制到 `media/`，在 html 中显示为「[语音 N″] 文件名」并附下载链接；数据库字段 `45906` 提供毫秒时长用于显示。

**可选开关 `--decode-voice`：** 尝试用 `pilk` 或 `silk-python` 解码为 wav 再转 mp3。这两个包能否在 Python 3.14 上安装属于**待验证项**（见 §12 风险 R5）；若导入失败则自动回退到 v1 行为并在报告中说明。`0x02` 前缀是否需要剥除，两种情况都要试。

---

## 3. 实测环境基线

以下均为 2026-10-23 在本机实测，非推测。

| 项 | 实测值 |
|---|---|
| 客户端 | `C:\Program Files\Tencent\QQNT\QQ.exe`，版本目录 `versions\9.9.36-53644\` |
| 数据根目录 | `D:\Users\<user>\Documents\Tencent Files\` |
| 账号目录 | `...\Tencent Files\123456789\nt_qq\` |
| 消息库 | `nt_db\nt_msg.db` = **335,545,344** 字节 |
| 库大小结构 | 335,545,344 = 81,920 × 4096 + **1024** ← 精确印证 1024 字节自定义头 |
| WAL | `nt_msg.db-wal` ≈ 1.88 MB，采样时正在被写入（QQ 运行中） |
| 联系人库 | `nt_db\profile_info.db` 16.8 MB |
| 群信息库 | `nt_db\group_info.db` 23 MB |
| 其它相关库 | `rich_media.db` 95 KB、`files_in_chat.db` 775 KB、`emoji.db` 906 KB、`misc.db` 4 MB |
| 图片 | `nt_data\Pic\` 510 MB |
| 视频 | `nt_data\Video\` 124 MB |
| 文件 | `nt_data\File\` 22 MB |
| 语音 | `nt_data\Ptt\` 2.7 MB（67 个文件） |
| 账号总占用 | 2.2 GB |
| Python | 3.14.6，pip 26.1.2 |
| PDF 引擎 | `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`，156.0.4314.42 |

**关键推论：数据目录不在默认位置。** 本机 Windows「文档」已知文件夹被重定向到了 `D:\Users\<user>\Documents`，因此 `%USERPROFILE%\Documents\Tencent Files` 不存在。数据目录发现逻辑必须以 Windows 已知文件夹 API 为准，不能硬编码 `%USERPROFILE%`（见 §5.1）。

**关键推论：WAL 不是可选项。** 采样时 1.88 MB 的最新消息尚未 checkpoint 进主库。只复制 `nt_msg.db` 会丢掉最近的消息。db + wal + shm 必须成组处理（见 §6）。

---

## 4. 技术选型

### 4.1 解密：纯 Python 实现，不依赖 sqlcipher 绑定

**决定：自己实现 SQLCipher 4 的页级解密与 WAL 恢复。**

| 方案 | 结论 |
|---|---|
| `sqlcipher3-wheels` | ❌ 最新 0.5.2.post0（2024-02-25），轮子只到 cp312，本机 Python 3.14 装不上 |
| `sqlcipher3` 源码编译 | ❌ 需要本机编译 SQLCipher + OpenSSL 工具链，对用户环境要求过高 |
| 捆绑 `sqlcipher.exe` | ❌ 引入第三方二进制，供应链与许可审查成本高 |
| **纯 Python** | ✅ 只依赖 `cryptography`（abi3 轮子，跨 3.14 通用）；能一并解决 WAL；参数与算法完全可控可测 |

**性能评估：** 81,920 页 × (一次 AES-256-CBC 解密 ~4 KB + 一次 HMAC-SHA1)。`cryptography` 底层是 OpenSSL，量级在数秒到数十秒，可接受。实现时流式写盘，不在内存中持有整个库。

### 4.2 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.14 | 本机已装；参照实现与生态均为 Python |
| 加密 | `cryptography` | abi3 轮子，AES-CBC / HMAC 均由 OpenSSL 提供 |
| Web 服务 | `fastapi` + `uvicorn` | 本地网页界面 + SSE 进度推送 |
| 模板 | `jinja2` | HTML 渲染 |
| PDF | headless Edge（系统已装） | 中文字体零配置，与 HTML 所见即所得 |
| 测试 | `pytest` | — |
| protobuf | **自写通用 wire-format 解析器** | 不引入 protoc 构建步骤；未知字段可优雅降级（见 §8.1） |
| 语音（可选） | `pilk` 或 `silk-python` | 仅在 `--decode-voice` 时导入 |

**前端不引入任何构建步骤**：原生 HTML + CSS + 少量 ES module，由 FastAPI 直接托管静态文件。导出的 HTML 也**不引用任何外部 CDN 资源**，保证离线双击可看。

### 4.3 许可证与知识产权

| 项目 | 许可 | 处理 |
|---|---|---|
| `labazhou2024/chatlog-keeper` | **MIT** | 可作为算法参照；移植其密码学与 WAL 逻辑，在 `NOTICE` 中署名 |
| `QQBackup/nt_msg_db_util` | GPL-3.0 | **不复制代码**。字段号属客观事实，可参考；proto 解析器自写 |
| `QQBackup/qq-win-db-key` | 非商用自定义协议 | **不复制代码** |
| `Mythologyli/qq-nt-db` | 非商用 | **不复制代码** |
| `star-picker/QQNT-Database-Export-Tool` | AGPL-3.0 | **不复制代码** |

本项目采用 **MIT** 许可，随附 `NOTICE` 说明 chatlog-keeper 的 MIT 参照来源，随附 `DISCLAIMER` 声明仅限本人自有账号自有数据的本地使用。

**外部法务风险提示：** 同类微信工具（`sjzar/chatlog` 9.1k★、`xaoyaoo/PyWxDump` 9.6k★、`LC044/WeChatMsg` 42k★）在 2025-10 至 2025-12 期间相继因腾讯法务函从 GitHub 下架。本工具应仅作本地私用，**不主动公开发布**。

---

## 5. 架构

### 5.1 核心设计决策：切开「拿到明文库」与「解析导出」

这是整个架构最重要的一刀。

- **前半段（core/）脆弱且不可单测**：依赖实时运行的 QQ 进程、依赖版本相关的内存特征、依赖 SQLCipher 参数探测。
- **后半段（qq/ + export/）确定且可完全单测**：输入是一个标准明文 SQLite 文件与一个 md5→路径 的媒体索引，输出是文件。

切开后，后半段（约占代码量 85%）可以用测试内构造的合成明文库跑确定性测试，前半段的脆弱性被隔离在少数几个模块里。二者之间唯一的契约是：**一个标准 SQLite 文件路径** + **一份密钥无关的媒体索引**。

### 5.2 目录结构

```
qqexport/
├── pyproject.toml
├── NOTICE                      # chatlog-keeper (MIT) 署名
├── DISCLAIMER.md               # 仅限自有账号自有数据本地使用
├── qqexport/
│   ├── cli.py                  # 入口：serve / export / key / doctor / list
│   ├── config.py               # 配置读写（%LOCALAPPDATA%\QQExport\config.json）
│   ├── report.py               # ExportReport 数据类与序列化
│   │
│   ├── core/                   # ── 前半段：脆弱、依赖实时环境 ──
│   │   ├── discover.py         # 定位 QQ 安装 / 数据目录 / uin 列表
│   │   ├── keyscan.py          # 只读内存扫描取 16 字节口令
│   │   ├── keystore.py         # DPAPI 加密持久化口令
│   │   ├── snapshot.py         # db+wal+shm 成组一致性复制
│   │   ├── sqlcipher.py        # 页级解密 oracle + 参数自动探测
│   │   ├── wal.py              # WAL 帧校验与恢复计划
│   │   └── decrypt.py          # 编排 → 输出标准明文 SQLite
│   │
│   ├── qq/                     # ── 后半段：确定性解析 ──
│   │   ├── proto.py            # 通用 protobuf wire-format 读取器
│   │   ├── msgmap.py           # 所有字段号常量（唯一的「版本漂移」集中点）
│   │   ├── tables.py           # 表名/列名发现与 SQL 构造
│   │   ├── messages.py         # 行 → Message + [Segment]
│   │   ├── contacts.py         # uid/uin ↔ 显示名
│   │   └── media.py            # nt_data 扫描 → md5 索引 → 媒体解析
│   │
│   ├── export/                 # ── 后半段：渲染 ──
│   │   ├── model.py            # Chat / Message / Segment / MediaRef（稳定契约）
│   │   ├── txt.py
│   │   ├── html.py
│   │   ├── pdf.py
│   │   └── media_copy.py       # 复制被引用的媒体到导出目录
│   │
│   └── web/
│       ├── app.py              # FastAPI 路由 + SSE
│       └── static/
│           ├── index.html
│           ├── app.js
│           └── style.css
│
├── tests/
│   ├── fixtures/               # 合成 fixture，不含任何真实聊天内容（见 §10.1）
│   ├── test_proto.py
│   ├── test_sqlcipher.py
│   ├── test_wal.py
│   ├── test_messages.py
│   ├── test_contacts.py
│   ├── test_media.py
│   ├── test_txt.py
│   ├── test_html.py
│   ├── test_discover.py
│   ├── test_snapshot.py
│   ├── test_keyscan.py
│   ├── test_e2e.py             # 合成明文库 → 三种格式全链路
│   └── test_live_smoke.py      # @pytest.mark.live，默认跳过，需实时 QQ
│
└── tools/
    └── capture_fixtures.py     # 一次性在本机采集页级 fixture（仅 schema，不含消息）
```

### 5.3 数据流

```
discover ──► 数据目录 + uin
   │
   ├─► keyscan ──► 16 字节口令 ──► keystore（DPAPI 缓存，下次免扫）
   │
   ├─► snapshot ──► 工作副本 nt_msg.db / -wal / -shm / profile_info.db / group_info.db / rich_media.db
   │
   ├─► decrypt ──► 明文 SQLite（标准格式，可被 stdlib sqlite3 打开）
   │                  └─► ExportReport.corrupt_pages
   │
   ├─► media.scan ──► {md5_hex: [候选路径]}
   │
   ├─► messages.read ──► [Chat] → [Message] → [Segment]
   │        ├─ contacts.resolve（显示名）
   │        └─ media.resolve（MediaRef）
   │
   └─► render ──► export/<uin>/<chat_id>/
                    ├── index.html
                    ├── chat_<id>.html   +  media/
                    ├── chat_<id>.txt
                    ├── chat_<id>.pdf
                    ├── report.json
                    └── report.txt
```

---

## 6. 前半段规格（core/）

### 6.1 `discover.py` — 数据目录发现

**必须按以下顺序尝试，取第一个命中者：**

1. `--data-dir` 命令行覆盖 / 配置文件中的持久化覆盖
2. **`SHGetKnownFolderPath(FOLDERID_Documents)`**（ctypes 调 `shell32`）→ `<Docs>\Tencent Files`
   —— 这是唯一能正确处理「文档」被重定向到其他盘的方法。**本机即属此情况**（`D:\Users\<user>\Documents`）
3. 注册表 `HKCU\...\Shell Folders\Personal` 与 `User Shell Folders\Personal`（展开 `%USERPROFILE%`）
4. `%USERPROFILE%\Documents\Tencent Files`
5. 兜底扫描：对每个可用盘符查找 `*\Tencent Files\*\nt_qq\nt_db\nt_msg.db`（限深度，跳过 `System Volume Information` 等无权限目录，捕获 `OSError` 继续）

**uin 枚举：** 列出 `Tencent Files` 下名字全为数字、且 `<uin>\nt_qq\nt_db\nt_msg.db` 存在的子目录。

**QQ 安装位置发现：** `C:\Program Files\Tencent\QQNT\versions\*\` 取最高版本；用于 `doctor` 报告与版本漂移诊断，不参与解密。

**接口：**

```python
@dataclass(frozen=True)
class QQAccount:
    uin: str
    data_dir: Path          # ...\Tencent Files\<uin>\nt_qq
    db_dir: Path            # <data_dir>\nt_db
    media_dir: Path         # <data_dir>\nt_data

def discover_accounts(explicit: Path | None = None) -> list[QQAccount]: ...
class DiscoveryError(RuntimeError): ...   # 一个都没找到时抛，消息含人工排查建议
```

### 6.2 `keyscan.py` — 被动内存扫描

**只读。** 仅使用以下 Win32 API，不注入 DLL、不附加调试器、不写 QQ 任何文件：

```
OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
VirtualQueryEx        # 遍历内存区域
ReadProcessMemory     # 读取候选区域
CloseHandle
```

**算法：**

1. 定位 `QQ.exe` 进程 PID（`CreateToolhelp32Snapshot` / `EnumProcesses`）。若有多个，全部尝试。
2. 读取真库的验证材料：`nt_msg.db` 的前 `1024 + 4096` 字节（自定义头 + 加密页 1）。只需读一次。
3. `VirtualQueryEx` 从头遍历地址空间，筛选：`State == MEM_COMMIT` 且 `Protect ∈ {0x02,0x04,0x08,0x10,0x20,0x40,0x80}` 且 `0 < RegionSize < 200 MB`。
4. `ReadProcessMemory` 整块读入，用正则找**长度恰为 16 或 32 的可打印 ASCII 序列**，且后接 `0x00`、前为 `0x00` 或区域边界（NTQQ 以 C 字符串形式把口令传给 `sqlite3_key_v2`）。
5. 跨区域去重（`seen` 集合）。
6. 每个候选用 §6.4 的 `_verify` 对 page-1 HMAC 做校验，**优先试 `(sha512, sha1, 48)`**，命中即返回。
7. 墙钟超时保护（默认 120 秒），超时不挂死。

**权限失败区分：** `GetLastError() ∈ {5, 1314}` → 抛 `ProcessAccessDenied`（提示「以管理员身份重试」）；其它失败静默跳过该区域。

**兜底：** `qqexport key set` 手动写入（只支持交互式 `getpass` 或 `--from-stdin`；
位置参数会进 shell history 与进程列表，已移除），工具完全不碰 QQ 进程。
`qqexport key show` 只显示长度与 SHA-256 前 8 位指纹，**永不打印口令本体**。

**接口：**

```python
def scan_key(pids: list[int], verify_bytes: bytes, timeout_s: int = 120) -> bytes | None: ...
class ProcessAccessDenied(RuntimeError): ...
```

### 6.3 `keystore.py` — 口令持久化

- 存储位置：`%LOCALAPPDATA%\QQExport\keys.json`
- 保护：Windows DPAPI `CryptProtectData`，标志 `CRYPTPROTECT_UI_FORBIDDEN`，CurrentUser 作用域 → 同机其他用户无法解出
- 索引键：`uin` + 口令 SHA-256 指纹前 8 位
- `--no-cache-key` 关闭持久化；`qqexport key clear` 删除
- **口令本体永不写入日志、异常消息、导出文件、报告、Web API 响应**

### 6.4 `sqlcipher.py` — 页级解密

**已核实的算法（源自 chatlog-keeper MIT 实现，逐行核对）：**

```
常量：
  WRAPPER_SZ = 1024        # QQ NT 自定义头，仅主库有，wal/shm 没有
  PAGE_SZ    = 4096
  SALT_SZ    = 16
  IV_SZ      = 16
  KEY_SZ     = 32
  KDF_ITER   = 4000        # SQLCipher 默认是 256000，QQ 改成了 4000
  FAST_ITER  = 2

密钥派生（salt = 剥头后 page1 的前 16 字节）：
  aes_key = PBKDF2_HMAC_SHA512(passphrase_ascii, salt,        4000, dklen=32)
  mac_key = PBKDF2_HMAC_SHA512(aes_key,          salt ^ 0x3A,    2, dklen=32)

页布局（reserve = 48）：
  密文      = page[0            : PAGE_SZ - reserve]          # 4048 字节
  IV        = page[PAGE_SZ-48   : PAGE_SZ - 48 + 16]
  HMAC 标签 = page[PAGE_SZ-48+16: PAGE_SZ - 48 + 16 + 20]     # SHA1，20 字节
  填充      = 剩余 12 字节

页认证：
  expected = HMAC_SHA1(mac_key, 密文 + IV + struct.pack("<I", page_no))
  合法 ⟺ expected == 存储标签

页解密（page_no ≥ 2）：
  plaintext = AES_256_CBC_decrypt(aes_key, IV, 密文) + b"\x00" * reserve
  # 输出页长仍为 4096：4048 字节明文 + 48 字节零填充

第 1 页特殊处理（标准 SQLCipher 行为）：
  磁盘前 16 字节是明文 salt，不参与解密
  body      = page[16 : PAGE_SZ - reserve]
  plaintext = b"SQLite format 3\x00" + AES_256_CBC_decrypt(aes_key, IV, body)
              + b"\x00" * reserve
```

**reserve 区填零而非照抄密文。** 解密后页尾那 48 字节写 `b"\x00" * reserve`，不把密文的 IV+HMAC 残留带进明文库。SQLite 按页头第 20 字节的声明完全跳过保留区，填零与照抄对它等价；填零额外保证明文产物中不含任何密文片段。参照实现 chatlog-keeper 选择照抄，那是它的自由而非正确性要求。

**页 1 第 20 字节（SQLite reserved bytes）必须保留解密出的原值，不得改写成密文尾 reserve（R34）。** 真机实测（QQ 9.9.36，2026-10-28）：密文 HMAC 在 reserve=48 下通过，但解密出的 page 1 第 20 字节是 **80**；保留 80 时 `PRAGMA quick_check == ok`、`c2c_msg_table`/`group_msg_table` 可查询，改成 48 则整库报 «Fragmentation of 32 bytes reported as 0»。两者不是同一个概念：reserve 是密文尾（IV16+SHA1 20+padding12），第 20 字节是 SQLite 的每页保留字节数（80 = 4016 可用 + 80 保留）。WAL 回放写页 1 时同样保留原值。

**参数自动探测。** 不硬编码单一组合，按下表顺序试算 page-1 HMAC，首个通过者胜出，随后再确认解密结果以 `b"SQLite format 3\x00"` 开头：

| 优先级 | KDF | 页 HMAC | reserve | 几何上是否可能 |
|---|---|---|---|---|
| 1 | sha512 | sha1 | 48 | ✅ 48 ≥ 16+20 |
| 2 | sha512 | sha1 | 64 | ✅ |
| 3 | sha512 | sha1 | 80 | ✅ |
| 4 | sha1 | sha1 | 48 | ✅ |
| 5 | sha512 | sha512 | 64 | ❌ 64 < 16+64，探测时跳过 |
| 6 | sha512 | sha512 | 80 | ✅ 80 ≥ 16+64 |
| 7 | sha1 | sha1 | 64 | ✅ |
| 8 | sha1 | sha1 | 80 | ✅ |

八项全部保留以忠实反映参照实现的探测表，但第 5 项由 `combo_is_possible(kdf, mac, reserve)`（即 `reserve >= IV_SZ + digest_len(mac)`）判为不可能并跳过——reserve 至少要容得下 IV 加一个完整摘要。

预期本机 9.9.36 命中第 1 项。探测结果记入报告，便于日后诊断版本漂移。

> 注意区分：**KDF 用 SHA-512，页认证 HMAC 用 SHA-1**，二者不同。网上大量资料把两者混为一谈，或与微信 4.0（真的用 HMAC-SHA512 / reserve 80 / 256000 迭代）搞混。

**特殊页 262144（wrapped lock page）。** SQLite 的 pending byte 在 `0x40000000`；1024 字节自定义头把它推移到 `0x3FFFFC00`，对应页号：

```
_STRIPPED_PENDING_BYTE = 0x40000000 - 1024 = 0x3FFFFC00
_LOCK_PAGE_NO          = 0x3FFFFC00 // 4096 + 1 = 262144
```

该页可能全零，**不参与 HMAC 认证**，解密时原样透传并置 `shifted_pending_byte` 标志。335 MB 的库共 81,920 页，尚未触及该页号；但更大的库会，必须实现。

**接口：**

```python
@dataclass(frozen=True)
class CipherParams:
    kdf: str            # "sha512" | "sha1"
    hmac: str           # "sha1" | "sha512"
    reserve: int
    aes_key: bytes
    mac_key: bytes

def detect_params(passphrase: bytes, header_and_page1: bytes) -> CipherParams | None: ...
def decrypt_page(params: CipherParams, page: bytes, page_no: int) -> bytes | None:
    """返回明网页；HMAC 校验失败返回 None（调用方计入 corrupt_pages 并跳过）。"""
```

### 6.5 `wal.py` — WAL 恢复

`-wal` 与 `-shm` **没有** 1024 字节自定义头，按标准 SQLite 格式处理；但其中每个帧的页数据是**与主库页完全相同的加密形式**，用同一套 `aes_key`/`mac_key`（由主库 page-1 salt 派生）解密，`page_no` 取帧头声明的页号。

**WAL 头（32 字节，明文）：**

| 偏移 | 内容 |
|---|---|
| 0 | magic：`0x377F0682`（小端校验和）/ `0x377F0683`（大端） |
| 4 | 格式版本 3007000 |
| 8 | 页大小（编码值 1 表示 65536） |
| 12 | checkpoint 序号 |
| 16 | salt-1 / salt-2（各 4 字节） |
| 24 | 头校验和（8 字节） |

WAL 的 salt **与 page-1 的 salt 无关，不需要额外密钥派生**，只用于帧认证：帧头 `[8:16]` 必须等于 WAL salt。

**帧结构：** 24 字节帧头 + `page_size` 字节加密页数据。

| 帧头偏移 | 内容 |
|---|---|
| 0 | 页号 |
| 4 | 提交后数据库大小（**非 0 即为 commit 帧**） |
| 8 | salt-1 / salt-2 |
| 16 | 累计校验和 s0 / s1 |

**校验和：** 对 `帧头[:8] + 加密页数据` 按 8 字节对齐做累计 s0/s1 运算，字节序由 magic 决定；不匹配即视为有效帧序列的终点。

**恢复计划：**

1. 校验 WAL 头（magic / 版本 / 页大小 == 4096 / 头校验和）
2. 若存在 `-shm`，校验其两份 48 字节头是否**逐字节相同**，再校验字节序标志（偏移 13）、头校验和（偏移 40）与 `aSalt`（偏移 32）是否与 WAL 头一致，读取 `mxFrame`（偏移 16）与 `nPage`（偏移 20）与扫描结果交叉验证（`mxFrame >= frame_count`，`nPage == 0` 或 `nPage == commit_size`）；任一项不符则退回「仅扫描 WAL」模式并置 `shm_validated=False`。
   **不校验 `iVersion`（偏移 0）与页大小编码（偏移 14）**：`-shm` 在本设计中仅用于交叉验证、任何缺陷都 fail-safe 退化，而这两个偏移的确切语义只有一处二手依据，不足以写死断言。
3. 顺序扫描帧，校验帧头 salt 与累计校验和，遇首个无效帧停止
4. **只应用到最后一个 commit 帧**（`db_size_after_commit != 0`）
5. 输出截断到 `commit_size × page_size`
6. **两遍处理**：第一遍认证所有已提交帧，第二遍才写出；若期间 WAL 文件签名发生变化则中止
7. **原子性边界是 commit 帧，不是帧。** 若第二遍因页 HMAC 失败或页号非法而提前停止，
   则「可应用范围内最后一个 commit 帧」也随之后移——第二遍的回放上界与截断长度
   必须取自**该范围内实际达成的最后一个 commit**，而不是原计划的最后 commit。
   若范围内一个 commit 帧都没有，则**一帧都不写**，主库保持原样。
   绝不允许把属于未提交事务的帧留在产物里：那会让页面引用从未写入的结构，
   产物结构性损坏、`quick_check` 失败，把「本可得到稍旧但完好的库」变成「什么都拿不到」。
8. **页号非法分两类。** `pgno == 0` 是真损坏（SQLite 的 `walDecodeFrame` 亦拒绝，
   且会算出负偏移）→ 硬中止并计入 `corrupt_frames`。
   `pgno > 最终 commit_size` 是**良性帧**（SQLite 照写后再截断掉；库收缩型 commit
   与单事务内先追加后释放都会产生它）→ **跳过该帧继续**，不计损坏，仅在 `notes` 记录。
   把后者当损坏会丢弃整个合法 WAL 的贡献。（防止读到正在被写的文件）

**接口：**

```python
def inspect_wal(wal_path: Path, shm_path: Path | None,
                expected_page_size: int = 4096) -> WalPlan: ...
def apply_wal(params: CipherParams, db_out: Path, plan: WalPlan) -> WalApplyResult: ...
```

### 6.6 `snapshot.py` — 尽力获取一致快照

QQ 采样时正在运行并写库，直接复制可能得到撕裂快照。

**措辞约束：** 本模块一律称「**尽力**获取一致快照」。三次重试本身**不构成**
一致性证据；只有下述 identity 比对与 WAL 交叉校验才是证据。面向用户的文案、
字段名与 docstring 都不得声称「已证明一致性」。

**一致性不来自「page 1 能解密」。** 主库首页有效**不能**证明
`db` / `wal` / `shm` 三件套来自同一时点。因此校验分四层：

```
FileIdentity = (exists, size, mtime_ns, volume_serial + file_index)

1. 复制前记录 db/wal/shm 三者的 FileIdentity
2. 复制三件套
3. 复制后再次记录源三者的 FileIdentity
4. 前后不一致 → 判定「复制期间源发生变化」→ 清理本轮副本 → 重试
5. 一致 → 再做内容级校验：
     主库：size % 4096 == 1024、page-1 salt 非全零、（有口令时）detect_params 命中
     WAL ：inspect_wal(副本) 必须 valid 且 page_size == 4096；
           reason == "wal_absent" 视为通过（无 WAL 是合法状态）
```

**`consistency` 三态：**

| 值 | 条件 |
|---|---|
| `proven` | 拿到 shm 锁 **且** 前后 identity 一致 **且** WAL 校验通过 |
| `best_effort` | 未拿到锁，但前后 identity 一致且 WAL 校验通过 |
| `unverified` | identity 一致但 WAL 校验失败（仍返回，由上层决定） |

**其余策略：**

1. 用 `msvcrt.locking(fd, LK_NBLCK, 3)` 对 `-shm` 偏移 **120** 处的 3 字节尝试**非阻塞**字节范围锁（SQLite 的 WAL-index / writer / checkpoint 锁区），成功则复制期间写入方被静默。**该偏移与长度必须先在目标 Windows 上实证**，不得凭文档假定；实测不符时停下来报告而不是猜。加锁与解锁都不得改变文件内容。
2. 拿不到锁不报错，`consistency` 降为 `best_effort`，靠上述校验兜底
3. 成组复制 `nt_msg.db` + `-wal` + `-shm`；以及 `profile_info.db`、`group_info.db`、`rich_media.db`（各自带 wal/shm）
4. 目标目录中若存在上一轮的旧副本，**复制前必须先清除同名文件**，否则会把旧 WAL 与新 DB 混成一组
5. 每次重试都要清理不完整副本，不得留残留
6. 3 次都失败 → 中止并提示用户「请完全退出 QQ 后重试」（干净退出会触发 checkpoint）
7. **永不修改 QQ 数据目录下的文件**；唯一以 `"r+b"` 打开的是 `-shm`，仅为取锁、不写任何字节。工作副本放在 `%LOCALAPPDATA%\QQExport\work\<uin>\`，用完可清

### 6.7 `decrypt.py` — 编排

```
输入：QQAccount + 口令
输出：Path（标准明文 SQLite）+ DecryptResult

步骤：
  1. snapshot 成组复制到工作目录
  2. 剥掉主库前 1024 字节（流式分块，不在内存持有整库；wal/shm 原样保留在旁边）
  3. detect_params 自动探测密码学参数
  4. inspect_wal 生成恢复计划
  5. 逐页解密主库 → 写临时明文文件；页 262144 特殊透传；HMAC 失败页计入
     corrupt_pages 后**写零页占位**（跳过写入会让后续页偏移错位、整库不可读）
  6. apply_wal 两遍处理，回放到最后 commit，截断
  7. stdlib sqlite3 以 `mode=ro` 打开做完整性自检：
       PRAGMA quick_check（不校验全部索引，369 MB 库上才可接受）
       SELECT name FROM sqlite_master WHERE type='table'
  8. 原子替换到最终路径
```

**corrupt_pages 非致命。** 调研显示较大的 `group_msg_table` 库常有损坏页，跳过并在报告中计数即可，不中止导出。

**但「非致命」不等于「成功」。** `DecryptResult` 必须携带三态 `status`，
且任一降级条件命中就压低状态——**禁止只追加 warning 却照常返回成功**：

| 条件 | status |
|---|---|
| `corrupt_pages > 0` | 至多 `lossy` |
| WAL 存在但 `plan.valid is False` | 至多 `lossy`，notes 写明「主库回退版」 |
| `wal_signature_changed` 或 `stopped_early` 或 `wal_corrupt_frames > 0` | 至多 `lossy` |
| `integrity_check != "ok"` | `failed` |
| 产物页 1 缺 SQLite 魔数 | `failed` |
| 源库本就无 `-wal`（`wal_reason == "wal_absent"`） | 不降级，属合法状态 |
| 以上皆无 | `complete` |

`status == "failed"` 时仍返回结果对象供诊断，但必须删除产物文件。
doctor 与 CLI 必须如实呈现 status，`lossy` 不得显示为普通成功。

**产物生命周期：** partial 文件名含 pid 与随机后缀（避免并发运行互相覆盖），
写在目标同目录以保证同卷、满足 `os.replace` 的原子性。已存在的最终产物在
成功前保持不变，成功后原子替换。进程被强杀后，下次启动按唯一前缀识别遗留
partial，只清理自己的。故障注入测试须证明：写入途中抛异常时旧产物字节未变、
无 partial 残留、源数据 identity 未变。

---

## 7. 后半段规格（qq/）

### 7.1 `msgmap.py` — 字段号唯一真源

**所有魔法数字集中在这一个文件。** QQ 更新导致字段漂移时，只需改这里。禁止在其他模块内联字段号。

**列 ID（`c2c_msg_table` / `group_msg_table` 的列名就是这些数字字符串）：**

| 列 | 含义 |
|---|---|
| `40001` | msg_id |
| `40011` | 外层消息类型 |
| `40012` | 子类型（群，位掩码） |
| `40013` | 方向（1 = 自己发出） |
| `40020` | 发送者 NT UID（`u_...` 字符串） |
| `40021` | 对端 UID / 群号 |
| `40030` | 对端 QQ 号 |
| `40033` | 发送者 QQ 号 |
| `40050` | Unix 时间戳（秒） |
| `40800` | **消息体 protobuf blob** |
| `49154` | proto_ver |
| `49155` | 内部时间戳 |

群表另有 `40600` / `40605` / `40801` / `40900`，语义部分未知 → 读入但仅存进原始调试转储，不参与渲染。

**外层类型 `40011`：**

| 值 | 含义 |
|---|---|
| 2 | 标准消息 |
| 3 | 文件 |
| 5 | 回复 / 名片 |
| 6 | 名片（C2C）/ 语音（群） |
| 7 | 短视频 |
| 8 | **老式转发**（XML，可离线渲染预览） |
| 9 | 戳一戳 / 回应（`40012=0x21`；`47401` 群、`47419` action=47、`47413` 文本） |
| 10 | 红包 |
| 11 | **NT 合并转发**（`40012=0` → `47901` JSON `app="com.tencent.structmsg"`；`0x7` → `com.tencent.multimsg` 批量转发） |
| 17 | 状态 / 商城表情 |
| 19 | 通话（`48151` 类型、`48152` 毫秒） |

**内容类型 `45002`（`40800` 内每个 MsgContent 的判别字段）：**

| 值 | 类型 |
|---|---|
| 1 | 文本 |
| 2 | 图片 |
| 3 | 文件 |
| 4 | 语音（Ptt） |
| 5 | 视频 |
| 6 | QQ 表情 |
| 7 | 回复引用块 |
| 8 | 灰条提示 |
| 9 | 红包 |
| 10 | ARK / JSON 卡片（载荷在 `47901`） |
| 11 | 商城表情 |
| 14 | Markdown |
| 16 | 老式转发 XML |
| 17 | Markdown 按钮 |
| 21 | 直播记录 |

**媒体子类型 `45003`：** 1 图片 / 2 语音 / 7 视频 / 11 文件

**元素字段号：**

| 字段 | 含义 |
|---|---|
| `45101` | 文本内容（UTF-8） |
| `45402` | 图片文件名字符串 |
| `45406` | **16 字节原始 MD5** ← 主键，hex 后即磁盘文件名 |
| `45411` / `45412` | 图片宽 / 高 |
| `45802`–`45804` | CDN URL（v1 不使用，全程离线） |
| `45812` | 本地绝对路径（兜底用） |
| `45550` | =4 时 `45906` 为语音时长（毫秒） |
| `45906` | 语音时长 ms |
| `45907` | 语音格式（1 = amr，实为 SILK） |
| `45405` | 文件大小 |
| `45419` | 文件扩展名 |
| `45503` | 文件 uuid |
| `47710` | **被引用消息的完整 MsgContent，内联递归嵌套** |
| `47402` | 群消息 seq |
| `47404` | 被引用消息原始时间戳 |
| `47413` | 引用摘要文本 |
| `47703` | 被引用消息发送者 uid |
| `47901` | NT 转发 JSON 元数据 / ARK 卡片载荷 |
| `47902` | NT 转发 base64 下载令牌 |
| `47904` | NT 转发 uuid |
| `48601` | 老式转发 resid |
| `48602` | 老式转发 XML |
| `48603` | 老式转发 uuid |

**@ 提及：无结构化字段。** 调研在所有参照实现中均未找到独立的 at 标签；@ 以字面文本形式出现在 `45101` 中。v1 不做特殊渲染，按文本处理（`group_at_me_msg` 表可作为将来「只看@我」功能的索引）。

### 7.2 `proto.py` — 通用 wire-format 读取器

**不使用 protoc，不生成代码。** 手写一个通用读取器：

```python
def parse(buf: bytes) -> dict[int, list[Value]]
# Value = int | bytes | float | dict（递归嵌套的 length-delimited 子消息）
```

- 支持 wire type 0（varint）、1（64 位定长）、2（长度分隔）、5（32 位定长）
- wire type 3/4（已废弃的 group）→ 记为未知并停止该层解析，不抛异常
- **任何字段都不做语义假设**；语义解释全部在 `messages.py`
- 截断 / 越界 / 非法 varint → 返回已成功解析的前缀 + 置 `truncated` 标志，**永不抛异常**
- 未知字段号原样保留在返回的 dict 中，供 `unknown_fields` 统计

**为什么这样设计：** QQ 一旦改字段，生成式 protoc 代码会整体崩；通用读取器只会让某一类 segment 降级为「未知」，其余消息照常导出。这是抗版本漂移的关键。

### 7.3 `tables.py` — 表名与列名发现

**不硬编码表名。** 打开明文库后：

```sql
SELECT name FROM sqlite_master WHERE type='table'
```

按模式匹配 `c2c_msg_table` / `group_msg_table`（容忍后缀变化）。列名是数字字符串，SQL 中必须加双引号：

```sql
SELECT "40001","40011","40012","40013","40020","40021","40030","40033","40050","40800"
FROM "c2c_msg_table" ORDER BY "40050" ASC
```

用 `PRAGMA table_info` 校验列是否存在，缺失的列以 `None` 填充而非报错。

### 7.4 `messages.py` — 消息解析

```
一行 → Message
  Message.segments: list[Segment]   # 保持原始顺序（观测到单条消息最多 121 个 segment）

Segment.kind ∈ {
  text, image, face, market_face, file, voice, video,
  reply, forward_nt, forward_legacy, ark, markdown,
  poke, gray_tip, red_bag, call, live, unknown
}
```

**判别顺序：** 外层 `40011` → `40012` 位掩码 → 内层 `45002` → `45003`。四级联合判别，任一级缺失都能降级。

**回复引用（`45002=7`）：** `47710` 是**内联递归嵌套的完整 MsgContent**，直接递归调用本模块解析 → 得到被引用消息的 segments，渲染为嵌套块。

**老式转发（`40011=8`）：** 解析 `48602` XML，提取 `dataitem` 下的 `sourcename` 与 `datadesc`，逐条渲染为预览行；总长上限 500 字符后截断并标注。

**NT 合并转发（`40011=11`）：** 按 §2.1 渲染卡片 + 记入未展开清单。

**降级原则（贯穿全模块）：单条消息解析失败绝不中止整次导出。** 捕获异常 → 生成 `kind=unknown` 的 segment（保留原始字段号与 hex 片段）→ 计入报告 → 继续下一条。

### 7.5 `contacts.py` — 显示名解析

数据源：`profile_info.db`（好友）、`group_info.db`（群资料 + 成员 + 群名片）。

**显示名优先级：**

```
群内：群名片 > 好友备注名 > 昵称 > "QQ号<uin>" > "未知用户<uid前8位>"
私聊：好友备注名 > 昵称 > "QQ号<uin>" > "未知用户<uid前8位>"
自己：固定 "我"（可配置）
```

**ID 双轨：** 消息表里同时存在 NT UID（`u_...` 字符串）与 QQ 号（数字）。两个方向都要建索引，任一命中即可。

**群成员快照：** 导出时把当次解析到的成员表写进导出目录的 `contacts.json`，使得导出文件在成员后续改名/退群后仍能还原当时的名字。

### 7.6 `media.py` — 媒体解析

**一次性建索引，不要每条消息扫盘。**

```
遍历 nt_data\{Pic,Video,File,Ptt}\**
  → {md5_hex_lowercase: [Path, ...]}
```

实测目录形态：

```
nt_data\Pic\<YYYY-MM>\Ori\<32位小写hex md5>.jpg
nt_data\Pic\<YYYY-MM>\Thumb\<md5>_0.jpg  /  <md5>_720.jpg
nt_data\Video\<YYYY-MM>\Ori\<md5>.mp4
nt_data\Ptt\<YYYY-MM>\Ori\<md5>.amr
nt_data\File\...
```

**解析优先级：**

1. `45406`（16 字节原始 MD5）→ hex 小写 → 索引命中；`Ori` 优先于 `Thumb`
2. `45812`（本地绝对路径）→ 文件存在则直接用
3. `45402`（文件名字符串）→ 在全索引中按文件名后缀匹配
4. 全部失败 → `MediaRef.missing = True`，渲染为「[图片 · 本地文件缺失]」并计入报告

**v1 不使用 `45802`–`45804` 的 CDN URL**（全程离线原则）。

---

## 8. 导出格式规格（export/）

### 8.1 中间模型（`model.py`）

三种格式**共享同一个模型**，txt / html / pdf 都只是它的不同投影。HTML 是唯一真源，pdf 由 html 派生。

```python
@dataclass(frozen=True)
class MediaRef:
    kind: str                    # image | video | voice | file
    source: Path | None
    missing: bool
    width: int | None
    height: int | None
    size: int | None
    duration_ms: int | None
    filename: str | None
    rel_path: str | None         # 复制到导出目录后的相对路径

@dataclass(frozen=True)
class Segment:
    kind: str
    text: str | None = None
    media: MediaRef | None = None
    quoted: "Message | None" = None      # 回复引用（递归）
    forward_previews: list[tuple[str,str]] | None = None   # 老式转发 (名称, 内容)
    forward_meta: dict | None = None                       # NT 合并转发元数据
    unknown_field: int | None = None
    raw_hex: str | None = None

@dataclass(frozen=True)
class Message:
    msg_id: int
    ts: int                      # Unix 秒
    sender_uid: str | None
    sender_uin: str | None
    sender_name: str
    is_self: bool
    segments: list[Segment]
    outer_type: int
    sub_type: int

@dataclass(frozen=True)
class Chat:
    kind: str                    # "c2c" | "group"
    peer_id: str                 # 对端 uid 或群号
    title: str
    subtitle: str | None         # 群号 / QQ号
    messages: list[Message]      # 由流式生成器填充，不要求全量驻留
```

### 8.2 txt

**用户指定格式：`名称：内容`。**

```
============================================================
群聊：某某交流群  (群号 123456789)
导出账号：123456789
导出时间：2026-10-23 23:40:12
时间范围：2024-03-01 ~ 2026-10-23
消息条数：12,480
============================================================


---------- 2026-10-20 星期一 ----------

[14:32:07] 张三：今天开会吗
[14:32:41] 李四：开的，三点
[14:33:02] 张三：[图片] media\img_a1b2c3d4.jpg
[14:33:15] 王五：[语音 12″] media\ptt_e5f6a7b8.amr
[14:35:20] 李四：[回复 张三：今天开会吗] 嗯，会议室 A
[14:36:01] 张三：[文件] 会议材料.pdf (2.4 MB) media\file_9c8d7e6f.pdf
[14:40:12] 系统：张三 邀请 赵六 加入了群聊
[14:41:00] 张三：[合并转发 · 本地无缓存，未展开] 共 8 条消息，来自 李四
```

**规则：**

- 每条消息一行起，多 segment 时非文本部分用 `[类型]` 占位，媒体附相对路径
- 回复引用压成单行 `[回复 原发送者：原文摘要] 正文`
- 老式转发按缩进块展开预览行
- 日期变化时插入分隔行（含星期）
- 时间戳只显示 `HH:MM:SS`（日期已在分隔行）
- **编码：默认 UTF-8 with BOM**（中文 Windows 记事本兼容性最好）；`--encoding gbk` 可选，GBK 无法编码的字符替换为 `?` 并在报告计数

### 8.3 html

**目录形态：**

```
export/<uin>/
├── index.html                    # 会话索引
└── <chat_slug>/
    ├── chat.html
    ├── media/                    # 仅含被实际引用的媒体
    │   ├── img_<md5前8>.jpg
    │   └── ...
    └── contacts.json             # 本次导出的成员名快照
```

**`index.html`：** 列出所有已导出会话，含类型图标、标题、消息数、时间范围、文件大小、指向 `chat.html` / `.txt` / `.pdf` 的链接，以及报告摘要。

**`chat.html` 要求：**

- 顶部固定信息条：会话标题、消息数、时间范围、导出时间、格式切换链接
- 气泡布局：**自己的消息右对齐**（蓝底白字），他人左对齐（白底深字），头像（`nt_data\avatar` 可解析时内嵌，否则用昵称首字生成的纯色圆形）、昵称、时间戳
- 日期分隔条（sticky）
- 图片：`<img loading="lazy" src="media/...">`，`max-width:100%`，点击放大（纯 CSS/JS lightbox，无外部依赖）
- 视频：`<video controls preload="metadata">`
- 语音：显示「▶ 语音 N″」+ `<a download>` 链接（SILK 浏览器不可播，见 §2.2）
- 文件：图标 + 文件名 + 大小 + 下载链接
- 回复引用：左侧竖线嵌套块，显示原发送者与原文
- 老式转发：折叠卡片，展开显示预览行
- NT 合并转发：卡片 + 醒目「未展开」标注 + 摘要元数据
- 灰条系统提示：居中灰色小字
- 戳一戳 / 红包 / 通话：居中提示样式
- 未解析消息：黄色警示块，显示原始字段号，可折叠查看 hex（默认折叠）
- **零外部资源**：CSS/JS 全部内联，不引用任何 CDN，`file://` 双击即可离线查看

**大会话分片。** 单个 `chat.html` 超过 **20,000 条消息**时按年份切分为 `chat-2024.html` / `chat-2025.html` / …，并在会话目录内生成 `index.html` 做分片导航。**流式写出**（Jinja2 stream / 逐块 flush），绝不在内存里构造整棵 DOM —— 335 MB 的库全量展开会有几十万条消息。

### 8.4 pdf

**PDF 由 HTML 派生，因此勾选 pdf 时必然先生成 html 与 media/。** 这两份中间产物**保留不删**——HTML 是唯一真源（§8.1），留着既能复查排版，也是 PDF 失败时的可用兜底（§11）。`--format pdf` 等价于 `--format html,pdf`，`list`/报告/界面上均如实体现。

**引擎：系统已装的 headless Edge。**

```
msedge.exe --headless=new --disable-gpu --no-pdf-header-footer \
           --print-to-pdf="<out>.pdf" "file:///<绝对路径>/chat.html"
```

Edge 路径发现顺序：`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` → `C:\Program Files\Microsoft\Edge\Application\msedge.exe` → `PATH` 中的 `msedge`。找不到时报错并提示改用 html。

**为什么用 Edge 而不是 WeasyPrint：** Chromium 直接用系统中文字体，零字体配置；WeasyPrint 需要显式安装并嵌入 CJK 字体（Noto Sans CJK SC ≈16 MB），且长文档分页更慢。本机已装 Edge 156，无额外依赖。

**打印专用 CSS（`@media print`）：**

- `@page { size: A4; margin: 18mm 15mm }`
- 单条消息 `break-inside: avoid`
- 日期分隔条 `break-after: avoid`
- 隐藏 lightbox、格式切换链接、折叠控件；展开所有默认折叠的卡片
- 图片 `max-width: 100%` 且不跨页断裂
- 字体回退栈：`"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif`

**媒体不内嵌 base64**：Edge 以 `file://` 加载 HTML，相对路径 `media/` 可正常解析，避免体积爆炸。

**已知限制（明确记录，不在 v1 解决）：** Chromium 不支持 CSS `@page` 边距盒（`@bottom-center` 等），因此 **PDF 无页码**。要页码需改走 CDP（`Page.printToPDF` 的 `displayHeaderFooter`），列为 P4 可选增强。

**体积保护：** 消息数超过 **30,000** 条时，PDF 导出前弹确认并建议按年分片；单次 PDF 生成设 10 分钟超时，超时保留 html/txt 产物并在报告中标注 PDF 失败原因。

### 8.5 `media_copy.py`

- 只复制**被实际引用**的媒体（先解析完所有 segment 收集引用集，再统一复制）
- 目标名：`<类型前缀>_<md5前8位>.<原扩展名>`，冲突时追加序号
- 硬链接优先（`os.link`，同盘时零拷贝），失败回退到复制
- **只读源文件**，绝不移动或删除 `nt_data` 下的任何东西
- 导出前若目标目录已存在，先备份为 `<name>.bak-<时间戳>` 再覆盖

---

## 9. Web 界面与 CLI

### 9.1 CLI

```
qqexport doctor                     # 全链路自检，打印环境与各阶段可用性报告
qqexport list                       # 列出所有会话（类型/标题/消息数/时间范围）
qqexport key scan                   # 只读内存扫描取口令
qqexport key set                    # 交互式输入口令（getpass，不回显）
qqexport key set --from-stdin       # 管道/自动化场景
qqexport key show                   # 只显示长度与 SHA-256 指纹前 8 位
qqexport key clear                  # 删除缓存口令
qqexport export --chat <id> --format txt,html,pdf [--out DIR]
qqexport export --all --format html
qqexport serve [--port 8765]        # 启动本地网页界面
```

**`key set` 不接受位置参数形式的口令。** 位置参数会进入 shell history、`ps`
输出与诊断采集，属不可接受的泄漏面。只支持交互式 `getpass`（不回显）与
`--from-stdin`（读一行即弃，不落临时文件）。`getpass` 在无法关闭终端回显时
会退化为读 stdin 并发 `GetPassWarning`，此时必须中止并提示改用 `--from-stdin`，
不得在不回显失效的情况下继续读取。

`--format` 默认 `html`。`--all` 与 `--chat` 互斥，`--chat` 可重复传多个。所有子命令支持 `--data-dir` / `--uin` / `--no-cache-key` / `--decode-voice`。

**会话 ID 格式（`list` 输出、`--chat` 入参、Web API `chat_ids` 三处统一）：**

```
c2c:<对端 NT UID>          例：c2c:u_8f3a2b1c9d
group:<群号>               例：group:123456789
```

**目录 slug（§8.3 的 `<chat_slug>`）** 由会话 ID 派生：`c2c_<昵称或UID前12位>` / `group_<群号>_<群名前12字>`，其中昵称与群名经清洗——只保留中英文、数字、`-`、`_`，其余替换为 `_`，并截断到 40 字符；清洗后为空则退回纯 ID。同一批导出内 slug 冲突时追加 `-2`、`-3`。清洗规则必须与 §9.2 的路径白名单校验共用同一函数，杜绝路径穿越。

### 9.2 本地网页界面

`qqexport serve` 启动 uvicorn 监听 **127.0.0.1**（不绑定 0.0.0.0），自动打开浏览器。

**布局：**

- 左栏：会话列表，可按类型（私聊/群）筛选、按标题搜索、显示消息数与最后消息时间；支持多选与全选
- 右栏：格式勾选（txt / html / pdf）、输出目录、高级选项（是否含媒体、语音转码、分片阈值）、导出按钮
- 底部：进度条 + 实时日志（SSE 推送）+ 完成后各产物的打开链接与报告摘要

**REST / SSE：**

```
GET  /api/status                  # 环境自检结果（QQ 版本、数据目录、口令是否已缓存、Edge 是否可用）
GET  /api/chats                   # 会话列表
POST /api/export                  # {chat_ids, formats, options} → {job_id}
GET  /api/progress/{job_id}       # SSE：阶段 / 百分比 / 当前会话 / 日志行
GET  /api/report/{job_id}         # ExportReport JSON
POST /api/key/scan                # 触发内存扫描
```

**安全：** 仅监听回环；无鉴权（本机单用户场景），但所有路径参数做严格校验，导出目录必须在用户显式选择的白名单内，杜绝路径穿越。

### 9.3 `doctor` 自检报告

逐项检查并给出 ✅/⚠️/❌ 与修复建议：

1. Python 版本、`cryptography` 可导入
2. QQ 安装与版本（读 `versions\*` 目录名）
3. 数据目录发现（走了哪条路径命中的）
4. 账号枚举结果
5. `nt_msg.db` 大小与「+1024 头」结构校验（`size % 4096 == 1024`）
6. QQ.exe 是否在运行（内存扫描的前提）
7. 口令：缓存命中 / 扫描成功 / 失败
8. 参数探测命中的 (KDF, HMAC, reserve) 组合
9. 解密 + 页 1 魔数校验；**解密前先做容量预估**——所需工作空间按
   `(主库 size + wal size) × 3`（源快照 + 明文产物 + 余量）实算，与目标卷
   可用空间比较，不足则本项判 ⚠️ 并给出所需/可用的**字节数**（不展示完整路径）。
   估算必须按实际文件大小算，**不得把本文档 §3 里的 335 MB 样本值写成常数**。
   （原第 15 项「剩余空间」的 P0 部分由此吸收，不新增第 12 项检查以免全量重编号。）
10. WAL 帧数与最后 commit 位置
11. `sqlite_master` 中目标表是否存在，关键列是否齐全
12. 抽样解析 N 条消息，统计各 segment 类型与未知字段号分布
13. 媒体索引规模与抽样命中率
14. Edge 可用性（PDF 前提）
15. 输出目录可写、剩余空间是否足够（估算：媒体引用集大小 × 1.2）

---

## 10. 测试策略

### 10.1 Fixture 隐私红线

**仓库中不得包含任何真实聊天内容、真实昵称、真实 QQ 号、真实媒体文件。**

本 spec 与所有计划文档中出现的 QQ 号一律是**合成值**（如 `123456789`、`11111111`、`12345`），路径中的用户名一律写作 `<user>`。实测得到的真实 uin、真实数据目录、真实昵称**不得**写入任何入库文件——包括测试代码、fixture、doctor 输出存档与验收记录。真机命令的输出需要留档时，必须先脱敏。

| Fixture | 来源 | 隐私 |
|---|---|---|
| `page1.bin` | `tools/capture_fixtures.py` 从本机真库截取「1024 头 + 页 1」 | 页 1 只含 `sqlite_master` 与 schema，**不含消息**；仍需人工确认后再提交 |
| 合成 WAL | 测试内构造 | 无隐私 |
| protobuf 字节 | 测试内手工构造各种 segment | 无隐私 |
| 明文库 | 测试内用 stdlib `sqlite3` 现造，列名用数字字符串，内容为虚构 | 无隐私 |
| 媒体 | 测试内生成的 1×1 PNG / 极短静音 | 无隐私 |

`tools/capture_fixtures.py` 必须在输出前打印将写入的内容摘要，由用户人工确认后才落盘。

### 10.2 测试矩阵

| 测试文件 | 覆盖 | 是否需要实时 QQ |
|---|---|---|
| `test_sqlcipher.py` | 参数探测八组合择一；page-1 特殊布局；HMAC 校验失败返回 None；页 262144 透传；`size % 4096 == 1024` 断言 | 否（fixture） |
| `test_wal.py` | 头校验；大小端 magic；帧 salt 认证；累计校验和；只到最后 commit；按 commit_size 截断；shm 交叉验证与退回；两遍处理时签名变化则中止 | 否（合成） |
| `test_proto.py` | 四种 wire type；嵌套子消息；截断输入返回前缀 + truncated；非法 varint 不抛异常；未知字段保留 | 否 |
| `test_messages.py` | 每种 `45002` / `40011` 组合；回复引用递归；老式转发 XML 预览与 500 字截断；NT 转发卡片与未展开清单；单条解析抛异常时降级为 unknown 且不影响其余消息 | 否 |
| `test_contacts.py` | 显示名六级优先级；uid / uin 双轨索引；群名片覆盖备注名；全未知时兜底串 | 否 |
| `test_media.py` | md5 索引；Ori 优先于 Thumb；四级解析回退；缺失时 `missing=True`；只读不改源 | 否 |
| `test_txt.py` | `名称：内容` 格式；日期分隔；占位符；UTF-8 BOM；GBK 回退与替换计数 | 否 |
| `test_html.py` | golden file；自己/他人对齐；零外部资源（断言 HTML 中无 `http://` / `https://` / `//cdn`）；分片阈值触发；未解析消息警示块 | 否 |
| `test_discover.py` | 已知文件夹重定向场景（`SHGetKnownFolderPath` 返回非默认盘下的 `<Documents>`）；uin 枚举过滤；找不到时抛 `DiscoveryError` | 否 |
| `test_snapshot.py` | 成组复制；锁不可用时仍能复制并靠校验兜底；校验失败重试 3 次；**断言从未写入源目录** | 否 |
| `test_keyscan.py` | 候选生成（16/32 长度、0x00 边界）；验证回调；超时不挂死；错误码 5/1314 抛 `ProcessAccessDenied` | 否（注入假 handle） |
| `test_e2e.py` | 合成明文库 → 三种格式全跑通 → 断言产物存在且非空、报告计数自洽 | 否 |
| `test_live_smoke.py` | **标记 `@pytest.mark.live`，默认跳过。** 需本机实时 QQ，跑真实 discover → scan → decrypt → 抽样解析 | 是 |

### 10.3 验收标准（前置，避免返工）

实现完成的判定条件：

1. `pytest` 全绿（`live` 标记除外）
2. `qqexport doctor` 在本机 15 项全部 ✅ 或 ⚠️（⚠️ 需有明确说明）
3. `qqexport list` 能列出本机真实会话，标题为可读中文名而非 uid
4. 任选一个真实私聊与一个真实群聊，三种格式各导出一次：
   - txt：记事本打开中文无乱码，格式为 `名称：内容`
   - html：双击离线打开，气泡布局正确，图片能显示并点击放大，无任何外部网络请求（DevTools Network 面板为空）
   - pdf：中文正常渲染不断字，单条消息不跨页断裂，图片在位
5. 导出内容与 QQ 客户端内肉眼比对，**最近的消息在**（证明 WAL 恢复生效 —— 这是最容易静默失败的点）
6. 导出报告计数自洽：`总数 == 成功 + 降级 + 失败`
7. 只读性分两种条件记录，**不得混为一谈**：
   - **QQ 完全退出**（用户手动配合）：源目录逐文件比对
     `(size, mtime_ns, volume_serial + file_index)` 三元组全部不变 → 记 `PASS`
   - **QQ 运行中**：只能断言「无文件被我们删除」；出现新增或内容变化记
     `SKIP(未验证)`，因为那是 QQ 自己在写，不能归因于本工具
8. 连续两次导出结果字节一致（证明确定性）。**必须对同一份快照重复解密**——
   若两次都重新快照，QQ 在期间写库会把「源变化」误判为「解密不确定」。
   整库比较用流式 SHA-256，不得两次 `read_bytes()` 把数百 MB 同时载入内存。
9. 明文产物确实被清理：`tempfile.TemporaryDirectory()` 上下文退出后目录不存在，
   无 `.partial` 残留，`git status` 干净。**注意 pytest 的 `tmp_path` 默认保留
   最近 3 次运行的目录，不是清理保证**——凡产生明文库的测试一律用
   `TemporaryDirectory`，并在退出前关闭全部 SQLite 连接与文件句柄
   （Windows 上句柄未关会让清理抛 `PermissionError`）。
10. 汇总必须三态分列 `PASS` / `WARN` / `SKIP(未验证)`。任何一项 SKIP 都使
    总体结论为「部分验收」，**不得表述为全绿**。没有有效 WAL commit 时结论
    必须写成「本次没有可验证的 WAL 更新」，不得据此声明 WAL 恢复通过。

---

## 11. 错误处理与降级总则

**一条铁律：任何单点异常都不得中止整次导出。**

| 层级 | 异常 | 处理 |
|---|---|---|
| 页 | HMAC 校验失败 | 跳过该页，`corrupt_pages++`，继续 |
| WAL | 帧校验和断裂 | 应用到断裂前的最后 commit，`wal_truncated = True` |
| 行 | protobuf 解析抛异常 | 该消息降级为 `unknown` segment，保留字段号与 hex 片段 |
| segment | 未知 `45002` 值 | `kind=unknown`，`unknown_fields[字段号]++` |
| 媒体 | 文件缺失 | `missing=True`，渲染占位符 |
| 合并转发 | 本地无正文 | 渲染元数据卡片 + 未展开清单 |
| 语音 | 解码库缺失 | 回退原样复制 |
| 联系人 | 查不到名字 | 六级兜底串 |
| PDF | Edge 缺失 / 超时 | 保留 html + txt，报告标注 PDF 失败原因 |
| 口令 | 扫描失败 | 提示手动 `key set`，或提示以管理员身份重试 |
| 快照 | 3 次校验失败 | 中止并提示「请完全退出 QQ 后重试」 |

**`ExportReport`（`report.py`）** 随每次导出写入 `report.json` + `report.txt`：

```python
@dataclass
class ExportReport:
    started_at: str; finished_at: str; duration_s: float
    qq_version: str; uin: str
    cipher_params: tuple[str, str, int]
    db_size_bytes: int; page_count: int
    corrupt_pages: int
    wal_frames: int; wal_last_commit_frame: int; wal_truncated: bool
    chats_requested: int; chats_exported: int
    messages_total: int; messages_ok: int; messages_degraded: int; messages_failed: int
    segments_by_kind: dict[str, int]
    unknown_fields: dict[int, int]
    media_referenced: int; media_found: int; media_missing: int; media_bytes: int
    forwards_nt_unexpanded: int; forwards_legacy_rendered: int
    voice_decoded: int; voice_raw: int
    pdf_pages: int | None; pdf_error: str | None
    warnings: list[str]
```

`unknown_fields` 的分布是**版本漂移的早期预警信号** —— QQ 改字段号时它会突然飙升，据此定位需要更新 `msgmap.py` 的哪一项。

---

## 12. 风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | QQ 更新改变 `wrapper.node` 内存布局，口令扫描失效 | 无法自动取密钥 | 扫描逻辑不依赖硬编码偏移（全区域正则搜候选 + HMAC 验证），对布局变化天然鲁棒；`key set` 手动兜底 |
| R2 | QQ 更新改变消息 protobuf 字段号 | 消息解析错乱 | 字段号全部集中在 `msgmap.py`；通用 wire 读取器只降级不崩；报告 `unknown_fields` 飙升即预警；`doctor` 第 12 项抽样检查 |
| R3 | SQLCipher 参数变化（HMAC 算法 / reserve） | 解密失败 | 八组合自动探测；探测结果记入报告 |
| R4 | 335 MB 库全量导出产物过大、耗时过长 | 用户体验差、磁盘压力 | 按需选会话导出；HTML 分片；媒体硬链接优先；PDF 阈值确认 |
| R5 | `pilk` / `silk-python` 在 Python 3.14 上装不上 | 语音无法转码 | 已划为可选开关，默认关闭，导入失败自动回退 |
| R6 | 快照撕裂导致解密失败或数据缺失 | 导出不完整 | 非阻塞锁 + 复制后校验 + 重试 3 次 + 明确提示退出 QQ |
| R7 | 内存扫描触发风控 / 封号 | 账号受损 | **仅使用只读权限**（`PROCESS_VM_READ \| PROCESS_QUERY_INFORMATION`），不注入、不附加调试器、不改文件；这是用户明确选定的方案；`key set` 提供零风险路径 |
| R8 | 腾讯法务下架风险 | 项目无法公开 | 参照 chatlog / PyWxDump / WeChatMsg 在 2025-10~12 相继被下架的先例：仅作本地私用，不主动公开发布，随附 `DISCLAIMER` |
| R9 | 工具误写 QQ 数据目录导致库损坏 | 用户数据丢失 | 架构上 core/ 只对源文件 `open(..., "rb")`；`test_snapshot.py` 显式断言源目录 mtime 不变；工作副本一律放 `%LOCALAPPDATA%\QQExport\work\` |
| R10 | 缓存口令泄露 | 全部聊天记录可被解密 | DPAPI CurrentUser 保护；永不打印本体；`key clear` 可清除；`--no-cache-key` 可禁用 |

---

## 13. 实现阶段划分

| 阶段 | 交付物 | 验收 |
|---|---|---|
| **P0** 打通解密链路 | `discover` `keyscan` `keystore` `snapshot` `sqlcipher` `wal` `decrypt` `doctor` | `qqexport doctor` 前 11 项全绿；能产出可被 stdlib `sqlite3` 打开的明文 `nt_msg.db`，且**最近的消息在**（WAL 生效） |
| **P1** 打通解析 | `proto` `msgmap` `tables` `messages` `contacts` `media` `model` `report` | `qqexport list` 列出真实会话且标题可读；能 dump 出结构正确的 JSON；未知字段号统计可用 |
| **P2** 导出与界面 | `txt` `html` `media_copy` `web/` `cli export` | 三种会话各导出 txt + html，满足 §10.3 第 4、6、7 条 |
| **P3** PDF 与打磨 | `pdf`、HTML 分片、报告 UI、体积保护 | §10.3 全部 8 条通过 |
| **P4** 可选增强 | 语音转码、PDF 页码（CDP）、PyInstaller 打包 | 各自独立开关，失败自动回退，不阻塞 P0–P3 |

每个阶段独立可验收、可回滚。P0 与 P1 之间是架构切口（§5.1），两侧可并行开发但需先冻结 §8.1 的模型契约。

---

## 14. 未决事项

无。所有影响架构的问题已在方案确认阶段解决，剩余不确定性均已归入 §12 风险表并配好缓解措施，或明确划出 v1 范围（§1.2）。
