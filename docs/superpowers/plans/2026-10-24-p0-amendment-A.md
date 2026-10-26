# P0 计划修正案 A（Amendment A）

**日期：** 2026-10-24
**状态：** 规范性（normative）。与本文件冲突时，**以本文件为准**，优先级高于
`2026-10-24-qq-export-p0-decryption-pipeline.md` 的原文。
**来源：** 用户提供的两份外部评审
（`QQExport_P0_Task1-13_执行方案详细评审建议.md`、
`QQExport_P0_补充建议_成熟实践借鉴.md`），经控制器逐条技术甄别后裁定。

本修正案按 task 组织。派发某个 task 时，实现者同时拿到该 task 的 brief 与本文件
中对应的小节。未提及的 task 段落一律照原计划执行。

---

## 0. 全局约束增补（对所有 task 生效）

### 0.1 三态结果，不允许「warning 后照常成功」

所有产出数据的结果对象必须携带显式状态，取值：

| 状态 | 含义 |
|---|---|
| `complete` | 全部页认证通过、WAL 完整回放到最后 commit、`quick_check == "ok"` |
| `lossy` | 产物可用但有缺失：存在损坏页，或 WAL 未完整回放（回退到主库版本） |
| `failed` | 未能产出可查询的明文库 |

`DecryptResult` 增加 `status: str` 字段。任何 `corrupt_pages > 0`、
`wal_signature_changed`、`wal_stopped_early`、`integrity_check != "ok"`
都必须把状态压到 `lossy` 或 `failed`，**不得只追加 warning 就返回成功**。
doctor 与 CLI 必须如实呈现该状态。

### 0.2 skip ≠ pass

测试与验收汇总必须三态分列：`PASS` / `WARN` / `SKIP(未验证)`。
任何一项 SKIP 都使总体结论为「部分验收」，不得表述为「全绿」。
没有有效 WAL commit 时，结论必须写成「本次没有可验证的 WAL 更新」，
不得据此声明 WAL 恢复通过。

### 0.3 明文产物生命周期

- **pytest 的 `tmp_path` 默认保留最近 3 次运行的目录**（`tmp_path_retention_policy`
  默认 `all`）。它**不是**清理保证。凡是会产生明文库的测试（Task 12 全部、
  Task 9 的 doctor 探测、Task 13 的 P1 探测），一律用
  `tempfile.TemporaryDirectory()` 上下文管理器，**并在退出上下文前关闭所有
  SQLite 连接与文件句柄**——Windows 上句柄未关会导致清理抛 `PermissionError`。
- 清理失败必须表现为测试失败或显式警告，不得静默吞掉。
- 不得使用固定的探测目录名（如 `work-p1-probe`）：它不匹配 `.gitignore` 的
  `work/` 规则，中断后会遗留明文。
- 不得把明文库作为 CI artifact 上传。需要诊断信息时只输出脱敏摘要与页级计数。
- `pyproject.toml` 增加 `tmp_path_retention_policy = "none"`（见 §1）。

### 0.4 密钥流转

- 密钥**不得**经过：命令行位置参数、shell 命令替换、通用环境变量、
  日志、异常消息、断言文本、JSON 报告、notes 文档。
- 测试需要真实密钥时，**在测试进程内直接调用 `KeyStore.load()`** 读取。
- 只允许出现 SHA-256 指纹前 8 位。

### 0.5 每次提交前的敏感信息扫描（两层）

推送前必须扫描 **staged diff**，范围包括源码、测试、文档、fixture、
测试输出、提交信息与作者/提交者元数据，检查项：

- uin 形状（5–12 位纯数字），且不等于已批准的合成值 allowlist：
  `123456789` `11111111` `22222222` `33333333` `12345`。
  本机真实 uin **不得写入任何入库文件**（包括本修正案），需要指代时写
  「本机真实 uin」或掩码 `****<后4位>`。
- Windows 用户目录（`Users\<name>\`、`/home/<name>/`）
- 真实数据绝对路径、昵称、消息正文片段
- `*.db` / `*.db-wal` / `*.bin` 等数据文件是否被 staged
- 16/32 字节可打印 ASCII 的密钥形状串

**GitHub Push Protection 只作第二层拦截，不能替代本扫描**——它识别平台已知的
凭证类型，识别不了纯数字账号标识与用户路径。

### 0.6 中断与恢复约定

- 每次运行使用**唯一**临时目录（`tempfile.mkdtemp` 前缀 `qqexport-`），
  partial 文件名含 pid 与随机后缀，避免并发运行互相覆盖。
- partial 必须写在**目标文件同一卷**上，以满足 `os.replace` 的原子性要求。
- 已存在的最终产物在成功前**保持不变**；成功后同卷原子替换。
- 进程被强杀后，下次启动通过唯一前缀识别遗留 partial，只清理自己的。
- 需有故障注入测试：在页写入途中抛异常，断言旧产物完整、partial 已清理、源数据未变。

### 0.7 测试分层与 marker

| 层 | 数据 | 环境 | marker |
|---|---|---|---|
| 单元 | 合成页 / 合成 WAL / 假 reader | 任意 | 无（默认） |
| Windows 集成 | 合成文件 + 可控假 Win32 对象 | Windows | `win` |
| 本机 live | 用户明确选择的自有账号 | 人工本机 | `live` |

新增 `win` marker。CI 不得安装 QQ、不得读真实进程内存、不得使用用户密钥、
不得上传 pytest 临时目录或 doctor 报告。live 未运行时，报告必须单列
「live 未运行」，不得让自动化结果看起来全绿。

---

## 1. Task 1（已完成）— 追加项

不返工已完成的代码。以下两项在后续 task 中补齐：

- **`pyproject.toml`** 增补（由 Task 10 的执行者顺手改）：
  ```toml
  [tool.pytest.ini_options]
  tmp_path_retention_policy = "none"
  ```
  理由见 §0.3。
- **console script 的实际可用性**留到 Task 10 验证。Task 1 的 editable install
  只证明打包元数据正确，不证明 `qqexport` 命令可用。

Task 1 已记录的 4 项 deferred minor 维持延后。

---

## 2. Task 2 — `discover.py`

### 2.1 全盘扫描必须显式 opt-in

`discover_accounts(scan_drives=False)` 的默认值保持 `False`，且
**doctor 默认绝不递归扫盘**。新增测试断言：`run_doctor()` 不传 `scan_drives`
时，`_find_tencent_files` 未被调用。

递归扫描增加**已访问目录计数上限**（`MAX_VISITED_DIRS = 20000`），超限即停止
并在返回的 plan/报告里标注 `scan_truncated=True`。深度上限保持 5。

### 2.2 错误分类，不得一律 `DiscoveryError`

新增四个异常子类，消息中**不得回显完整用户路径或真实 uin**（用
`<Documents>` / `uin[0]` 之类的占位）：

```python
class DiscoveryError(RuntimeError): ...          # 基类
class NoAccountFound(DiscoveryError): ...        # 一个账号目录都没有
class DatabaseMissing(DiscoveryError): ...       # 目录在但没有 nt_msg.db
class DataDirPermissionDenied(DiscoveryError): ...
class ScanAborted(DiscoveryError): ...           # 超时或超过目录上限
```

`discover_accounts` 按实际情况抛出对应子类；`NoAccountFound` 的消息必须包含
`--data-dir` 的使用指引。

### 2.3 多账号不得静默取第一个

新增：

```python
def select_account(accounts: list[QQAccount], requested_uin: str | None
                   ) -> QQAccount:
    """唯一确定则返回；否则抛 AmbiguousAccountError，消息列出候选的序号与
    uin 后 4 位掩码（如 `****8588`），要求调用方显式指定。"""

