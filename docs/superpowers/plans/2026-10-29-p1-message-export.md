# P1 实现计划：消息解析与 txt / html / pdf 导出

**日期：** 2026-10-30
**状态：** 提案（待用户确认后执行）
**分支：** 直接提交 `main`（不再开长期分支）
**前置：** P0 已完成并验收（2026-10-29）；本文档只描述 P1。
**执行状态（2026-10-30）：** T1–T12 ✅；P1 完成。真机 `list` 与 c2c/group 导出
（txt/html/pdf）通过；486 本地测试 + 10 项 live 全绿。
**权威顺序：** 修正案 A（P0）> 设计规格 `2026-10-23-qq-chat-history-export-design.md`
> P0 实现计划 > 本计划。本文档与 spec 冲突时，先按 spec 修正本文档；需要偏离
spec 的地方必须在本文件里写成 `P1-D*` 决定并说明理由。

**发布门槛（用户 2026-10-29 裁定）：** 只有"导出聊天记录"功能完整可用后才打 tag
发布。P0 只是解密链路，**不能**作为发布依据。本计划的 T1–T12 全部通过、
spec §10.3 验收项全部 PASS，才允许 `git tag v*` 触发 release 工作流。

---

## 1. 范围

### 1.1 本计划包含（spec §13 的 P1–P3）

- 解析层：`proto`、`msgmap`、`tables`、`messages`、`contacts`、`media`。
- 导出层：`model`、`txt`、`html`、`pdf`、`media_copy`、`ExportReport`。
- CLI：`list`、`export`（`--chat/--all`、`--format txt,html,pdf`、`--out` 等）。
- 测试：合成明文库 + 合成 protobuf 的全链路测试；真机 live 导出验收。
- 发布门：wheel 包含新模块、CI 3.12/3.13/3.14 全绿。

### 1.2 明确不做（本计划外）

- **合并转发的在线展开**（spec §2.1：本地无正文，渲染未展开卡片）。
- **语音转码**（spec §2.2：`--decode-voice` 属 P4 可选；本计划只原样复制并显示时长）。
- 频道 / 收藏 / 讨论组（spec §1.2 的非目标；`discuss_msg_table` 只作为 P1 事实记录）。
- 多账号同时导出（CLI 仍要求显式 `--uin` 或配置指定单账号）。
- PyInstaller 打包、PDF 页码（CDP）。
- **Web UI（spec §9.2）**：放在可选的 `T-W`，不阻塞首发；理由见 §8 待确认项。

---

## 2. 真实基线（2026-10-29 只读探测，已脱敏）

这些事实来自本机测试账号的明文库与伴随库，只记录表/列/字段号/数量，不记录
uin、路径、昵称、消息内容。**实现必须以这些事实为准，不得从 spec 的预期字段
直接推导真实结构**（《项目优化与计划建议》§4.4）。

### 2.1 消息库

| 事实 | 值 |
|---|---|
| 表数量 | 32（含 `c2c_msg_table`、`group_msg_table`、`discuss_msg_table` 等） |
| 关键表行数（探测时） | c2c 86；group 991（live 时随 QQ 写入变化） |
| 两表列数 | 各 39；spec §7.1 的 `EXPECTED_COLUMNS` **全部存在**，无缺失 |
| `40800` 顶层结构 | **重复字段 40800**，每个子消息是一个元素；单条消息 1–4 个元素（文本常见 1–3） |
| 元素公共字段 | `45001`（元素类型/序号）、`45002`（内容类型判别） |
| 文本元素（45002=1） | `45101` 正文；`45102`；少数带 `45103–45111`（@/特殊文本） |
| 图片元素（45002=2） | `45402` 文件名、`45406` 16B MD5、`45411/45412` 宽高、`45405` 大小、`45802–45804` CDN（不用）、`45812` 本地路径兜底 |
| 文件元素（40011=3 或 45002=3） | `45402` 名、`45405` 大小、`45419` 扩展名、`45503` uuid |
| 表情元素（45002=6，或 40012=16） | `47601/47602`（群组里常见 `47603–47622`） |
| 回复（实测 40011=5、40012=4） | `47702/47703/47704/47713`；**未观察到 spec 说的 `47710` 嵌套**（需 T5 先探测再实现） |
| NT 合并转发（40011=11） | `47901` JSON，没有正文 |
| 戳一戳（40011=9、40012=33） | `40020` + `47402/47403/47404/47411/47416/47422/47423` |
| 状态/商城表情（40011=17） | `80810/80824/80900–80995` 一族 |
| 群消息特殊位 | `40012` 位掩码实测出现 1/2/4/8/16/17/33/129/2049/4096；实现按位解释，不假设单一枚举 |
| 尾页事实 | 真机曾出现文件 1024 页但页 1 头声明 698 页；尾部是旧密文/零页，SQLite 不读。解密必须按页 1 头的 database size 截断，否则会被误判成 326 个 HMAC 损坏页（R36） |