class AmbiguousAccountError(DiscoveryError): ...
```

CLI 与 doctor 一律经 `select_account` 取账号。掩码规则：`"****" + uin[-4:]`。
控制台交互可以显示完整 uin（那是用户自己的机器），但**写入报告/JSON 时一律用掩码**。

### 2.4 符号链接与重解析点

`_iter_accounts_under` 与 `_find_tencent_files` 已使用
`is_dir(follow_symlinks=False)`，**不得改为跟随**。新增一条测试用假
`os.scandir` 断言 `follow_symlinks=False` 被传入（Windows 上创建 junction
需要权限，不适合放进单测）。

### 2.5 路径合理性检查

`QQAccount` 增加校验：`db_dir` 必须是 `data_dir` 的直接子目录且名为 `nt_db`，
`msg_db` 必须位于 `db_dir` 内。不满足则不纳入结果（而非静默继续）。

---

## 3. Task 3 — `sqlcipher.py`

### 3.1 必须增加独立 known-answer 测试（最高优先级）

**理由：** 加密方向（`fake_sqlcipher`）与解密方向共用同一套常量与布局理解。
若两端对同一处理解错误（例如 HMAC 输入的拼接顺序、页号编码字节序），
round-trip 会**双双抵消而通过**，真机解密才暴露。必须引入与被测代码无关的
外部参考值。

新增 `tests/test_sqlcipher_kat.py`，覆盖四层：

1. **`derive_keys` 输出钉死。** 对固定的 `passphrase` 与 `salt`，断言
   `aes_key` 与 `mac_key` 等于内联的十六进制期望值。期望值的**产生方式**：
   在本机用 `hashlib.pbkdf2_hmac` 单独算一次、把结果硬编码进测试，并在
   docstring 里写明「provenance: Python 3.14 / OpenSSL <版本>，
   参数 KDF_ITER=4000、FAST_ITER=2、mac_salt = salt XOR 0x3A」。
   这条抓的是**接线错误**（用错迭代数、忘了 0x3A、KDF 选了 sha1 而非 sha512），
   不是 hashlib 本身的正确性——后者由 OpenSSL 保证。

2. **HMAC-SHA1 对 RFC 2202 公开用例。** 用测试用例 1 与 2。
   **不要相信任何人凭记忆写出的摘要值**（包括本修正案）：实现者必须
   联网核对 RFC 2202 原文后再内联期望值，并在 docstring 注明来源章节。
   若核对结果与本文件描述不符，以 RFC 为准。

3. **AES-256-CBC 对 NIST SP 800-38A F.2.5 公开向量。** 同上：
   实现者必须联网取原文的 key / IV / plaintext / ciphertext 十六进制，
   注明来源，不得凭记忆填写。

4. **页级组合 KAT（最关键的一条）。** 在测试文件内**不使用
   `fake_sqlcipher`**，只用 `hashlib` + `cryptography` 原语，按 spec §6.4 的
   文字描述独立拼出一个 4096 字节密网页（自己算 salt 派生、自己拼
   密文+IV+tag+padding、自己算 HMAC），然后断言 `decrypt_page` 还原出预期明文。
   这条是唯一能抓住「加解密两端同时理解错」的测试，因为它绕开了共享的
   helper 代码路径。

所有向量必须注明来源（RFC / NIST 文档编号或本机计算方式），且必须是合成数据。
**若第 2、3 条无法联网核对到权威值，就把该条改成第 1 条的形式（本机计算 +
注明 provenance），并在报告中说明未能取得外部权威值**——不得凭记忆填入
看似合理的十六进制，那会制造假权威，比没有测试更糟。

### 3.2 cipher 指纹是观测值，不是契约

`VERIFY_COMBOS[0]` 为 `("sha512","sha1",48)` 是**本机 QQ 9.9.36 的预期命中项**，
不是所有版本永远成立的保证。因此：

- 单元测试可以硬断言八组合的顺序与内容。
- **Task 11/12 的真机测试不得硬断言指纹等于 `sha512/sha1/48`**。改为：
  断言指纹 ∈ 八组合之一，记录实际值，并在实际值 ≠ 预期值时打印
  「本机 QQ 版本 <ver> 命中 <fp>，与 9.9.36 的预期不同，请更新兼容矩阵」
  的 WARN，**不使测试失败**。
- 兼容矩阵见 §13.2。

### 3.3 reserve 置零是预期输出

明确写入模块 docstring：`decrypt_page` 的输出页尾部 reserve 区**恒为零**，
不照抄密文的 IV+HMAC。禁止再出现「字节级还原密文 reserve」这类断言。

### 3.4 异常不得携带敏感内容

所有 `ValueError` / 返回 `None` 的路径，其消息与日志中不得包含口令、
原始页二进制、或数据库绝对路径。新增测试断言异常消息的字符集。

---

## 4. Task 4 — `wal.py`

### 4.1 commit 语义写成显式状态机并逐态测试

必须新增的用例（原计划缺）：

- **多个 commit 帧**：只回放到最后一个有效 commit，中间 commit 的
  `dbsize` 不得成为最终截断长度
- **重复页号**：后写帧覆盖先写帧（原计划已有，保留）
- **commit 帧之后还有未提交帧**：这些帧绝不写入
- **页号非法分两类处理（R18 修正，见下）**
- **frame HMAC 失败分别出现在 commit 之前与之后**：两种情况的截断长度不同
- **截断尾帧**（原计划已有，保留）
- **inspect 与 apply 之间 WAL 被改写**（原计划已有，保留）

#### R18 —— 修正本节原文：`pgno > commit_size` 是良性帧，不是损坏

本节原文要求「页号越界（`pgno == 0` 或 `pgno > commit_size`）：拒绝写入并计入
`corrupt_frames`」。**这条把两种性质完全不同的情况混为一谈，Task 4 评审实证推翻了它：**

- **`pgno == 0` 是真损坏。** SQLite 的 `walDecodeFrame` 也拒绝它，且
  `db.seek((0-1)*4096)` 会得到负偏移、在 Windows 上抛 `OSError`。
  → **硬中止**，计入 `corrupt_frames`。
- **`pgno > 最终 commit_size` 是良性帧**，SQLite 自己**不**视为损坏：
  该帧照写，随后被截断掉。它在两种真实场景下可达：
  1. 跨 commit：前一个 commit 把库涨到 N 页，后一个 commit 收缩到 M < N 页
     （SQLite 的 `releasePage()` 置 `bDoTruncate`，`sqlite3BtreeCommit` 调
     `sqlite3PagerTruncateImage` 压低 commit 帧的 `nSize`）
  2. 单事务内：先追加高页号帧，commit 前又释放，于是 commit 帧的 `dbsize`
     低于已入帧的最高页号

  评审实测：WAL `[pgno9 dbsize0, pgno10 dbsize10, pgno2 dbsize8]` 对 10 页库，
  按原文实现得到 `applied=0, corrupt=1, stopped=True, trunc=0` ——
  **一个完全合法的 WAL 的全部贡献被丢弃**，且健康帧被误标为损坏。
  → **跳过该帧（`continue`）而非中止（`break`）**，**不计入** `corrupt_frames`，
  改为在 `notes` 追加一条说明（保留可观测性，且不动已冻结的字段集）。

这正是本 task 要防的「最新消息静默丢失」，只是被 §0.1 降级成 `lossy` 而非彻底静默。

#### R19 —— 新增：pass 2 绝不得写入未提交事务的帧

评审实测发现原 brief 的 Step 4 参考实现有真缺陷：当 pass 2 因 HMAC 失败或页号
守卫提前 `break` 时，**此前已写入的帧会留在产物里**，其中可能包含属于
「从未提交的事务」的帧，而 `target_pages` 仍为 0 → 不截断。

实测 WAL `[pgno2 dbsize0, pgno3 dbsize0(HMAC坏), pgno4 dbsize6]` 得到
`applied=1, corrupt=1, trunc=0`，且**未提交的 page 2 被写进了产物**。

**SQLite 的原子性边界是 commit 帧。** 写入半个事务会让 page 2 引用从未写过的结构，
即产物**结构性损坏**——`quick_check` 随之失败、状态被判 `failed`，
于是「本可得到一个稍旧但完好的库」变成「什么都拿不到」。

**裁定：** pass 1 必须在可应用范围内记录**最后一个 commit 帧的帧号与其 `dbsize`**；
pass 2 的回放上界压到该帧号，截断长度用该 `dbsize`。
若可应用范围内**没有任何 commit 帧**，则一帧都不写、库保持原样。

配套：brief 中 `test_apply_stops_at_page_hmac_failure_keeping_earlier_frames`
断言「保留较早帧」（`out[PAGE_SZ+100] == 0x11`）—— **该断言钉住的是错误行为，
必须反转**为 `frames_applied == 0` 且 `db.read_bytes() == before`。
测试名里的 "keeping_earlier_frames" 本身就是错误观念：较早的**未提交**帧绝不能保留。

### 4.1b `WalApplyResult` 的冻结字段集含 `notes`

**R20：** brief 的 Produces 块列了 6 个字段，但 brief 自己的 Step 4 参考实现
定义并使用了第 7 个 `notes: list[str]`。两者自相矛盾，实现按代码走是对的。
裁定：**Produces 块补上 `notes`**，冻结字段集为 7 个。理由：`notes` 是 §0.1
三态诊断的承载处，且 R18 的跳过说明也要写进它——它是 load-bearing 的，
不能删。字段位于末尾且有默认值，Task 8 的位置参数构造不受影响。

### 4.1c 一并修掉的评审 Minor（本轮内）

- `test_inspect_stops_at_recycled_frame_salt` **不具鉴别力**：删掉 salt 比较后
  测试仍通过，因为陈旧 WAL 代的头 seed 不同，累计校验和会先拒绝该帧。
  **salt 检查目前零覆盖**，且实现者报告第 10 条对它的声称是假的。
  必须重造 fixture：salt 不匹配但校验和按**当前**头 seed 重算，
  使删掉 salt 比较真的会让测试失败。
- `_frames_raw` 复制了 `_iter_frames` 的读循环且**不重复校验任何东西**，
  留下 TOCTOU 窗口：若 QQ 在 pass 间 checkpoint 并重启 WAL，pass 2 会回放
  新一代的帧（页密钥相同故 HMAC 通过）却沿用旧的 `applicable` 计数。
  改为复用 `_iter_frames` 并以 `applicable` 为上限，零成本关闭该窗口。
- `wal.py` 的 `state[0] >= last_commit` 应为 `>= frame_count`
  （约束原文是「`mxFrame` 小于扫描所得」，`frame_count` 才是扫描结果）。
- `nPage` 被返回却从未使用：要么与 `commit_size` 交叉校验，要么从元组里去掉。
  裁定：**做交叉校验**（`nPage == 0` 或 `nPage == commit_size` 时才算一致）。
- `fake_sqlcipher.plain(tag)` / `test_wal.py` 的 `plain(tag)` 把整 4096 字节
  都填成 `tag`，含 reserve 区——那不是合法的 SQLCipher 明文页。
  当前无断言触碰页尾所以无害，但是个陷阱。改为尾部 reserve 区填零。

### 4.1d 本轮**不**修、记录在案的项

- **spec §6.5 要求的 `-shm` `iVersion`（偏移 0）与页大小编码（偏移 14）交叉校验：延后。**
  裁定理由：`-shm` 在本设计中**仅**用于交叉验证，任何缺陷都 fail-safe 退化为
  「仅扫描 WAL」，不影响正确性；而我对 WalIndexHdr 偏移 14 的确切语义
  （u16 页大小编码，1 表示 65536）只有 chatlog-keeper 一处二手依据，
  不足以据此写死断言。**代价：** spec §6.5 的措辞比实现更严，
  需把 spec 改为「salt + 校验和 + 字节序标志 + mxFrame + nPage」以与实现一致。
- 三个 shm 回退测试命名不准（`b"\x99"*136` 实为两份相同副本，
  触发的是字节序标志分支而非「垃圾」），且缺 `-shm` 过短的覆盖
- `reason` 字符串未冻结，仅 `"ok"` / `"wal_absent"` / `"page_size_mismatch"`
  被测试钉住；Task 9 的 doctor 不得对其它值做字符串匹配
- `hdr` 在 `try` 内绑定、`try` 外使用（当前每条失败路径都先 return，故正确但脆）
- 只读文件测试未断言 `db.read_bytes() == before`，且在 POSIX 上以 root 运行会失败

### 4.2 回放只写临时明文库

`apply_wal` 的目标 `db_path` 必须始终是解密产物（临时明文库），
绝不可能是 QQ 数据目录下的文件。新增测试：传入一个无法写入的路径时
抛 `OSError` 而不是静默成功。

**Windows 实测补充（R21）：** Windows **忽略目录上的只读属性**，
因此「只读目录」测不出来。改为把目标**文件**设为只读（`S_IREAD`），
断言 `OSError`（`PermissionError` 是其子类）。该写法在 POSIX 上同样成立，
比目录方案更可移植。

### 4.3 结构先冻结

Task 8 集成前，`WalPlan` / `WalApplyResult` 的字段不得再改。
冻结集以 §4.1b 为准（`WalApplyResult` 含 `notes`，共 7 字段）。
若集成时发现需要新字段，走修正案而不是就地改。

---

## 5. Task 5 — `snapshot.py`（**改动最大，最先重审**）

### 5.1 有效 page 1 不能证明 DB 与 WAL 同时刻

原计划的 validator 只校验主库 page-1 HMAC。这**不足以**证明
`db` / `wal` / `shm` 三件套来自同一时点。改为四重校验：

```python
@dataclass(frozen=True)
class FileIdentity:
    exists: bool
    size: int
    mtime_ns: int
    dev: int        # os.stat().st_dev —— Windows 上即卷序列号的映射
    ino: int        # os.stat().st_ino —— Windows 上即文件索引