### 2.2 伴随库（P1 新增依赖）

`profile_info.db`、`group_info.db`、`rich_media.db` **都不是明文**：

| 文件 | 包装 | 加密 | 探测结果 |
|---|---|---|---|
| `profile_info.db` | 1024 字节自定义头 + 4096 页 | 与主库同 key/参数 | `sha512/sha1/48`，解密后是 SQLite |
| `group_info.db` | 同上 | 同上 | 同上 |
| `rich_media.db` | 同上 | 同上 | 同上 |

关键表/列（仅列名）：

- `profile_info_v6`：`1000/1001/1002/20001–20017/20042/20056/20059/20060/20067/20070/20071/20072/20075/20083/21000/24103–24111`
- `buddy_list`：`1000/1001/1002/25007`
- `profile_info_adelie`：`1000/1002/320001–320088`
- `group_list`：`60001/60004–60013/60017/60018/60020–60040/60204/60206/60216/60221/60238/60250/60252/60255/60256/60258/60259/60262/60267/60269/60277/60279–60281/60294/60295/60298/60299/60304/60310/60344/60345`
- `group_member3`：`60001/1000/1001/1002/64002–64018/64020–64035`
- `group_detail_info_ver1`、`group_avatar`、`group_bulletin`、`rich_media.file_table`（`454xx/455xx/40001/45001`）

**列语义尚未确认**（哪个数字是 uin、uid、昵称、群名片、群名）。T6 必须先做
"值形状探测"（只打印长度/是否 `u_` 前缀/是否纯数字，不打印内容），再写映射常量；
不得凭列号猜名字。

### 2.3 媒体目录（本机测试账号）

| 目录 | 数量 | 大小 | 备注 |
|---|---|---|---|
| `Pic` | 93 文件 | 23,178,611 B | `.jpg` 72 / `.png` 21 |
| `Ptt` | 1 文件 | 16,573 B | 语音（SILK） |
| `Video` | 不存在 | — | 实现必须容忍目录缺失 |
| `File` | 不存在 | — | 同上 |

---

## 3. 架构与接口

### 3.1 目录

```
qqexport/
├── core/                  # P0，已冻结；只允许新增伴随库解密，不改既有行为
│   └── companions.py      # 新增：profile/group/rich_media 的快照+解密
├── qq/
│   ├── msgmap.py          # 字段号唯一真源（新增伴随库列常量）
│   ├── proto.py           # 通用 wire 读取器
│   ├── tables.py          # 表/列发现 + 流式读取
│   ├── messages.py        # 行 → Message/Segment
│   ├── contacts.py        # 显示名解析 + contacts.json 快照
│   └── media.py           # md5 索引 + 媒体解析
├── export/
│   ├── model.py           # Chat/Message/Segment/MediaRef（冻结契约）
│   ├── session.py         # ExportSource：解密主库+伴随库、生命周期
│   ├── txt.py
│   ├── html.py
│   ├── pdf.py
│   ├── media_copy.py
│   └── report.py          # ExportReport
├── cli.py                 # 新增 list / export 子命令
└── report.py              # 保留 DoctorReport；ExportReport 放 export/report.py
```

### 3.2 关键接口（先冻结，测试围绕它写）

```python
# core/companions.py
def decrypt_companion(src_db: Path, passphrase: bytes, dst_dir: Path) -> Path: ...

# export/session.py
class ExportSource:
    @classmethod
    def open(cls, account: QQAccount, passphrase: bytes,
             work_dir: Path | None = None) -> "ExportSource": ...
    msg_db: Path; profile_db: Path | None; group_db: Path | None
    rich_media_db: Path | None; media_index: MediaIndex
    def close(self) -> None: ...          # 关闭全部 sqlite 连接并清理临时目录

# qq/tables.py
def discover(con: sqlite3.Connection) -> TableSet: ...
def iter_rows(con, table: str, columns: list[str]) -> Iterator[dict[str, Any]]: ...
def list_chats(con) -> list[ChatSummary]: ...

# qq/messages.py
def parse_message(row: dict, ctx: ParseContext) -> Message: ...
def iter_messages(source: ExportSource, chat_id: str) -> Iterator[Message]: ...

# qq/contacts.py
class Contacts:
    def display_name(self, uid: str | None, uin: str | None,
                     group_id: str | None = None) -> str: ...
    def snapshot(self) -> dict: ...

# qq/media.py
def build_index(media_dir: Path) -> MediaIndex: ...
def resolve(index: MediaIndex, element: Element) -> MediaRef: ...

# export/txt.py / html.py / pdf.py
def write_txt(chat: Chat, out: Path, opts: TxtOptions) -> None: ...
def write_html(chat: Chat, out_dir: Path, opts: HtmlOptions) -> list[Path]: ...
def write_pdf(chat_dir: Path, opts: PdfOptions) -> PdfResult: ...

# export/orchestrator.py
def export_chats(account, passphrase, chat_ids: list[str], formats: set[str],
                 out_dir: Path, opts: ExportOptions) -> ExportReport: ...
```