def file_identity(path: Path) -> FileIdentity: ...
```

**R22 —— 用 `os.stat` 即可，不要调 Win32 API。** 本节原文写的是
「volume serial + file index」，容易被理解成需要 `CreateFileW` +
`GetFileInformationByHandle`。控制器在本机 Python 3.14.6 实测：
`st_file_index` **不存在**，但 `st_dev` 与 `st_ino` 均已正确填充
（Windows 上 `st_ino` 就是文件索引、`st_dev` 就是卷序列号的映射）。
因此 `(st_dev, st_ino, st_size, st_mtime_ns)` 四元组全部来自 `os.stat`，
无需 ctypes，也无需处理 `FILE_SHARE_WRITE` 共享标志。
文件不存在时返回 `FileIdentity(exists=False, ...)` 而不是抛异常。

`snapshot()` 的新流程：

1. 复制**前**记录 db/wal/shm 三者的 `FileIdentity`
2. 复制三件套
3. 复制**后**再次记录源三者的 `FileIdentity`
4. 前后不一致 → 判定「复制期间源发生变化」→ 清理本轮副本 → 重试
5. 一致 → 再做内容级校验：
   - 主库：`size % 4096 == 1024`、page-1 salt 非全零、（有口令时）`detect_params` 命中
   - WAL：调用 `wal.inspect_wal(副本wal, 副本shm)`，要求 `valid is True`
     且 `page_size == 4096`；`reason` 为 `wal_absent` 时视为通过（无 WAL 是合法状态）
6. 全部通过才返回 `SnapshotResult`，并携带 `consistency: str` 字段：
   - `"proven"`：拿到了 shm 锁 **且** 前后 identity 一致 **且** WAL 校验通过
   - `"best_effort"`：未拿到锁，但前后 identity 一致且 WAL 校验通过
   - `"unverified"`：identity 一致但 WAL 校验失败（仍返回，由上层决定）

### 5.2 措辞必须诚实

`snapshot.py` 的模块 docstring、`SnapshotResult` 的字段名与所有面向用户的
文案，一律称**「尽力获取一致快照」**，不得声称「已证明一致性」。
三次重试本身不构成一致性证据——只有 §5.1 的 identity 比对与 WAL 校验才是。

### 5.3 SHM 锁必须先在目标 Windows 上实证

**R23 —— 自动化测试用合成文件，不锁真实 QQ 的 `-shm`。** 本节原文写的是
「在本机真实 `-shm` 文件上验证」。控制器裁定改为合成文件，理由：
锁的**机械语义**（偏移、长度、非阻塞、失败降级、不改内容）与被锁文件是不是
QQ 的毫无关系，任何 ≥123 字节的文件都能验证；而在自动化测试里去锁一个
正在运行的 QQ 所持有的文件，是拿真实客户端的稳定性换零额外信息。
「偏移 120 是否真是 SQLite 的 WAL-index 锁区」属于对 SQLite 布局的考证，
应通过阅读 SQLite 文档/源码确认，而不是通过在真机上试锁观察 QQ 是否被阻塞——
后者本身就是我们想避免的侵入行为。

新增 `win` marker 的测试，在一个**大小与真实 `-shm` 相同（32768 字节）**的
合成文件上验证：

- 偏移 120、长度 3 的 `msvcrt.locking(LK_NBLCK)` 能成功取得
- 取得与释放**都不改变文件内容**（前后逐字节比较）
- 锁被同一进程的另一句柄持有时，第二次 `LK_NBLCK` 立即失败而非阻塞
- 文件不存在 / 长度不足 123 字节 / 以 `"r+b"` 打开失败时优雅降级为 `locked=False`
- 上下文管理器在异常路径下也会解锁并关闭句柄（不泄漏）

并**额外要求**：在实现 `shm_lock` 前，先用 WebFetch 核对 SQLite 官方文档或
`wal.c` 源码中 WAL-index 头之后的锁区布局（`WAL_SHM_NLOCK`、锁字节起始偏移），
把核对结论与出处写进模块 docstring 与报告。**若核对结果与「偏移 120、长度 3」不符，
停下来报告，不要猜也不要照抄**——锁错位置要么锁不住、要么干扰 QQ。

### 5.4 必须覆盖的异常路径

- WAL / SHM 在复制前消失（`copy_family` 已容忍，补测试）
- 复制过程中 WAL 被轮转或截断（identity 比对应捕获 → 重试）
- 源文件被替换（identity 的 volume/index 变化应捕获）
- **目标目录已有上一轮的旧副本** → 复制前必须先清空目标目录中的同名文件，
  否则会把旧 WAL 与新 DB 混在一起。新增测试。
- 重试时**清理不完整副本**（新增测试断言目标目录在失败重试后无残留）

### 5.5 与 Task 4 的顺序

§5.1 第 5 步要调用 `wal.inspect_wal`，因此 **Task 5 必须在 Task 4 之后**。
原计划 DAG 已满足此顺序，执行时不得调换。

---

## 6. Task 6 — `keyscan.py`

### 6.1 扫描必须显式 opt-in（已在原计划，此处强调）

只有 `qqexport key scan` 与 `qqexport doctor --scan` 才读进程内存。
默认 doctor 只用缓存密钥，无缓存则该项 fail 并给出可操作提示。

### 6.2 「多进程取第一个验证通过者」是正确行为 — 驳回评审意见

评审建议不要默默采用第一个验证通过的候选。**驳回**，理由：验证回调是拿
**用户选定账号**的真库 page-1 HMAC 做的，能通过的候选在密码学上就是该账号的
密钥（16 字节口令空间下伪造 HMAC-SHA1 碰撞的概率可忽略）。这不是启发式挑选，
是确定性判定。

改为**用测试把这个行为钉住**：新增测试断言「多个 QQ 进程、其中一个含正确
密钥时，`scan_key` 返回该密钥」以及「验证回调只对选定账号的库生效」。

### 6.3 补充异常路径测试

- 扫描中途目标进程退出（`ReadProcessMemory` 失败）→ 跳过该区域继续，
  不崩溃；若所有进程都退出则返回 `None`
- 部分读取（`ReadProcessMemory` 返回的 `read_n` 小于请求长度）→ 只用实际读到的字节
- 区域在 `VirtualQueryEx` 与 `ReadProcessMemory` 之间失效 → 返回 `None` 并跳过
- 超时覆盖**整个扫描过程**而非单次 API 调用（原计划已如此，补一条跨区域超时测试）

### 6.4 安全边界不得只靠 grep

保留 `test_source_contains_no_write_or_inject_apis`，但**它不是唯一防线**。
`test_reader_requests_only_read_permissions` 已在运行时断言实际请求的权限位；
新增断言：`OpenProcess` 被调用的**次数与参数**全程不变，且不存在任何
第二次以更高权限重开的尝试。

---

## 7. Task 7 — `keystore.py`

### 7.1 损坏文件不得当作空存储（**数据丢失风险**）

原计划的 `test_corrupt_store_file_is_treated_as_empty` 断言「损坏即视为空、
save 可自愈」。**这是错的**：它会在 JSON 损坏时静默抹掉其他账号的密钥。改为：

```python
class KeyStoreCorrupt(RuntimeError):
    """keys.json 存在但无法解析。消息必须给出恢复指引，不得包含文件内容。"""