### 3.3 明文生命周期

- 主库与伴随库都只在 `TemporaryDirectory`（或 `%LOCALAPPDATA%\QQExport\work` 的
  唯一子目录）里解密；导出产物写到用户指定目录，**不写回 QQ 数据目录**。
- 所有 `sqlite3.Connection` 必须在 `ExportSource.close()` 里显式关闭，再删临时
  目录（Windows 上未关句柄会让清理失败）。
- `ExportSource` 支持 `with`；异常路径同样清理。测试必须断言退出后临时目录不存在。
- 报告里可以写 uin（导出目录是本地私用），但**不得**把导出目录、报告或日志提交到
  仓库；`.gitignore` 已忽略 `export/`，新增 `*.bak-*` 也要忽略。

### 3.4 流式与内存

- `iter_messages` 用 `fetchmany(1000)`，不 `fetchall()`。
- HTML 逐条写、逐块 flush，禁止在内存构造整棵 DOM。
- txt 也逐条写；PDF 依赖 HTML，不另建 DOM。
- 媒体索引一次性建 `{md5: [路径]}`，只存路径，不读文件内容。
- 目标：335 MB 库、数十万条消息时 RSS 增量 < 200 MB（T12 实测记录，不达标就改）。

### 3.5 确定性

- 导出时间通过 `ExportOptions.now` 注入；CLI 默认 `datetime.now()`，测试/幂等
  复现用固定时钟。
- 同一份快照 + 同一时钟 + 同一选项 → 三种格式字节一致（spec §10.3 #8）。
- 媒体复制顺序、contacts.json 键顺序、报告字段顺序全部排序固定。

---

## 4. 关键设计决定

- **P1-D1 分层：** `ExportSource`（解密与生命周期）→ `qq/*`（纯解析）→ `export/*`
  （渲染）。解析层只接受已解密的 `Path` 与 `MediaIndex`，可在合成库上完全单测。
- **P1-D2 `proto.parse` 返回原始 `bytes`，另提供 `as_message()` 递归助手。**
  spec §7.2 写的是"length-delimited 自动解析成 dict"，但任意 blob 无法可靠区分
  "子消息"与"文本/JSON/XML"；自动递归会把中文文本误判成字段。改为：顶层解析
  只切分 wire 类型并保留 bytes，调用方显式对"应当是消息"的字段调 `as_message`。
  这是对 spec 的偏离，已记录理由；KAT 必须覆盖"文本不会被当成子消息"。
- **P1-D3 字段号唯一真源：** 所有 40xxx/45xxx/47xxx/48xxx/6xxxx/808xx 与伴随库
  列号只允许出现在 `qq/msgmap.py`。其他模块引用常量，测试里可用字面量。
- **P1-D4 表/列容忍：** 表名用正则 `^c2c_msg_table` / `^group_msg_table` 匹配；
  列用 `PRAGMA table_info` 存在性校验，缺失补 `None`；SQL 标识符双引号转义。
- **P1-D5 降级铁律：** 单条消息、单个 segment、单个媒体解析失败都只降级并计数，
  绝不中止整次导出；异常消息保留 `unknown_fields` 与 hex 片段（截断到 64 字节）。
- **P1-D6 递归上限：** 回复引用递归深度 ≤ 8，检测循环引用；超限降级为文本摘要。
- **P1-D7 先探测后映射：** 伴随库列语义、回复字段（`477xx` vs `47710`）、
  `40011=17` 的 `808xx` 语义，必须先跑只读探测脚本（只输出值形状）再写映射；
  探测结果补进 §2 与 msgmap 注释。
- **P1-D8 输出原子性：** 每个会话先写 `<slug>.partial-<pid>-<rand>/`，成功后
  `os.replace` 成正式目录；已存在正式目录先改名为 `<slug>.bak-<时间戳>`。
- **P1-D9 路径安全：** 会话 slug、媒体名共用 `sanitize_component()`（只保留
  中英文/数字/`-_`，截断 40 字符，空则回退 ID）；输出路径必须 `resolve()` 后
  仍位于 `--out` 之下；拒绝 `..`、分隔符、Windows 保留名（CON/PRN/...）。
- **P1-D10 HTML 是唯一真源：** 零外部资源；CSS/JS 全内联；`http://`、`https://`、
  `//cdn` 在产物中必须为 0；PDF 由本地 HTML 派生，不另建模板。