```

- `load()`：文件不存在 → `None`；文件存在但解析失败 → 抛 `KeyStoreCorrupt`
- `save()`：文件存在但解析失败 → 抛 `KeyStoreCorrupt`，**绝不覆盖**
- `clear()`：同上
- 恢复指引文案：告知用户文件路径、建议手动重命名为 `.bak` 后重试，
  并说明这只会丢失缓存的密钥（可重新扫描），不会丢失聊天记录

`version` 字段不匹配（来自更新版本）同样按 `KeyStoreCorrupt` 处理，
不再静默视为空——原计划的 `test_store_file_with_unknown_version_is_treated_as_empty`
相应改写。

### 7.2 坏条目隔离

单个条目的 base64 非法、字段缺失、类型错误 → 该条目在 `entries()` 中
标记为 `corrupt=True` 且 `load()` 对它返回 `None`，但**不影响其他账号**。
校验项：uin 为 5–12 位数字串、`blob` 可 base64 解码、`fingerprint` 为
8 位十六进制、`saved_at` 可解析为 ISO 时间。

### 7.3 唯一临时文件名

`_write_raw` 改用 `tempfile.mkstemp(dir=parent, prefix=name + ".", suffix=".tmp")`
生成唯一名，写完 `os.replace`；任何异常路径都要 `finally` 清理临时文件。
新增并发 save 测试（两个线程同时 save 不同 uin，最终两条都在，无 `.tmp` 残留）。

### 7.4 DPAPI 的保护边界要写清楚 — 部分接受 ACL 加固

**接受：** 写入 `keys.json` 时使用 `os.open(..., 0o600, ...)` 语义
（Windows 上效果有限，但属廉价纵深防御），并在 `DISCLAIMER.md` 增补一段
明确边界：DPAPI 把密钥绑定到**当前 Windows 用户 + 当前设备**；
`keys.json` 本身若被复制到别的机器或其他用户下，DPAPI 密文无法解开。

**驳回：** 通过 ctypes 操纵 DACL 或调用 `icacls` 做完整 ACL 加固。
理由：`%LOCALAPPDATA%` 本身已是每用户目录，标准用户之间默认不可读；
DPAPI 已使文件内容在无对应用户密钥时毫无价值。为边际收益引入
ACL 操作代码，其自身出错风险（把用户锁在自己的文件外）高于收益。
列为 P2 可选加固。

### 7.5 无口令本体的断言范围扩大

除磁盘内容、异常、`repr`、`redacted_dump` 外，新增断言：
`KeyStoreCorrupt` 的消息、`load()` 失败时的日志、以及 `entries()` 对
损坏条目的描述中，都不含口令本体与 blob 原文。

---

## 8. Task 8 — `decrypt.py`

### 8.1 三态状态（落实 §0.1）

`DecryptResult` 增加 `status: str`，按下表判定（**任一条件命中即降级**）：

| 条件 | status |
|---|---|
| `corrupt_pages > 0` | 至多 `lossy` |
| `wal_reason == "wal_absent"` 且源库确实无 `-wal` | 不降级（合法状态） |
| WAL 存在但 `plan.valid is False` | 至多 `lossy`，`notes` 写明「主库回退版」 |
| `wal_signature_changed` 或 `stopped_early` | 至多 `lossy` |
| `wal_corrupt_frames > 0` | 至多 `lossy` |
| `integrity_check != "ok"` | `failed` |
| 产物页 1 缺 SQLite 魔数 | `failed` |
| 以上皆无 | `complete` |

`decrypt_file` / `decrypt_database` 在 `status == "failed"` 时**仍返回结果对象**
（供诊断），但必须删除产物文件；调用方据 `status` 决定是否继续。

### 8.2 唯一 partial 名与清理

partial 名改为 `<dst>.<pid>.<8位随机>.partial`，写在 dst 同目录（保证同卷，
满足 `os.replace` 原子性）。清理路径覆盖：partial、快照的 db/wal/shm、
以及本次运行创建的唯一临时目录。新增故障注入测试（见 §0.6）：
在 `decrypt_pages` 写到第 N 页时抛异常，断言：

- 已存在的旧最终产物字节未变
- 无 `.partial` 残留
- 源目录 identity 未变

### 8.3 目标已存在时的行为

默认**原子替换**（`os.replace`）：旧产物在新产物完成前保持可用。
新增 `--no-overwrite` 语义的参数 `overwrite: bool = True`；为 `False`
且目标已存在时抛 `DecryptError`，不做任何写入。

### 8.4 内存占用

`decrypt_pages` 已是流式。新增测试断言峰值内存不随库大小线性增长：
用一个 200 页的合成库，配合 `tracemalloc` 断言峰值增量远小于库体积
（阈值取 8 MB，即约 2 页的缓冲余量）。

### 8.5 真机验收提高

Task 12 的 live 测试必须断言 `integrity_check == "ok"` **且**
`c2c_msg_table` / `group_msg_table` 可 `SELECT COUNT(*)`。
「能被 sqlite3 打开」不等于「消息完整」。

**关于「要求 0 个损坏页」：部分接受。** 调研显示较大的
`group_msg_table` 库常有损坏页，硬性要求 0 可能让验收永远无法达成。
裁定：`corrupt_pages == 0` 是**目标**；若 > 0，`status` 必须为 `lossy`，
验收结论记为「部分恢复」并列出损坏页占比，**不计入「标准明文库」的完整通过**，
但也不自动判定 P0 失败——由用户在 Task 13 据占比决定。

---

## 9. Task 9 — `report.py` + `doctor.py`

### 9.1 检查表是实现与测试共用的唯一规则源

在 `doctor.py` 顶部以模块级常量写出 11 项检查的规格表，实现与测试都引用它：

```python
CHECK_SPECS: tuple[CheckSpec, ...]   # CheckSpec(index, name, inputs,
                                     #   pass_when, warn_when, fail_when,
                                     #   side_effects, skips_on_fail)
```

### 9.2 doctor 默认只读、不留数据

解密探测的 `work_dir` 默认改为 `tempfile.TemporaryDirectory()`，
并在 `finally` 中清理（含关闭 SQLite 连接）。**不得**默认写入
`config.work_dir(uin)`。`keep_output=True` 时才落到调用方指定的目录。

### 9.3 容量预估并入第 9 项检查

不新增第 12 项（避免全量重编号）。在检查 9「解密与页 1 魔数」**之前**
预估所需工作空间：`(主库 size + wal size) × 3`（源快照 + 明文产物 + 余量），
与目标卷可用空间比较。不足则该项判 `warn` 并给出所需/可用的**字节数**
（不展示完整路径）。估算必须按实际文件大小算，不得把设计文档里的
335 MB 写成常数。

### 9.4 状态映射收紧

| 情况 | 状态 |
|---|---|
| QQ 未运行但有可用缓存密钥 | `warn` |
| QQ 未运行且无缓存密钥 | `fail`（无法继续） |
| 口令验证失败 / page-1 无法认证 | `fail` |
| 数据库损坏（`integrity_check != ok`） | `fail` |
| WAL 缺失 | `warn` |
| `status == "lossy"` | `warn` 且 detail 写明降级原因 |

### 9.5 JSON 增加 schema version 与脱敏

`DoctorReport.to_json()` 增加 `"schema_version": 1`。所有路径字段输出为
`<Documents>\Tencent Files\****8588\nt_qq\nt_db\nt_msg.db` 形式
（uin 掩码、用户目录用占位）。新增测试断言 JSON 文本中不含完整用户路径
与完整 uin。

### 9.6 不得吞掉用户中断

`_run` 捕获 `Exception`，**不得**捕获 `BaseException`。新增测试：
某项检查抛 `KeyboardInterrupt` 时必须向外传播，而不是变成一条 `fail`。

### 9.7 承接 Task 2 遗留义务：盘符扫描截断信号

Task 2 的 `discover_accounts` 按控制器裁定保持 `-> list[QQAccount]` 签名，
因此当盘符扫描**被 `MAX_VISITED_DIRS` 截断但仍找到了账号**时，截断这一事实
无处上报（`ScanAborted` 只在「截断且零结果」时抛）。

Task 9 的 doctor 必须补上这一环：`find_tencent_files` 是公开函数且返回
`(hits, truncated)`，当 doctor 在 `scan_drives=True` 路径下工作时，
**必须直接调用它并把 `truncated` 传进检查 3 或检查 4 的 detail**，
文案形如「盘符扫描在第 N 个目录处达到上限而中止，可能未列出全部账号」。
默认路径（不扫盘）不受影响。

若 Task 9 的实现者认为这需要改 `discover_accounts` 的签名，停下来报告，
不要自行改——那是控制器的裁定范围。

---

## 10. Task 10 — `cli.py`

### 10.1 移除密钥位置参数

`qqexport key set <口令>` 的位置参数形式**删除**。理由：会进入 shell history、
`ps` 输出与诊断采集。改为只支持：

- TTY 交互：`getpass.getpass()`（不回显）
- 管道/自动化：`--from-stdin`（读一行，读完即弃，不落临时文件）

这**修改了 spec §9.1**，需同步更新 spec（见 §14）。
原计划的 `test_key_set_positional_warns_about_process_list` 改为
`test_key_set_rejects_positional_passphrase`，断言 argparse 以退出码 2 拒绝。

### 10.2 getpass 的非 TTY 回退

`getpass` 在无法关闭回显时会退化为读 stdin 并发 `GetPassWarning`。
处理：捕获该 warning，向 stderr 打印「当前终端无法关闭回显，输入会被显示；
建议改用 `--from-stdin` 或在真实终端中运行」，然后**中止**而不是继续读取。
新增测试用 `warnings.catch_warnings(record=True)` 断言该行为。

### 10.3 非 TTY / 编码路径

`_force_utf8_stdio` 的 `reconfigure` 失败必须安全回退（不得抛异常终止命令）。
新增测试：

- stdout 被替换为无 `reconfigure` 方法的对象 → 命令仍正常完成
- stdout 为非 TTY 管道 → 输出不含 ANSI 转义
- `--ascii` 输出严格可 `.encode("ascii", "strict")`
- 模拟 cp936 编码的 stdout（`io.TextIOWrapper(..., encoding="cp936")`）
  → 默认模式不得抛 `UnicodeEncodeError`

### 10.4 多账号不得默认第一个

所有需要账号的子命令（`key scan` / `key set` / `key clear` / `doctor`）
经 `discover.select_account` 取账号。歧义时打印带掩码的候选列表并以退出码 1
结束，提示用 `--uin` 指定。

### 10.5 console script 实机验证

Task 10 的出口条件增加：在**干净 shell** 中执行 `qqexport version` 与
`qqexport key show`，确认 entry point 真的解析到 `qqexport.cli:main`
且退出码符合 0/1/2 约定。Task 1 的 editable install 成功不能替代此项。

### 10.6 顺手改 pyproject

按 §1 增加 `tmp_path_retention_policy = "none"`。

---

## 11. Task 11 — fixture 采集

### 11.1 密钥不得经环境变量（落实 §0.4）

删除原 Step 7 那条把口令塞进 `QQEXPORT_TEST_PASSPHRASE` 的 shell 命令。
改为：测试进程内直接 `KeyStore().load(uin)`；无缓存则 `pytest.skip`
并提示先运行 `qqexport key scan`。`test_fixture_real_page1.py` 中所有
依赖口令的测试统一走一个 module 级 fixture 取密钥。

### 11.2 采集前必须审查全部可读字符串

`capture_fixtures.py` 的摘要不得只打印前 96 字符与「可打印串前 400 字符」。
改为打印页 1 解密后的**全部**可打印字符串（按 `\x00` 与不可打印字符切分，
逐条列出），并明确提示：其中若出现账号、路径、昵称或任何自定义字段名，
必须取消。人工确认输入从 `yes` 改为要求输入 `I HAVE REVIEWED THE STRINGS`
（提高误按回车通过的门槛）。

### 11.3 provenance.json 最小化

只保留复现所必需的信息，避免形成设备指纹：

| 字段 | 处理 |
|---|---|
| `captured_at` | 粗化到**日期**（不含时分秒） |
| `qq_version` | 保留（兼容性必需） |
| `source_size_bytes` | **删除**，改为 `source_page_count` 与 `has_wrapper_header: true` |
| `captured_bytes` | 保留（恒为 5120） |
| `cipher_fingerprint` | 保留 |
| `key_fingerprint` | **删除** |
| uin / 路径 | **绝不记录** |

相应更新 `test_provenance_records_no_passphrase` 的 allowlist。

### 11.4 提交前的 staged 核验

Step 8 增加显式核验命令，且必须人工看过输出：

```bash
git diff --cached --stat            # 只应有 2 个文件
git diff --cached --numstat         # .bin 应显示为二进制（- -）
ls -l tests/fixtures/               # .bin 必须恰为 5120 字节
```

断言 staged 集合中不含 `*.db`、`*.db-wal`、`*.db-shm`，且 `.bin` 恰为 5120 字节。

### 11.5 无法确认就保持 skip

不得为了让测试变绿而降低隐私门槛。fixture 缺失时测试保持 SKIP，
并在 Task 13 的验收表中记为「未验证」。

---

## 12. Task 12 — live 冒烟

### 12.1 账号选择

module 级 `account` fixture 改为：优先 `Config.load().uin`；未配置时若
恰好发现一个账号则用它；多于一个则 `pytest.skip` 并提示先
`qqexport doctor --uin <uin>` 或写入配置。**失败信息不得打印真实 uin**，
一律用 `****` + 后 4 位掩码。

### 12.2 WAL 恢复必须给出结论性证据（**核心修正**）

原断言 `ts_with >= ts_without` 允许相等，而相等无法区分
「WAL 没有新内容」与「WAL 回放根本没生效」。**必须改为按 commit 边界直接比对页内容：**

```python
def test_live_wal_recovery_applies_committed_frames(...):
    # 1. 取一次快照，得到 (db_copy, wal_copy, shm_copy) 三件套
    # 2. plan = inspect_wal(wal_copy, shm_copy)
    # 3. 若 plan.last_commit_frame == 0 → pytest.skip("本次没有可验证的 WAL 更新")
    # 4. 对同一份快照做两次解密：一次 apply_wal，一次不 apply
    # 5. 逐帧取 plan 中已提交帧的 pgno，断言两份产物在该页的字节**不同**
    #    （若相同，说明该帧的内容恰好与主库一致，需换一个 pgno 继续；
    #     所有已提交帧都相同才允许 skip，并说明原因）
    # 6. 断言 with-WAL 产物的页数 == plan.commit_size