- **P1-D11 PDF 用系统 Edge：** 发现顺序按 spec §8.4；命令固定
  `--headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf`；
  单次 600 秒超时；失败保留 html/txt 并在报告写原因。
- **P1-D12 报告：** `ExportReport` 单独放 `export/report.py`，字段按 spec §11；
  计数恒等式 `total == ok + degraded + failed` 由测试钉死。
- **P1-D13 首发范围：** CLI `list/export` + txt/html/pdf + 报告；Web UI 见 T-W，
  **不阻塞 tag**。若用户要求 Web UI 必须随首发，则 T-W 前移，发布时间顺延。
- **P1-D14 编码：** txt 默认 UTF-8 with BOM；`--encoding gbk` 时用
  `errors="replace"` 并统计替换数；html/pdf 固定 UTF-8。
- **P1-D15 只读：** 解析/导出阶段对源目录只做 `open(..., "rb")` 与 `stat`；
  媒体复制只读源文件；任何写操作只发生在 `--out` 与临时工作目录。
- **P1-D16 尾页按页 1 头截断（R36）：** 文件大小可能大于逻辑页数；`decrypt_pages`
  读到页 1 后以 offset 28 的 database size 为上限，`decrypt_file` 在 WAL 后再次
  按头截断。尾部旧页不参与 corrupt 计数。

---

## 5. 任务分解

> 每个任务独立提交（中文 `feat:`），先写测试再写实现，完成后做变异验证
> （删/改实现必须让测试失败），并通过 `tools/check_privacy.py`。任务估时仅供参考。

### T1 — 探测工具与合成 fixture 工厂

**目标：** 让后续所有解析任务都能在"无真实数据"的合成库上 TDD。

**文件：**
- `tools/probe_formats.py`（只读；输入明文库；按 `40011/40012/45002` 输出字段号、
  wire 类型、长度、元素个数；**不打印任何文本**）
- `tools/probe_contacts.py`（只读；输入解密后的伴随库；输出列的值形状：
  `digits/u_/text/binary/empty` 与长度分布；不打印值）
- `tests/helpers/synth_proto.py`：按字段号构造 text/image/file/voice/video/face/
  reply/forward_nt/forward_legacy/poke/gray/redbag/call/live/markdown/unknown 的
  40800 字节与子元素
- `tests/helpers/synth_db.py`：建 `nt_msg.db`（c2c/group 数字列、可缺列、可加
  1000 行）+ 合成 `profile_info.db`/`group_info.db`/`rich_media.db`；可选用
  `tests/helpers/fake_sqlcipher.py` 加密伴随库

**测试先行：** 构造的每种 blob 经 `proto.parse` 后字段号/长度与预期一致；
合成库 `PRAGMA quick_check == ok`；列名带引号可查；加密伴随库能被
`decrypt_companion` 解开（T6 前先留失败测试）。

**出口：** 后续任务不需要真实 QQ 数据即可写测试；探测脚本在真机上输出与 §2 一致。

---

### T2 — `proto.py` + `msgmap.py`

**目标：** 永不抛异常的通用 wire 读取器 + 全部字段号常量。

**文件：** `qqexport/qq/proto.py`、`qqexport/qq/msgmap.py`、
`tests/test_proto.py`、`tests/test_msgmap.py`。

**测试先行：**
- wire 0/1/2/5 各一组；嵌套 `as_message`；截断 varint / 越界长度返回已解析前缀 +
  `truncated=True`；wire 3/4 遇到即停止该层；未知字段保留。
- `parse(b"中文 UTF-8 文本")` 不得把文本误判为子消息（P1-D2）。
- msgmap 无重复字段号；`ELEMENT_KIND` 覆盖 spec §7.1 的 45002 表；伴随库列常量
  与 §2.2 对齐。

**实现要点：** `read_varint` 上限 10 字节；长度字段做 `i+ln <= len(buf)` 检查；
`Value = int | bytes`；`Parsed(fields: dict[int, list[Value]], truncated: bool)`；
`as_message(value: bytes) -> Parsed`；hex 片段截断 64 字节。

**出口：** 42+ 条测试；用变异验证截断/边界分支。

---

### T3 — `tables.py` + 流式读取

**目标：** 发现表/列，按需 SELECT，流式产出 dict。

**文件：** `qqexport/qq/tables.py`、`tests/test_tables.py`。

**测试先行：** 合成库表名带后缀仍命中；缺列返回 `None`；数字列名正确加引号；
`fetchmany` 每次 ≤ 1000；`list_chats` 的会话数/最早最晚时间正确；空表不崩。

**实现要点：** `TableSet(c2c: str | None, group: str | None)`；列顺序固定
`40001,40011,40012,40013,40020,40021,40030,40033,40050,40800`；`ORDER BY "40050","40001"`；
`list_chats` 用 `GROUP BY "40021"` 并按类型区分；空表/缺列降级。