```

对**同一份快照**做两次解密，可同时消除「QQ 在两次解密之间写了新 WAL」
造成的混淆（见 §12.4）。

### 12.3 `_source_fingerprint` 改用相对路径

原实现以 `p.name` 为键，同目录树下同名文件会互相覆盖。改为
`str(p.relative_to(account.db_dir))`。

### 12.4 确定性测试改为对同一快照重复解密

原 `test_live_decrypt_is_deterministic` 调用两次 `decrypt_database`，
每次都重新快照——QQ 在两次之间写库会导致产物不同，把「源变化」误判为
「解密不确定」。改为：快照一次，对同一份快照解密两次，比较结果。

### 12.5 大文件不得整体载入内存

- 读魔数：`with open(db,"rb") as f: f.read(16)`，不得用 `read_bytes()[:16]`
- 确定性比较：流式 SHA-256（分块 1 MB），不得两次 `read_bytes()` 后比较
- 新增测试断言这些辅助函数在 200 MB 输入下的峰值内存有界（`tracemalloc`）

### 12.6 明文产物必须确实清理

全部 live 测试改用 `tempfile.TemporaryDirectory()`，并在 fixture 的
`yield` 之后显式关闭所有 SQLite 连接。新增一条 teardown 断言：
上下文退出后目录不存在。清理抛 `PermissionError` 时测试判失败，不静默。

### 12.7 只读性验证分两种条件

- **QQ 运行中**：断言「无文件被删除」；出现新增/内容变化则记为
  `SKIP(未验证)`，并在验收表中如实呈现
- **QQ 完全退出**（用户手动配合）：断言 identity 三元组
  （size / mtime_ns / file index）逐一不变，这才是 `PASS`

两种条件的结果分别记录，不得混为一谈。

### 12.8 指纹断言放宽

按 §3.2，断言指纹 ∈ 八组合，记录实际值，与预期不符时 WARN 而非 FAIL。

---

## 13. Task 13 — 验收与收尾

### 13.1 notes 只存脱敏摘要

不得把 doctor 的完整输出粘进 notes。写入前先审查是否含路径、uin、
表名以外的指纹信息；只保留：QQ 版本、Python 版本、cipher 指纹、
页数、损坏页数、WAL 帧数与最后 commit 帧号、各检查项的 PASS/WARN/SKIP。

### 13.2 新增交付物：兼容矩阵

创建 `docs/compat-matrix.md`，每行一次验证记录，字段：

| Windows 版本/架构 | Python 版本 | QQ 版本 | wrapper 形状 | cipher 指纹 | WAL 状态 | 检查结论 |
|---|---|---|---|---|---|---|

**不得**记录 uin、完整用户路径、昵称、消息样本。
发现新版本不兼容时，用户应得到「未验证 / 暂不支持该格式」的诊断，
而不是误导性的「口令错误」。

### 13.3 P1 探测用 TemporaryDirectory

原 Step 3 的 heredoc 脚本改为使用 `tempfile.TemporaryDirectory()`，
`sqlite3` 连接在 `with contextlib.closing(...)` 中关闭，退出上下文即清理。
删除 Step 4 的 `rm -rf work-p1-probe`（Windows 上不稳定且路径不在 ignore 范围内）。

### 13.4 doctor JSON 走系统临时目录

原 Step 2 的 `/tmp/doctor.json` 在 Windows 上不稳定。改用
`tempfile.NamedTemporaryFile`，解析后只打印摘要并删除原文件。

### 13.5 新增发布检查门

1. `python -m build`（需在 dev extra 中加 `build`）产出 sdist 与 wheel
2. 检查 wheel 内容：含 `qqexport` 全部子模块，**不含** `tests/`、
   `tests/fixtures/`、任何 `*.db`、`keys.json`、`.superpowers/`
3. 在仓库目录**之外**创建干净 venv，从 wheel 安装
4. 运行 `qqexport version` 与 `qqexport key show`（无扫描、无真实数据的安全冒烟）
5. 退出码符合 0/1/2 约定

Python 3.12 最低版本的那一腿**延后到 CI**：本机只有 3.14，
为 P0 单独装一个 3.12 属范围外。在 notes 中记为待办。

### 13.6 验收表的阻断规则

| 门槛 | 要求 |
|---|---|
| 普通测试 | 全 PASS（`live` 除外） |
| doctor 关键检查 | 无 `fail` |
| SQLite 可查询性 | `quick_check == ok` 且关键表可 `SELECT COUNT(*)` |
| WAL 恢复 | 有结论性证据（§12.2）→ PASS；无可验证 commit → **未验证** |
| 源文件未被修改 | QQ 退出条件下 identity 不变 → PASS；QQ 运行中 → **未验证** |
| 重复解密确定性 | 同一快照两次解密字节一致 |
| 明文/密钥残留 | 无 `.partial`、无遗留临时目录、`git status` 干净 |
| staged/untracked 隐私 | 按 §0.5 扫描通过 |

**只有以上全部为 PASS，才能把 README 更新为「P0 已完成」。**
存在任何 SKIP / 部分恢复 / WAL 未验证时，README 必须写成
「P0 部分验收（待人工验证项：…）」。

---

## 14. 需要同步修改 spec 的条目

本修正案改变了 spec 的以下已确认内容，须回写
`docs/superpowers/specs/2026-10-23-qq-chat-history-export-design.md`：

| spec 位置 | 原内容 | 改为 |
|---|---|---|
| §9.1 | `qqexport key set <口令>` | 移除位置参数形式，只保留交互式与 `--from-stdin`，并注明理由 |
| §6.6 | 「一致性快照」 | 「尽力获取一致快照」，并补 §5.1 的四重校验与 `consistency` 三态 |
| §6.7 / §11 | `DecryptResult` 只有 warnings | 增加 `status ∈ {complete, lossy, failed}` 及 §8.1 的判定表 |
| §9.3 | 检查 15「剩余空间」属 P1+ | 容量预估并入 P0 的检查 9（不新增第 12 项） |
| §10.3 | 验收第 7 条「源目录 mtime 无变化」 | 区分 QQ 运行中（未验证）与 QQ 退出（PASS）两种条件 |

---

## 15. 执行顺序调整

采纳评审建议的节奏，把最高风险的纯算法件提前：

| 阶段 | 任务 | 出口条件 |
|---|---|---|
| 0 | Task 1 ✅ | 已完成 |
| 1 纯核心 | **Task 3** | KAT + round-trip + 全组合 + 错口令测试通过 |
| 2 发现与 WAL | **Task 2 → Task 4** | 多账号/错误分类确定；WAL 只应用有效 commit |
| 3 系统边界 | **Task 5 → Task 6 → Task 7** | 快照一致性定义可证明；扫描显式 opt-in；KeyStore 损坏语义确定 |
| 4 集成 | **Task 8** | 三态状态、唯一临时名、清理、SQLite 检查可解释 |
| 5 界面 | **Task 9 → Task 10** | doctor 状态映射稳定；CLI 无位置参数密钥；JSON 脱敏 |
| 6 真机 | **Task 11 → Task 12** | fixture 人工审查通过；WAL 有结论性证据；只读性可复核 |
| 7 收尾 | **Task 13** | 关键门槛全 PASS；发布检查通过；无明文/密钥残留 |

与原 DAG 的唯一实质差别：Task 3 提到 Task 2 之前（Task 3 是全项目风险最高的
纯算法件，且被 Task 4/5/8 依赖；先做它能最早暴露常量理解错误）。
依赖关系本身未变，Task 5 仍必须在 Task 4 之后。