**出口：** 合成 c2c 1000 行流式读取 RSS 有界（测试只断言批次大小与计数）。

---

### T4 — `messages.py` 基础类型

**目标：** 行 → `Message`，覆盖文本/图片/文件/语音/视频/表情/灰条/红包/
Markdown/未知，保持 segment 顺序。

**文件：** `qqexport/qq/messages.py`、`tests/test_messages.py`、`tests/helpers/synth_proto.py`。

**测试先行：** 每种 45002 一条；一条消息 1–3 个元素顺序不变；缺 40800/空 blob；
非法 blob → `unknown` 且其余消息正常；图片字段取 `45406` 16B 转 hex；文件取
`45402/45405/45419`；语音取 `45906/45907`；未知字段计数。

**实现要点：** `parse_message(row, ctx) -> Message`；`ParseContext` 提供
`contacts`、`media_index`、`account_uin`、`group_id`；`Segment.kind` 按 spec §7.4；
异常包一层 `_degrade(exc, raw)`；`raw_hex` 截断。

**出口：** 基础类型全部有测试；单条异常不影响批量；变异杀掉"异常分支"。

---

### T5 — 回复 / 转发 / 戳一戳 / 通话 / 卡片 / 递归

**目标：** 处理跨元素/跨层语义，先探测再实现。

**文件：** `qqexport/qq/messages.py`（扩展）、`tools/probe_formats.py`（扩展）、
`tests/test_messages.py`。

**步骤：**
1. 用 `tools/probe_formats.py` 对真库抽样：打印 `40011=5`、`45002=7`、
   `477xx` 的实际字段组合；确认 spec 的 `47710` 是否出现、是否被外层 `40011=5`
   取代。结果写回 §2 与 msgmap 注释。
2. 实现：
   - 回复引用：若 `47710` 存在 → 递归 `as_message`；否则按 `47702/47703/47704/
     47713` 拼摘要；引用原文为空时只显示发送者。
   - 老式转发 `40011=8`：`48602` XML → `dataitem/sourcename` + `datadesc`；
     `ElementTree` 解析失败降级为文本；总长 500 字符截断。
   - NT 转发 `40011=11`：`47901` JSON 解析发送者/时间/条数/摘要，卡片 +
     `forwards_nt_unexpanded += 1`，uuid/令牌进报告（本地报告，不入库）。
   - 戳一戳 `40011=9`：`47413` 文本/`47419` action；通话 `40011=19`：
     `48151/48152` 毫秒；`40011=17`：`808xx` 先探测后映射（不确定就 unknown）。
   - 递归深度 ≤ 8、检测循环；超限降级为文本摘要（P1-D6）。
3. 测试：每种场景合成 blob；引用递归 3 层；循环 blob；XML 截断；JSON 缺字段。

**出口：** 六类都能渲染且失败可降级；未展开清单数量正确；变异杀掉递归上限与
XML 截断。

---

### T6 — 伴随库解密 + `contacts.py`

**目标：** 解密 profile/group/rich_media，建立 uid/uin → 显示名索引。

**文件：** `qqexport/core/companions.py`、`qqexport/qq/contacts.py`、
`tests/test_companions.py`、`tests/test_contacts.py`、`tests/helpers/synth_db.py`。

**测试先行：**
- `decrypt_companion` 对合成加密库（同 key/params、1024 包装）产出可 `quick_check`
  的明文；源文件字节未变；损坏库报错且不删源。
- 显示名优先级：群名片 > 好友备注 > 昵称 > `QQ号<uin>` > `未知用户<uid前8>`；
  自己固定"我"；uid/uin 双轨任一命中；群成员缺失回退。
- `contacts.json` 只含本次导出会话用到的名字，键排序固定，不含账号密码。

**步骤：** 先跑 `tools/probe_contacts.py` 确认真实列语义（`1000/1001/1002/
20002/20009...`、`60001/64002–64005`），把映射写进 msgmap；实现
`Contacts` 用 `dict[uid]` + `dict[uin]` 双索引；群名片优先；快照写盘。
若伴随库缺失/解密失败：降级为 uin/uid 兜底，报告 `warnings`，不中止导出。

**出口：** 真机联系人库能解出名字（live 时抽样，不打印内容）；合成测试全过；
变异杀掉优先级顺序与兜底分支。

### T7 — `media.py` + `media_copy.py`

**目标：** 一次建索引，按需复制被引用的媒体；源目录只读。

**文件：** `qqexport/qq/media.py`、`qqexport/export/media_copy.py`、
`tests/test_media.py`、`tests/test_media_copy.py`。

**测试先行：**
- 索引：`Pic/<YYYY-MM>/Ori/<32hex>.jpg`、`Thumb/<md5>_0.jpg`、`Ptt/*.amr`、
  重复 md5 多路径；非 32hex 文件名忽略；目录缺失不崩。
- 解析：`45406` 16B → hex 命中；`45812` 路径存在则优先；`45402` 文件名后缀匹配；
  全失败 `missing=True`。
- 复制：只复制被引用集合；`Ori` 优先于 `Thumb`；硬链接优先、失败回退复制；
  目标名冲突追加序号；源文件 mtime/size 不变；目标已存在先备份。

**实现要点：** `MediaIndex = dict[str, list[Path]]`；`resolve` 返回 `MediaRef`
（kind/source/missing/width/height/size/duration/filename/rel_path）；复制目标名
`<kind>_<md5[:8]>.<ext>`；不读文件内容；`rich_media.file_table` 只用于补
文件名/大小（可选，失败忽略）。

**出口：** 合成媒体树全过；真机 Pic 93 个文件只复制被引用者；变异杀掉
"Ori/Thumb 优先级"与 "只读"。

---

### T8 — `model.py` + `txt.py`

**目标：** 冻结共享模型，产出 spec §8.2 的 txt。

**文件：** `qqexport/export/model.py`、`qqexport/export/txt.py`、
`tests/test_txt.py`、`tests/helpers/synth_db.py`。

**测试先行：**
- 模型：`Chat.kind/peer_id/title/subtitle`；`Message` 不可变；`Segment` 的
  `quoted` 递归；`MediaRef` 字段齐全。
- txt：头部（类型/标题/群号/账号/导出时间/时间范围/条数）；日期分隔含星期；
  `[HH:MM:SS] 名称：内容`；多 segment 占位符；回复单行；老式转发缩进；
  未知消息 `[未解析 字段 N]`；UTF-8 BOM；GBK 替换计数；空会话。
- 计数：`messages_total == ok + degraded + failed`（与报告同源）。

**实现要点：** 用同一 `render_segment_text()` 供 txt 与报告摘要复用；文本先
`strip()` 但保留换行转空格规则；中文星期用固定表；BOM 用 `encoding="utf-8-sig"`。

**出口：** golden 文本测试通过；变异杀掉日期分隔/编码分支。

---

### T9 — `html.py`

**目标：** 自包含、离线可看、可流式的 HTML；大会话分片。

**文件：** `qqexport/export/html.py`、`qqexport/export/static/`（模板/样式/JS 以
Python 字符串或包内文件形式存在）、`tests/test_html.py`、`tests/test_html_shard.py`。

**测试先行：**
- golden 文件（合成 3 条消息）对比结构；自己右对齐/他人左对齐 class 存在；
  HTML 转义（`<script>` 注入文本）；头像兜底首字圆形；日期 sticky；
  图片 lazy + lightbox；视频 controls；语音下载链接；回复引用嵌套；
  老式转发折叠；NT 转发未展开标注；未解析黄色警示。
- **零外部资源**：断言产物不含 `http://`、`https://`、`//cdn`；CSS/JS 全内联。
- 分片：20,000 条阈值触发 `chat-YYYY.html` + 会话目录 `index.html`；
  流式写出（写入器按行 flush）；10 万条合成数据内存有界（测试观测峰值）。

**实现要点：** 逐条 `write()`，不建 DOM；`html.escape`；`lang="zh-CN"`；
`@media print` 按 spec §8.4；模板只用标准库字符串拼接；分片按消息年份；
顶层 `index.html` 列会话、消息数、时间范围、文件大小、格式链接、报告摘要。

**出口：** golden/no-external/shard 全过；变异杀掉转义与分片阈值。

---

### T10 — `pdf.py`

**目标：** 由本地 HTML 派生 PDF；Edge 缺失/超时可降级。

**文件：** `qqexport/export/pdf.py`、`tests/test_pdf.py`（Windows only）。

**测试先行：**
- 用假 `msedge.exe`（脚本写入固定 PDF 字节）验证命令参数、路径转义、超时 kill、
  失败时不删 html/txt、报告写 `pdf_error`。
- Edge 发现顺序：Program Files (x86) → Program Files → PATH；找不到报可操作错误。
- 30,000 条阈值：未加 `--allow-large-pdf` 时拒绝并提示；加了才生成。
- 真机 live：一个会话导出 pdf，`%PDF-` 头、页数 > 0、中文可渲染（人工确认）。

**实现要点：** `subprocess.run([...], timeout=600)`；`file:///` 绝对路径；
`@page` 边距 + `break-inside: avoid`；不引入 WeasyPrint；PDF 无页码是已知限制。

**出口：** 假 Edge 测试全过；真机 PDF 成功（或报告明确失败原因）。

---

### T11 — 编排器 + `ExportReport` + CLI `list/export`

**目标：** 端到端可用命令；原子输出、路径安全、报告。

**文件：** `qqexport/export/orchestrator.py`、`qqexport/export/report.py`、
`qqexport/cli.py`、`tests/test_export_cli.py`、`tests/test_orchestrator.py`。

**测试先行：**
- `list`：合成库列出 c2c/group，标题经 contacts 解析；空库提示；多账号必须
  `--uin`；退出码 0/1/2。
- `export`：`--chat` 重复、`--all` 互斥；`--format txt` 默认 html；未知格式报错；
  `--out` 不存在则创建；已存在会话目录先备份；`.partial-*` 不残留。
- 路径安全：标题含 `../`、`C:\`、`CON`、超长 → slug 清洗后仍落在 `--out` 内；
  测试断言 `resolve()` 前缀。
- 报告：JSON/txt 字段齐全；计数恒等；媒体计数；未知字段；PDF 失败原因。
- 无 KeyStore 时提示 `key scan/set`，退出码 1；不打印口令。

**实现要点：** `export_chats` 内 `with ExportSource(...)`；对每个 chat 打开
`iter_messages` 流；txt/html 先写，pdf 后写；失败按 chat 记录 `warnings` 继续；
报告最后统一写；`--no-media` 跳过复制；`--encoding`/`--shard-size` 透传。
帮助文本不写真实账号/路径示例。

**出口：** CLI 冒烟可在干净环境跑通；e2e 合成库三格式产物非空；变异杀掉
`--all`/`--chat` 互斥与备份逻辑。

---

### T12 — 全链路、真机验收、性能与发布门

**目标：** 证明"导出功能完整"，达到 spec §10.3 全部验收项。

**文件：** `tests/test_e2e.py`、`tests/test_live_export.py`（`@pytest.mark.live`）、
`docs/compat-matrix.md`、`README.md`、`docs/HANDOFF.md`。

**测试：**
1. 合成明文库（c2c + group，各含全类型 segment）→ txt/html/pdf 全跑通；
   产物存在且非空；报告计数自洽；HTML 零外链；PDF 头正确。
2. live（真机、缓存口令、`TemporaryDirectory`）：
   - 一个真实 c2c + 一个真实群聊，各导出 txt/html/pdf；
   - **最近的消息在**（与 QQ 客户端肉眼比对，记录时间戳）；
   - 媒体：只复制被引用者，缺失计入报告；
   - 只读性：QQ 运行中只断言未删除；QQ 退出后比对 identity 三元组；
   - 清理：临时目录不存在、无 `.partial`、`git status` 干净；
   - 确定性：同一快照 + 固定时钟两次导出逐字节一致。
3. 性能：335 MB 库（或现有最大库）单会话/全量导出耗时与 RSS 记录；HTML 分片
   10 万条流式内存有界；PDF 超时行为。
4. 发布门：`python -m build` + `tools/check_wheel.py` 含全部新模块；干净 venv
   安装后 `qqexport list` 可运行（无真实数据时给明确提示）；CI 3.12/3.13/3.14 全绿。

**出口（发布条件）：** §6 验收表全部 PASS，没有 SKIP；否则 README 只能写
"部分验收"，不得打 tag。

---

### T-W（可选，不阻塞首发）— 本地 Web UI

按 spec §9.2 实现 FastAPI/SSE：`GET /api/status|chats`、`POST /api/export`、
`GET /api/progress/{job_id}`、`GET /api/report/{job_id}`、`POST /api/key/scan`。
仅监听 `127.0.0.1`，无鉴权但所有路径参数走 `sanitize_component()` 白名单；
导出目录必须由用户显式选择。若用户要求 Web UI 随首发，T-W 前移到 T11 之后、
T12 之前，发布时间顺延。

---

## 6. 验收表（spec §10.3 + P1 增补）

| # | 门槛 | 判定 | 证据 |
|---|---|---|---|
| 1 | 普通测试全绿（`live` 除外） | 全 PASS | CI 3.12/3.13/3.14 |
| 2 | `doctor` 11 项无 fail | 全 ok | live |
| 3 | `list` 列出真实会话，标题可读 | PASS | live 截图/脱敏输出 |
| 4a | 任选真实 c2c + group 导出 txt | 记事本中文正常，`名称：内容` | 人工 + golden |
| 4b | 导出 html | 双击离线可看、气泡正确、图片可放大、**无外部请求** | DevTools + 零外链断言 |
| 4c | 导出 pdf | 中文正常、不跨页断裂、图片在位 | 人工 + `%PDF` |
| 5 | 最近的消息在（WAL 生效） | 与客户端肉眼比对 | live |
| 6 | 报告计数自洽 | `total == ok + degraded + failed` | 单测 + live |
| 7 | 只读性 | QQ 退出 identity 不变 → PASS；运行中仅"未删除" | live 两条件 |
| 8 | 同快照两次导出字节一致 | 固定时钟 + 流式 SHA-256 | 单测 + live |
| 9 | 无明文/密钥残留 | 临时目录已删、无 partial、git 干净 | 单测 + live |
| 10 | 路径穿越防护 | 恶意标题全部落在 `--out` 内 | 单测 |
| 11 | HTML 零外部资源 | 产物无 http(s)/CDN | 单测 |
| 12 | 媒体只复制被引用者 | 计数一致、源文件未变 | 单测 + live |
| 13 | 报告未知字段统计可用 | `unknown_fields` 有值且可解释 | live |
| 14 | wheel 含全部新模块 | `check_wheel.py` 通过 | 发布门 |

**阻断规则：** 任一 SKIP/UNVERIFIED 都不能打 tag；README 写"P1 部分验收
（待验证项：…）"。只有全部 PASS 才允许发布。

---

## 7. 测试与工程约定

- **合成优先：** 所有单测用 `tests/helpers/synth_*` 构造的库/blob，禁止真实数据。
- **fixture：** 现有页 1 fixture 继续用于头/盐/HMAC；P1 不新增真实样本。
- **变异验证：** 每个任务的出口都要给出至少一条"删/改实现 → 测试失败"的记录，
  与 P0 相同；不得只跑覆盖率。
- **隐私：** 每个提交前 `tools/check_privacy.py`（staged + untracked）与
  `--audit` 全库巡检；报告/日志/测试输出不得含真实 uin、路径、昵称、正文。
- **提交：** 直接在 `main`；一个任务一个中文 `feat:`；正文解释为什么与偏离；
  推送后 CI 必须绿。
- **依赖：** 不新增第三方运行时依赖（PDF 用系统 Edge；HTML 用标准库）。
- **平台：** 解析/导出核心跨平台；PDF 与 DPAPI 相关测试标 `win`，CI 只跑 Windows。

---

## 8. 风险与待确认项

| # | 风险/未知 | 处理 |
|---|---|---|
| P1-R1 | 回复字段与 spec 不一致（实测 47702–47713，未见 47710） | T5 先探测；两种布局都支持；unknown_fields 记录 |
| P1-R2 | 伴随库列语义未知 | T6 先跑值形状探测；映射常量集中；猜错只影响显示名，不中止 |
| P1-R3 | 伴随库也有 WAL/加密参数差异 | `decrypt_companion` 复用 detect+WAL；失败降级为 uid/uin |
| P1-R4 | `40011=17` 的 808xx 语义未知 | T5 探测；不确定就渲染"未知卡片"+计数 |
| P1-R5 | 335 MB 全量导出耗时/内存 | 流式 + 分片 + 硬链接；T12 实测并记录 |
| P1-R6 | PDF 无页码、Edge 缺失/超时 | 明确写入限制；失败保留 html/txt |
| P1-R7 | 群成员量大、contacts.json 过大 | 只写被引用成员；分文件或截断（T6 决定） |
| P1-R8 | HTML 5 万条浏览器卡顿 | 20k 分片；分片导航；流式写 |
| P1-R9 | 时间戳/星期本地化 | 固定中文表 + 本地时区；测试固定时钟 |

**待用户确认（不阻塞 T1–T2，开工前回复即可）：**
1. **Web UI 是否必须随首发？** 建议否（先 CLI 完整导出，Web 随后）。
2. 默认输出目录用 `.\export` 还是 `%USERPROFILE%\Documents\QQExport`？建议前者。
3. `--decode-voice` 是否要进首发？建议否（P4 可选，默认关闭）。

---

## 9. 执行顺序与依赖

```
T1 ─┬─► T2 ─► T3 ─► T4 ─┬─► T5 ─┐
    │                    ├─► T6 ─┤
    │                    └─► T7 ─┤
    └───────────────────────────┴─► T8 ─► T9 ─► T10 ─► T11 ─► T12 ─► 发布
```

- T1/T2 可并行；T3 依赖 T2 的字段常量。
- T4 完成后 T5/T6/T7 可并行（共享合成 fixture）。
- T8 依赖 T4/T6/T7 的模型输入；T9/T10 顺序执行；T11 最后接线。

---

## 10. 附：本文档依据的探测记录（脱敏）

- 探测日期 2026-10-29，只读；未修改源目录。
- 探测脚本（本地、不入库）：`.git/probe_p1_facts.py`、`.git/probe_p1_facts2.py`；
  正式版将收敛为 `tools/probe_formats.py` / `tools/probe_contacts.py`（T1）。
- 记录内容仅表名、列名、字段号、数量、文件计数/大小；不含 uin、路径、昵称、正文。
- 与 spec 的差异（`477xx`、`40011=17`、伴随库加密）已在 §2 与本文件 P1-R1/R3/R4
  标注，实现前必须复核。


