# 交接说明 —— QQ 聊天记录导出工具 P0

**最后更新：** 2026-10-28
**当前分支：** `p0-decryption-pipeline`
**进度：** P0 的 13 个 task **代码全部落地**；普通测试 **381 passed / 8 skipped**
（8 项 skip 全是缺真库页 1 fixture），真机 live 冒烟 **9 passed**。
**结论：P0 部分验收** —— 仅剩两项人工/环境待办：真库页 1 fixture 的人工审查与
`git add -f`、Python 3.12 的 CI 验证。详见 `docs/compat-matrix.md`。

Task 6–10 提交：`4661776`（keyscan）、`ca2c4c8`（keystore）、`98340ce`（decrypt）、
`a0b0b20`（report+doctor）、`cf3f73e`（cli）；Task 11–13：`6161ed6`（fixture 工具）、
`8ac7223`（python -m 入口）、`5bc3603`（keyscan 性能）、`8595969`（R34 reserved bytes）、
`a948dd3`（live 冒烟）。隐私扫描新增全库巡检入口（`--audit`），文档里的真实用户名
残留已清除（`356f802`、`267a0e5`）。

**主链路已实证可用**：discover → keyscan → keystore → snapshot → decrypt →
doctor/cli。真机报告：doctor 11/11、`quick_check == ok`、消息表可查询、
WAL 回放有页级证据、同一快照两次解密一致、源目录 identity 未变、wheel 在
干净 venv 中安装并跑通。

这份文档是给接手者的**唯一入口**。先读完它，再读它指向的三份规范文档。

---

## 1. 一分钟了解现状

正在做的事：一个 Windows 桌面工具，把用户**自己账号**在本机 QQ NT 客户端里的
聊天记录导出成 txt / html / pdf。P0 阶段只做「解密链路」——产出能被
stdlib `sqlite3` 打开、且**含最新消息**的明文数据库。消息解析与导出是 P1+，
其计划**尚未编写**（理由见 §9）。

已落地并可用的模块：

| 模块 | 作用 | 状态 |
|---|---|---|
| `qqexport/config.py` | 应用目录与配置，`QQEXPORT_HOME` 可整体重定向 | ✅ |
| `qqexport/core/sqlcipher.py` | SQLCipher 4 页级解密、参数八组合探测 | ✅ 有四层 KAT |
| `qqexport/core/discover.py` | 定位数据目录、枚举账号、`select_account` | ✅ |
| `qqexport/core/wal.py` | WAL 帧校验、commit 边界、按事务原子回放 | ✅ |
| `qqexport/core/snapshot.py` | db/wal/shm 成组复制 + 四层一致性校验 | ✅ |
| `qqexport/core/keyscan.py` | 只读内存扫描取口令；纯计算与 Win32 分离 | ✅ 21/21 变异杀灭 |
| `qqexport/core/keystore.py` | DPAPI 口令缓存；损坏文件拒绝覆盖、坏条目隔离 | ✅ 13/13 变异杀灭 |
| `qqexport/core/decrypt.py` | 快照→解密→WAL 回放，三态状态 + 原子输出 | ✅ 18/18 变异杀灭 |
| `qqexport/report.py` | 报告数据结构、ASCII 退路、JSON schema_version | ✅ |
| `qqexport/doctor.py` | 11 项全链路自检，独立容错、脱敏、临时目录 | ✅ 20/20 变异杀灭 |
| `qqexport/cli.py` | version / doctor / key 子命令，cp936 与多账号保护 | ✅ 10/10 变异杀灭 |
| `tools/check_privacy.py` | 提交前扫描 + `--audit` 全库巡检 | ✅ 已自检 |

**不能端到端跑通的地方**：只剩 Task 11–13 的验收环节（真实 fixture、live 冒烟、
发布检查）。日常测试（374 项）不依赖真实 QQ，可在任何 Windows 机器跑。

---

## 2. 三份规范文档及其优先级

**冲突时优先级从高到低：**

1. **修正案 A** — `docs/superpowers/plans/2026-10-24-p0-amendment-A.md`
   外部评审后的规范性修正，含裁定 R18–R26。**与下面两份冲突时以它为准。**
2. **spec 设计文档** — `docs/superpowers/specs/2026-10-23-qq-chat-history-export-design.md`
   项目的 authority，含全部实测基线（§3）、加密参数（§6.4）、字段映射（§7.1）、
   导出格式（§8）、测试策略（§10）、风险表（§12）。
3. **P0 实现计划** — `docs/superpowers/plans/2026-10-24-qq-export-p0-decryption-pipeline.md`
   13 个 task 的逐步 TDD 说明，每步含可粘贴的完整代码。**5833 行，不要通读**，
   只看你要做的那个 task。

另有一份执行台账（**git 忽略，不会进仓库，换机器就没了**）：
`.superpowers/sdd/2026-10-24-qq-export-p0-decryption-pipeline/progress.md`
里面是每个 task 的评审结论、裁定理由、延后 minor 清单。若在，务必读。

---

## 3. 环境事实（已实测，别重新发现）

| 项 | 值 |
|---|---|
| OS | Windows 10/11 x64，`win32` |
| Shell | Git Bash（`C:\Program Files\Git\bin\bash.exe`） |
| Python | **3.14.6**，`python` 在 PATH；pip 26.1.2 |
| 已装依赖 | `cryptography` 50.0.2、`pytest` 9.1.1（由 `pip install -e ".[dev]"` 装入） |
| QQ 客户端 | `C:\Program Files\Tencent\QQNT\`，版本 **9.9.36-53644** |
| 数据目录 | `<文档已知文件夹>\Tencent Files\<uin>\nt_qq\` |
| **文档目录被重定向到 D 盘** | `%USERPROFILE%\Documents` **不存在**，真实位置在另一个盘。必须用 `SHGetKnownFolderPath` 定位，`discover.py` 已正确处理 |
| 消息库 | `nt_db\nt_msg.db`，实测 369,099,776 B（会随使用增长），满足 `size % 4096 == 1024` |
| WAL | `nt_msg.db-wal`，采样时 ~1.9 MB 且活跃写入 —— **最新消息在里面** |
| `-shm` | 32768 B |
| 媒体 | `nt_data\` 下 Pic 510 MB / Video 124 MB / File 22 MB / Ptt 2.7 MB |
| PDF 引擎 | `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`，156.x |
| 控制台代码页 | 常为 **cp936（GBK）**，直接 print emoji 或中文会 `UnicodeEncodeError` |
| `gh` CLI | 在 `C:\Program Files\GitHub CLI\gh.exe`，**不在 Git Bash 的 PATH 里**，已登录 |
| 远程 | `origin` = GitHub，仓库 **PUBLIC** |

**控制台乱码不是 bug。** Git Bash 里 Python 输出中文常显示为乱码，
但文件内容是正确的 UTF-8。判断成败要看退出码和数字，不要看中文显示。

---

## 4. 代码规范（从已有 5 个模块归纳，不是新发明）

### 4.1 文件与结构

- 每个模块顶部 `from __future__ import annotations`
- **模块 docstring 用中文**，必须写清：这个模块解决什么问题、
  关键不变量是什么、引用 spec/修正案的哪一节。参照 `wal.py` 与 `snapshot.py` 的写法。
- 常量 `UPPER_SNAKE` 集中在模块顶部，紧跟一行注释说明来源
  （加密常量必须注明「参照 spec §6.4，不得凭记忆改动」）
- 私有辅助函数前缀 `_`
- **不用 logging。** 全项目刻意没有日志层；诊断信息通过返回值（`notes` /
  `warnings` 列表）与 `report.py` 传递
- 不做超出 task 范围的抽象。YAGNI 是硬要求，过度设计会在评审中算缺陷

### 4.2 类型与数据类

- 全部函数带类型标注，用 `X | None` 而非 `Optional[X]`
- 值类型用 `@dataclass(frozen=True)`；需要就地累加的结果对象用普通 `@dataclass`
  并给字段默认值（参照 `WalApplyResult`、`DecryptResult`）
- **已冻结的数据类字段集不得增删重排**（`WalPlan` 8 字段、`WalApplyResult` 7 字段、
  `SnapshotResult` 8 字段）。要改必须走修正案，因为下游 task 按位置构造

### 4.3 错误处理

- **单点异常不得中止整体流程。** 页 HMAC 失败 → 跳过并计数；WAL 帧断裂 →
  应用到断裂前最后 commit；快照校验失败 → 重试；单个联系人查不到 → 兜底串
- 异常消息**不得包含**：口令、原始页二进制、数据库绝对路径、真实 uin、昵称、消息正文
- 只读的 Win32 调用要区分「权限拒绝」与其它失败（参照 `keyscan` 的设计：
  `GetLastError() ∈ {5, 1314}` → 专用异常，其余静默跳过）
- Windows 专用代码用 `if os.name != "nt": return <降级值>` 早退，
  使模块在非 Windows 上仍可导入、仍可被单测

### 4.4 安全红线（写进代码，不只是写进文档）

- **绝不修改 QQ 数据目录下的任何文件。** 对源文件只允许 `open(path, "rb")`。
  全项目唯一例外：`snapshot.shm_lock` 以 `"r+b"` 打开 `-shm` 取字节范围锁，
  **写零字节**，且有测试断言内容不变
- 内存扫描只申请 `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`。
  `keyscan.py` 里**不得出现** `CreateRemoteThread`、`WriteProcessMemory`、
  `DebugActiveProcess`、`VirtualAllocEx`、`SetWindowsHookEx`、`VirtualProtectEx`、
  `PROCESS_ALL_ACCESS`。有测试直接 grep 源文件断言这一点，别删那条测试
- 口令本体永不进入日志、异常、报告、stdout、JSON。只允许 SHA-256 指纹前 8 位
- 大文件一律流式（`COPY_CHUNK = 1 MiB`）。主库 369 MB，
  **任何 `read_bytes()` 整库载入都是缺陷**；需要比对整库用流式 SHA-256

---

## 5. 测试规范

### 5.1 分层与 marker

| marker | 含义 | 默认是否运行 |
|---|---|---|
| 无 | 单元测试，合成数据，任意平台 | ✅ |
| `win` | 需要 Windows API（合成文件 + 可控假对象） | ✅（本机是 Windows） |
| `fixture` | 需要 `tests/fixtures/` 下人工确认过的真库样本 | 缺文件则 skip |
| `live` | 需要本机实时运行的 QQ | ❌ 被 `addopts = '-m "not live"'` 排除 |

`pyproject.toml` 已设 `tmp_path_retention_policy = "none"`。

### 5.2 鉴别力是硬要求（这个项目栽过三次）

**每一个测试都必须满足：把它声称要验证的那段实现删掉，测试就会失败。**

已经出过的三起事故：
1. `test_drive_scan_prunes_noisy_dirs` 的噪声目录是空的，删掉 `_PRUNE` 照样通过
2. `test_inspect_stops_at_recycled_frame_salt` 删掉 salt 比较照样通过
   （陈旧 WAL 代的头 seed 不同，累计校验和会先拒绝该帧）——**salt 检查一度零覆盖**
3. 四个 `-shm` 回退测试都断言 `shm_validated is False`（默认值），
   把 `_read_shm_state` 打桩成 `return None` 时它们**全部通过**。
   整个变异杀灭只靠那一个正向测试

**做法：** 写完测试后做**变异验证**——真的把对应实现改掉/删掉，跑测试，
确认失败，再还原。控制器已多次亲手做这件事（`cp` 备份 → 改 → `pytest` → 还原 →
`git diff --stat` 确认为空）。报告里要写清每个测试的鉴别力论证。

### 5.3 hermetic（测试不得依赖真机状态）

- 只用 `tmp_path` 下的合成文件
- 凡是会调用 `discover` 的测试，必须 monkeypatch 掉
  `documents_folder` / `_registry_personal` / `USERPROFILE` / `_drive_roots`，
  否则真机会经真实注册表命中 D 盘的 `Tencent Files`，
  使断言失败**并把真实 uin 打进失败输出**
- 不要在自动化测试里锁/读运行中 QQ 的文件（裁定 R23）

### 5.4 隐私红线

- **仓库中不得出现**真实聊天内容、真实昵称、真实 QQ 号、真实媒体文件、
  含用户名的绝对路径
- 批准的合成 uin allowlist：`123456789` `11111111` `22222222` `33333333`
  `44444444` `55555555` `66666666` `77777777` `88888888` `99999999` `12345`
- 批准的合成口令：`#8FakeKeyForTest` `#8WrongKeyXXXXXX` `#8AnotherKeyXXXX`
  `A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6`
- 路径里的用户名一律写 `<user>` 或 `<Documents>`；uin 掩码规则
  `"****" + uin[-4:]`（`discover.mask_uin`）
- 真机命令的输出**不要粘进任何入库文件**（含 notes、报告、README）。
  需要留档时按脱敏规则处理
- `tests/fixtures/*.bin` 默认被 `.gitignore` 排除，需人工审查后用 `git add -f`

### 5.5 明文产物清理

**pytest 的 `tmp_path` 默认保留最近 3 次运行的目录，不是清理保证。**
凡会产生**真实**明文库的测试（Task 12 全部、Task 9 的 doctor 探测、
Task 13 的 P1 探测），一律用 `tempfile.TemporaryDirectory()`，
并在退出上下文前关闭所有 SQLite 连接与文件句柄——
Windows 上句柄未关会让清理抛 `PermissionError`。
不得用固定目录名（如 `work-p1-probe`），它不匹配 `.gitignore` 的 `work/` 规则。

---

## 6. 分支与提交规则

### 6.1 分支

- 工作分支 **`p0-decryption-pipeline`**，从 `main` 的 `806fdff` 切出
- **不要在 `main` 上直接提交**
- `origin/main` 目前停在 `806fdff`（只有 README），P0 完成并通过验收后才合并
- **不要 force-push。** 仓库是 PUBLIC，且用户未授权重写已推送的历史

### 6.2 提交

- **一个 task 一个 `feat:` 提交**；修复轮用 `fix:`；文档用 `docs:`；
  纯测试用 `test:`；杂项用 `chore:`
- 提交信息**用中文**。subject 一行说清做了什么，**body 解释为什么**——
  尤其是「哪条实测/评审推翻了原来的设计」。参照 `fe586aa`、`220d72d` 的写法
- 用 heredoc 传消息，避免引号与换行问题：
  ```bash
  git commit -q -m "$(cat <<'EOF'
  feat: 一句话主题

  正文解释为什么，不是重复 diff 里看得到的东西。
  EOF
  )"
  ```
- **每次提交前必须跑** `python tools/check_privacy.py`，通过才提交。
  默认扫描范围是 **staged + 未跟踪文件**（`git ls-files --others --exclude-standard`）——
  覆盖未跟踪文件是必需的：本项目真实踩过一次，新建的文档里写了真实标识符，
  因为还没 `git add`，只看 staged 的旧版扫描器报了 OK。
  它检查：引号内 5–12 位纯数字（uin 形状）、`Users\<name>\` 与 `/home/<name>/` 路径、
  数据文件后缀、密钥上下文中的 16/32 字节字面量。
  带 `--check-author` 可额外查提交者邮箱，`--range A..B` 可扫指定提交范围
- 扫描器有误报就改扫描器的规则（收紧到只报高置信度形状），**不要绕过扫描器**
- 除了默认的「staged + 未跟踪」扫描，另有只读全库巡检
  `python tools/check_privacy.py --audit`：清历史遗留泄漏，与默认扫描共用规则。
  它已经抓出过计划书里的真实用户名（转义双反斜杠写法曾绕过旧规则）。

### 6.3 推送

- **评审通过后推**，不是每次 commit 都推。用户已明确此策略
- **不装 post-commit hook。** 公开仓库下 hook 会让任何错误即时永久公开，
  没有拦截窗口——这个项目已经真的发生过一次（见 §7.1）
- 推送命令：`git push`（分支已设 upstream）

### 6.4 已知的历史瑕疵（合并时用 rebase 修）

- `b1f9e08` 的 subject 误用了上一条的文案（正文正确）
- `d214bec` 与 `fe586aa` subject 重复（前者是实现者的，后者带控制器裁定）

都已推送，按规矩没 force-push。合并到 `main` 时用 interactive rebase 整理。

---

## 7. 隐私：已经发生过一次事故，别再犯

### 7.1 事故复盘

2026-10-24，控制器（上一个 agent）在 pre-flight 阶段把本机实测到的
**真实 QQ 号写进了 35 处**（spec、实现计划、还有已提交的 `tests/test_config.py`），
以及含 Windows 用户名的真实数据目录 6 处——违反了它自己刚写进 spec §10.1 的红线。
是 Task 1 的评审把它标成 ⚠️ 才发现。

因为 5 个受污染提交**一个都没推过**，用 `git reset --mixed 806fdff` 后
按原语义重建为 3 个干净提交解决，GitHub 上从未出现过真实 uin。

**同一个坑当时又踩了两次**：先把真实 uin 当「禁用值示例」写进修正案，
再把真实 Windows 用户名写进扫描器的注释。两处都被扫描器抓到。

教训：**脱敏不是写完再检查，是写的时候就不用真实值。**
需要指代本机账号时写「本机真实 uin」或掩码 `****<后4位>`。

### 7.2 尚未解决的遗留

git author email 的**本地部分就是用户的真实 QQ 号**（形如 `qz<真实uin>@outlook.com`，
此处刻意不写出数字）。它出现在每一个提交里，包括早已公开的 `806fdff Initial commit`。
用 `git log -1 --format='%ae'` 可查看实际值。

- 这是用户自己的 git 身份配置，**不要擅自改 git config**
- 已上报用户，用户未要求处理
- 若用户日后要求彻底处理：改 email + 重写全部历史 + force-push，
  但 `806fdff` 已公开，重写也抹不掉既有的公开记录，实际收益有限
- `tools/check_privacy.py --check-author` 会报这一项，属**已知且已接受**，
  不要为了让它闭嘴而放宽规则

---

## 8. 工作流程（用户明确要求）

### 8.1 不要什么都开 subagent

用户原话：「没有必要什么事情都开 subagent 啊 你这就你自己能干的
又不是什么一边收集资料 或者多个并行任务之类的」。

**判断标准：**
- **自己做**：写单个模块、改代码、跑测试、变异验证、评审自己刚写的 diff、
  记账、提交推送。这些是串行的、需要连续上下文的活，派出去只是多一轮开销
- **才派 subagent**：需要联网收集大量资料（如查 RFC/NIST 向量、
  调研开源实现）、真正可并行的独立调查、或者需要「独立视角」的正式评审关口

本项目早期用了「每 task 一个实现者 + 一个评审者」的 subagent 流程，
质量确实高（评审抓到了 3 个我自己写进计划的真 bug），但慢且贵。
**从现在起默认自己做，只在上述三种情况才派。**

### 8.2 每个 task 的收尾动作（固定五步）

1. 跑 `python -m pytest -q`，全绿且输出洁净
2. 跑 `python tools/check_privacy.py`，必须通过
3. 提交（中文、正确前缀、body 写 why）
4. `git push`
5. 在 ledger 追加：`Task N: complete (commits A..B, ...)` + 裁定 + 延后 minor

### 8.3 裁定要写下来

凡是「计划/修正案说要 A，但实测或评审表明应该是 B」的情况，
**必须**：写一条 `R<编号> —— <裁定>` 进修正案对应小节（含理由与「若裁定错的代价」），
必要时同步改 spec，然后在提交信息里说明。
不要只在对话里说一句就改掉代码——下一个接手的人看不到对话。

已用编号到 **R26**。下一条从 R27 开始。

---

## 9. 剩余任务与待人工项

**Task 1–13 的代码全部落地。** 剩余的是两项**无法自动完成**的验证：

1. **真库页 1 fixture（Task 11 的人工作业）**：真机运行
   `python tools/capture_fixtures.py`，逐条审查打印出的页 1 全部可打印字符串，
   输入 `I HAVE REVIEWED THE STRINGS`；确认后 `git add -f
   tests/fixtures/nt_msg_header_page1.bin tests/fixtures/provenance.json`。
   未采集前 `tests/test_fixture_real_page1.py` 的 8 项保持 SKIP（预期状态）。
2. **Python 3.12**：本机只有 3.14，按修正案 §13.5 延后到 CI。

下面 Task 7–13 的原始说明保留供回溯，接口以源码为准；实现细节、裁定与延后
minor 在 ledger（`.superpowers/sdd/.../progress.md`，本地不入库）。

计划的每个 task 段落都含完整可粘贴代码，但**必须先读修正案对应小节**，
因为修正案改了不少东西。抽取单个 task 的修正案切片：
```bash
python .superpowers/sdd/extract-amendment.py <N>
```
（该脚本在 git 忽略的工作区里；若不在，直接读修正案全文的对应小节。）

生成 task brief：
```bash
bash "<superpowers skill 目录>/subagent-driven-development/scripts/task-brief" \
     docs/superpowers/plans/2026-10-24-qq-export-p0-decryption-pipeline.md <N>
```

### Task 6 — `keyscan.py` 被动内存扫描（✅ 已完成，commit `4661776`）

落地时新增三条裁定 R27–R29（修正案 §6.5）：失败语义分层、`_last_error`
按构造绑定、`KEY_LENGTHS` 顺序不可观测。以下为原始任务说明，保留供回溯。

**做什么：** 只读扫描 `QQ.exe` 进程内存，找 16/32 字节可打印 ASCII 的 C 字符串候选，
逐个拿真库 page-1 HMAC 验证。

**关键设计：** 纯计算与 Win32 调用彻底分离。
`find_key_candidates(blob) -> list[bytes]` 是纯函数（可穷举测试）；
`MemoryReader` Protocol 抽象区域遍历，`Win32ProcessReader` 是它的 ctypes 实现，
测试用假 reader 注入。`scan_with_reader(reader, verify, *, timeout_s, clock)`
的 `clock` 也可注入，使超时测试确定。

**坑：**
- 候选正则必须**最长优先**（32 在 16 之前），否则 32 字节串会被切成两个 16 字节候选。
  区域起始处的候选靠给 blob 前置一个 `\x00` 来命中 lookbehind
- 48 字节连续可打印串**不得**被匹配（其 32 字节后缀前一字节是可打印字符，要拒）
- `OpenProcess` 权限位只能是 `0x0010 | 0x0400`。有两条测试 grep 源文件断言
  不含写入/注入类 API、不含 `0x0020`/`0x0008`，别为了通过而放宽它们
- **驳回过的评审意见（R15）：** 「多 QQ 进程不得默默取第一个验证通过者」——驳回。
  验证是拿**选定账号**的真库 page-1 HMAC 做的，能通过就是该账号的密钥，
  属确定性判定而非启发式。改为用测试把该行为钉住
- 账号选择走 `discover.select_account`，**不得**静默取 `accounts[0]`（修正案 §2.3）

### Task 7 — `keystore.py` DPAPI 口令持久化

**做什么：** `CryptProtectData` / `CryptUnprotectData`（CurrentUser +
`CRYPTPROTECT_UI_FORBIDDEN` + entropy `b"qqexport/v1"`）保护口令，存
`%LOCALAPPDATA%\QQExport\keys.json`。

**关键设计：** `protect` / `unprotect` 是构造参数（默认绑 DPAPI），
使 `KeyStore` 的全部逻辑可在任何平台用假保护器测试。

**坑（修正案 §7，都是评审抓出来的）：**
- **损坏的 JSON 不得当作空存储。** 原计划的 `test_corrupt_store_file_is_treated_as_empty`
  断言「损坏即视为空、save 可自愈」——**这是错的**，它会静默抹掉其他账号的密钥。
  改为抛 `KeyStoreCorrupt`，消息给恢复指引（建议手动重命名为 `.bak`），
  并说明这只丢缓存密钥、不丢聊天记录。`version` 不匹配同样按损坏处理
- 坏条目要**隔离**：单条 base64 非法/字段缺失/类型错误 → 该条 `corrupt=True`、
  `load()` 返回 `None`，但**不影响其他账号**。校验 uin 为 5–12 位数字、
  fingerprint 为 8 位十六进制、`saved_at` 可解析为 ISO
- 原子写用 `tempfile.mkstemp(dir=parent, ...)` 生成**唯一**临时名，
  任何异常路径都要 `finally` 清理；加并发 save 测试
- **驳回过的评审意见：** 用 ctypes 操纵 DACL 或调 `icacls` 做完整 ACL 加固——驳回。
  `%LOCALAPPDATA%` 已是每用户目录，DPAPI 已使内容在无对应用户密钥时毫无价值，
  而 ACL 操作自身出错（把用户锁在自己文件外）的风险高于收益。列为 P2
- `_dpapi` 里 `create_string_buffer` 的生命周期：必须同时持有 blob 与底层缓冲区，
  否则缓冲区可能在调用前被 GC

### Task 8 — `decrypt.py` 编排与流式解密

**做什么：** `snapshot` → 剥 1024 头流式解密 → `apply_wal` → 完整性自检 →
原子替换到最终路径。

**关键设计：**
- **不产生「剥头中间文件」**：从快照偏移 `1024` 流式读，写到明文文件偏移 0，
  省掉一次 369 MB 复制
- **损坏页写零页而不是跳过写入**，否则后续所有页偏移错位、整库不可读
- 原子输出：先写 `<dst>.<pid>.<随机>.partial`（同目录以保证同卷），
  成功后 `os.replace`。已存在的旧产物在成功前保持不变
- `integrity_probe` 用 `PRAGMA quick_check`（**不是** `integrity_check`，
  后者要校验全部索引，369 MB 上太慢），以 `mode=ro` URI 打开确保只读、不生成 journal
- **三态 `status`**（修正案 §0.1、§8.1）：`complete` / `lossy` / `failed`。
  任何 `corrupt_pages > 0`、`wal_signature_changed`、`stopped_early`、
  `wal_corrupt_frames > 0`、WAL 存在但 `plan.valid is False` → 压到 `lossy`；
  `integrity_check != "ok"` 或产物缺 SQLite 魔数 → `failed`。
  **禁止只追加 warning 就返回成功**
- `SnapshotResult.consistency` 要传导进 status：`"unverified"` 至少是 `lossy`

**坑：**
- **测试边界（计划里写了两遍，别踩）：** stock `sqlite3` **无法**创建
  「每页保留 48 字节」的库——SQLite 头第 20 字节只能由 SQLCipher 这类 codec 设置，
  而 B-tree 单元内容从页尾向前生长，用 `page_size=4096` 造出的库其真实数据
  必然占用最后 48 字节。所以**不要**试图造「解密合成库 → sqlite3 查询」的测试。
  职责切分：随机页 round-trip 证明实现自洽；`integrity_probe` 单独对一个真实
  sqlite3 造的明文库测试；**真实产物可查询性只能由 Task 12 的 live 冒烟证明**
- `decrypt_pages` 的 `lock_page_no` 参数可注入，使全零锁页分支能在小文件上测
  （真值 262144 对应约 1 GB 的库）
- **R26 的截断保护：** 若 Task 12 实测发现 `-wal` identity 比对在 QQ 活跃时恒失败，
  修法之一是让截断**绝不低于所复制主库已有的页数**

### Task 9 — `report.py` + `doctor.py` 全链路自检

**做什么：** 11 项检查（spec §9.3 的 P0 部分），每项独立容错。

**关键设计：**
- `CHECK_SPECS` 模块级常量表是实现与测试**共用的唯一规则源**
  （index / name / inputs / pass_when / warn_when / fail_when / side_effects / skips_on_fail）
- 每项包在 `_run()` 里：异常只留**类名**进 detail，不带 traceback、不带口令。
  `_run` 返回 `(CheckResult, value)` 二元组，调用处写 `add(*_run(...))`
  ——**这里出过 bug**，别写成 `add(_run(...))`
- 捕获 `Exception`，**不得**捕获 `BaseException`；
  要有测试断言 `KeyboardInterrupt` 向外传播而不是变成一条 `fail`
- 解密探测的 `work_dir` 默认用 `tempfile.TemporaryDirectory()` 并 `finally` 清理，
  **不得**默认写进 `config.work_dir(uin)`
- 容量预估并入检查 9（**不新增第 12 项**，避免全量重编号）：
  `(主库 size + wal size) × 3` 与目标卷可用空间比较，不足则 `warn` 并给字节数。
  **必须实算，不得把 spec §3 的 369 MB 写成常数**
- JSON 加 `"schema_version": 1`；路径与 uin 脱敏（`<Documents>\...\****8588\...`）
- 状态映射见修正案 §9.4：QQ 未运行但有缓存密钥 → `warn`；
  QQ 未运行且无缓存 → `fail`；口令验证失败 / page-1 无法认证 / 库损坏 → `fail`；
  WAL 缺失 → `warn`；`status == "lossy"` → `warn` 且写明降级原因
- 检查 7 必须**真的验证**口令（`detect_params` 命中），
  不能「调用方给了就直接算通过」——这是原计划的 bug，已修
- **承接 R17（修正案 §9.7）：** `discover_accounts` 的签名被冻结成
  `-> list[QQAccount]`，所以「盘符扫描被 `MAX_VISITED_DIRS` 截断但仍找到账号」
  这一事实无处上报。doctor 在 `scan_drives=True` 路径下必须**直接调用**
  `discover.find_tencent_files`（它返回 `(hits, truncated)`）并把 `truncated`
  写进检查 detail
- **承接修正案 §2.1：** 要有测试断言默认 doctor **不**触发盘符递归扫描

### Task 10 — `cli.py` 命令行入口

**子命令：** `version` / `doctor` / `key scan|set|show|clear`。退出码 0 成功 / 1 失败 / 2 用法错误。

**关键设计：**
- `_force_utf8_stdio()`：一进来就 `reconfigure(encoding="utf-8", errors="replace")`，
  失败要安全回退。cp936 控制台下 `✅` 会直接 `UnicodeEncodeError`
- `--ascii` 走纯 ASCII 渲染（`STATUS_ASCII`），输出必须能 `.encode("ascii","strict")`
- **`key set` 不接受口令位置参数**（修正案 §10.1，已同步改 spec §9.1）。
  只支持交互式 `getpass` 与 `--from-stdin`。位置参数会进 shell history、`ps` 输出
  与诊断采集
- `getpass` 在无法关闭回显时会退化为读 stdin 并发 `GetPassWarning`：
  捕获它、提示改用 `--from-stdin`、然后**中止**，不得在回显失效下继续读
- 导入名要与测试的 monkeypatch 路径一致：写 `from .core import ... sqlcipher`
  （**不要** `as sc`），因为测试打的是 `cli.sqlcipher`。这里出过 bug
- 所有需要账号的子命令走 `discover.select_account`，歧义时打印带掩码的候选列表、
  退出码 1、提示用 `--uin`
- **测试的 autouse fixture 必须同时 stub `discover_accounts`**，
  否则传 `--uin 12345` 的测试在真机上会因 `DiscoveryError` 退出 1 而非 0
- 顺手确认 `tmp_path_retention_policy` 与 `win` marker 已在 pyproject
  （Task 3 已加，**不要重复添加**）
- **出口条件：** 在干净 shell 里跑 `qqexport version` 与 `qqexport key show`，
  确认 entry point 真的解析到 `qqexport.cli:main`。Task 1 的 editable install
  成功**不能**替代这项

### Task 11 — `capture_fixtures.py` 与真库页 1 fixture

**做什么：** 一次性从真库截取「1024 头 + 加密页 1」（5120 字节）作为 fixture，
把 QQ NT 的头格式钉死在仓库里。

**这个 fixture 的独立价值：** 日后 QQ 改头格式时结构断言会立刻失败，
不必等 live 测试才发现。

**坑：**
- **密钥不得经环境变量**（修正案 §11.1）。原计划 Step 7 那条
  `QQEXPORT_TEST_PASSPHRASE="$(python -c ...)"` 要**删掉**，
  改为测试进程内直接 `KeyStore().load(uin)`，无缓存则 skip
- 采集脚本必须打印页 1 解密后的**全部**可打印字符串（按不可打印字符切分逐条列出），
  人工确认输入要求敲 `I HAVE REVIEWED THE STRINGS` 而非 `yes`
- `provenance.json` 最小化（修正案 §11.3）：`captured_at` 粗化到日期、
  **删除** `source_size_bytes`（改为 `source_page_count` + `has_wrapper_header`）、
  **删除** `key_fingerprint`、绝不记录 uin 与路径
- 提交前显式核验 staged：只应有 2 个文件、`.bin` 恰为 5120 字节、
  不含 `*.db` / `*.db-wal` / `*.db-shm`
- **无法人工确认就保持 skip**，不得为了让测试变绿而降低隐私门槛

### Task 12 — 真机 live 冒烟（**P0 验收的执行载体**）

**前置：** 本机 QQ 正在运行且已登录，或已有缓存口令。运行 `pytest -m live -v -s`。

**必须做的修正（修正案 §12，全部是评审抓出来的真缺陷）：**
- 账号选择：优先 `Config.load().uin`；未配置且恰好一个账号则用它；
  多于一个则 skip 并提示。**失败信息不得打印真实 uin**，用掩码
- **WAL 恢复必须给结论性证据**（§12.2，这是 P0 最关键的一条）。
  原断言 `ts_with >= ts_without` **允许相等**，而相等无法区分
  「WAL 没有新内容」与「WAL 回放根本没生效」。改为：
  取**一次**快照 → `inspect_wal` → 若 `last_commit_frame == 0` 则 skip 并写
  「本次没有可验证的 WAL 更新」→ 否则对**同一份快照**做两次解密
  （一次 `apply_wal`、一次不 apply）→ 逐个已提交帧的 `pgno` 断言两份产物
  在该页字节**不同** → 断言含 WAL 产物页数 == `plan.commit_size`
- `_source_fingerprint` 改用**相对路径**作键（原用 `p.name`，同名文件会互相覆盖）
- **确定性测试改为对同一快照重复解密**（原来两次都重新快照，
  QQ 在期间写库会把「源变化」误判为「解密不确定」）
- 读魔数用 `open(...).read(16)`，**不得** `read_bytes()[:16]`（会载入整库 369 MB）；
  整库比较用流式 SHA-256，不得两次 `read_bytes()`
- 明文产物用 `TemporaryDirectory`，退出前关闭所有 SQLite 连接，
  加 teardown 断言目录已不存在；清理抛 `PermissionError` 时**测试判失败**
- 只读性分两种条件记录：QQ 退出 → identity 三元组逐一不变 = `PASS`；
  QQ 运行中 → 只能断言「无文件被删除」，有新增/变化记 `SKIP(未验证)`
- cipher 指纹断言**放宽**：断言 ∈ 八组合、记录实际值、与预期
  `sha512/sha1/48` 不符时 WARN 而非 FAIL（§3.2）
- **R26 实测义务：** 测量 `attempts` 的分布。若 QQ 活跃时 identity 比对恒失败，
  这是 P0 验收阻塞项，按 §5.6 R26 的修法处理

### Task 13 — P0 验收与收尾

- notes 只存**脱敏摘要**，不得粘 doctor 完整输出（QQ 版本、Python 版本、
  cipher 指纹、页数、损坏页数、WAL 帧数与最后 commit 帧号、各项 PASS/WARN/SKIP）
- 新增交付物 `docs/compat-matrix.md`（修正案 §13.2）：
  Windows 版本/架构、Python 版本、QQ 版本、wrapper 形状、cipher 指纹、
  WAL 状态、检查结论。**不记录** uin、完整路径、昵称、消息样本
- P1 探测用 `TemporaryDirectory` + `contextlib.closing` 关连接；
  **删掉**原计划的 `rm -rf work-p1-probe`（Windows 上不稳定，且该路径不被
  `.gitignore` 的 `work/` 覆盖）
- doctor JSON 用 `tempfile.NamedTemporaryFile`，**不要** `/tmp/doctor.json`
- **新增发布检查门**（修正案 §13.5）：`python -m build` 出 sdist+wheel →
  检查 wheel 含 `qqexport` 全部子模块、**不含** `tests/` / `tests/fixtures/` /
  任何 `*.db` / `keys.json` / `.superpowers/` → 在仓库外建干净 venv 从 wheel 装 →
  跑 `qqexport version` 与 `qqexport key show` → 退出码符合约定。
  需要在 dev extra 加 `build`。Python 3.12 那一腿延后到 CI（本机只有 3.14）
- 验收表（修正案 §13.6）**只有全 PASS 才能把 README 改成「P0 已完成」**；
  有任何 SKIP / 部分恢复 / WAL 未验证，README 必须写「P0 部分验收（待人工验证项：…）」

### P1+ 的计划还没写

**这是有意的。** spec §5.1 的架构切口使前后两半仅以「一个明文 SQLite 文件路径」
为契约；而 P1 的表名、列 ID、segment 类型分布**依赖只有 P0 在真机跑通后
才能实测确定的事实**。现在写死等于把假设固化进计划。

Task 13 Step 3 负责采集这批实测事实（实际表名全集、`c2c_msg_table` /
`group_msg_table` 的实际列名与行数、`40800` blob 的典型长度分布、
cipher 指纹、WAL 典型帧数、解密耗时与产物大小、任何与 spec §7.1 不符之处），
**脱敏后**记进 notes，作为 P1 plan 的输入。

---

## 10. 裁定登记册（R1–R26）

完整版在 ledger 与修正案里，这里是一行摘要，便于快速判断某处行为是不是有意的。

| # | 裁定 | 一句话 |
|---|---|---|
| R1 | `decrypt_page` 的 reserve 区**填零**，不照抄密文 | 否则所有 byte-exact round-trip 断言都不可能成立；填零还让明文产物不残留密文片段 |
| R2 | 保留八组合表，但加 `combo_is_possible()` 过滤 | `("sha512","sha512",64)` 几何上不可能（64 < 16+64） |
| R3 | `fake_sqlcipher.build_wal` 的 `mac`/`reserve` 给默认值 | 原测试用 `**{k:v ... if k!="reserve"}` 会缺参数 |
| R4 | `test_candidate_roots_dedupes...` 加 `delenv("USERPROFILE")` | 否则真实 USERPROFILE 额外贡献候选根 |
| R5 | 测试改名 `test_insignificant_inspect...` → `test_inspect...` | 笔误 |
| R6 | 超时测试的 verify 改 `lambda c: False` | 否则第一个区域就命中返回，走不到超时分支 |
| R7 | 损坏页告警文案含「损坏」二字 | 与测试断言的关键词对齐 |
| R8 | `cli.py` 用 `sqlcipher` 不用 `as sc`，删未用的 `config` 导入 | 测试 monkeypatch 打的是 `cli.sqlcipher` |
| R9 | CLI 测试的 autouse fixture 同时 stub `discover_accounts` | 否则真机上 `--uin 12345` 会 `DiscoveryError` |
| R10 | Task 11 Step 7 的口令注入命令保持不变 | **已被 §11.1 推翻**，改为进程内读 KeyStore |
| R11 | `.superpowers/` 用 `.git/info/exclude` 本地排除 | 不污染入库的 `.gitignore` |
| R12 | live doctor 测试传 `work_dir=tmp_path` | 否则会写进真实 `%LOCALAPPDATA%` |
| R13 | 只读性验证分 QQ 运行中 / 已退出两种条件 | 运行中只能断言「未删除」，其余记 SKIP |
| R14 | 从入库文件清除真实 uin 与真实路径 | 见 §7.1 事故 |
| R15 | **驳回**「多进程不得取第一个验证通过者」 | 验证是账号特定的 HMAC，属确定性判定 |
| R16 | `tmp_path_retention_policy` 与 `win` marker 提前到 Task 3 | 全局约束，Task 10 不得重复添加 |
| R17 | 盘符扫描截断信号义务转交 Task 9 | 因 `discover_accounts` 签名被冻结 |
| R18 | `pgno > commit_size` 是**良性帧**，跳过而非中止 | 原规则会把合法 WAL 的全部贡献丢弃 |
| R19 | pass 2 绝不写入未提交事务的帧 | SQLite 原子性边界是 commit 帧；写半个事务会产出结构性损坏的库 |
| R20 | `WalApplyResult.notes` 属冻结字段集（共 7 个） | brief 的 Produces 块与其自身代码矛盾 |
| R21 | Windows 忽略目录只读属性，改测只读**文件** | 「只读目录」在 Windows 上测不出来 |
| R22 | 文件身份用 `os.stat`，不调 Win32 API | 3.14 上 `st_file_index` 不存在，但 `st_ino`/`st_dev` 已正确填充 |
| R23 | shm 锁只在**合成**文件上测 | 锁运行中 QQ 的文件是拿真实客户端稳定性换零信息 |
| R24 | `-shm` 移出 identity 比对 | 它被 mmap 后持续变动，纳入会让每轮恒失败且对正确性无意义 |
| R25 | `use_shm_lock` 默认 `False` | 锁持有到 369 MB 复制完毕会让 QQ 报「数据库已被锁定」 |
| R26 | **P0 验收阻塞项**：Task 12 必须实测 `attempts` 分布 | `-wal` 在 QQ 活跃写入时可能让 identity 比对恒失败，合成测试测不出 |
| R27 | keyscan 失败语义分层：区域读失败跳过、进程失败跳过、全部被拒才抛 | 计划「一遇 5/1314 就抛」会因任一辅助进程拒权而中止整个扫描 |
| R28 | `_last_error` 按构造绑定（自建 DLL 用 `ctypes.get_last_error`，注入的用其 `GetLastError`） | 3.14 的 WinDLL 没有 `use_last_error` 属性，计划写法让假 kernel32 测试必失败 |
| R29 | `KEY_LENGTHS` 的 32→16 顺序不可由测试区分 | 带 NUL lookbehind/lookahead 时交换顺序结果相同，别再声称有覆盖 |
| R30 | 合成随机页库的 `quick_check` 必然非 ok → Task 8/9 的「完整成功」测试打桩 `integrity_probe` | 三态落地后计划里「产物存在即成功」的断言不再成立；failed 分支另用真实 probe 测 |
| R31 | CLI 的 `--data-dir` / `--uin` 挂在各子命令上 | argparse 中子命令后的选项只交给子 parser；挂主 parser 时计划自己的 `key set --uin` 测试都会失败 |
| R32 | `--ascii` 只保证状态标记 ASCII，中文检查名保留 | 强行全 ASCII 会把中文名称与降级原因变成 `?`；cp936 本就能显示中文，测试钉住「内容为 ASCII 时严格可 encode」 |
| R33 | `scan_key` 跨进程共享 `seen`；`detect_params` 按 kdf 缓存派生；候选循环内查 deadline | 真机 8 进程/5 GB 内存把 2.3 万候选放大成约 10 万次 PBKDF2，120s 与 600s 都超时；修复后 70.2s 完成 |
| R34 | **保留**页 1 第 20 字节（SQLite reserved bytes），删除 `normalize_page1_header` | 真机实测该字节是 80 而密文尾 reserve 是 48；强行写 48 会让整库 `quick_check` 报 Fragmentation、产物被判 failed |

---

## 11. 已延后的 minor（合并前由最终评审 triage）

累计约 25 项，完整清单在 ledger。其中**建议合并前修**的：

- Task 2：5 个 explicit-root 测试未加隔离，仅靠 first-hit-wins 语义才恰好不受
  真机状态影响；一旦语义回归会把真实 uin 打进失败输出
- Task 2：`test_drive_scan_prunes_noisy_dirs` 删掉 `_PRUNE` 仍通过（假保证）
- Task 4：`reason` 字符串未冻结，Task 9 的 doctor 不得对
  `"ok"` / `"wal_absent"` / `"page_size_mismatch"` 之外的值做字符串匹配
- Task 5：pass 2 的 `pending` 字典把一个事务的全部页缓存在内存，
  上界是单个 WAL 事务大小（本机 1.9 MB，远小于 369 MB 库），可接受；
  病态的整库单事务重写会缓冲整个 WAL

其余为文案、命名、覆盖广度类，见 ledger。

---

## 12. P0 验收清单（spec §10.3 + 修正案 §13.6）

2026-10-28 真机结果（Windows 11 x64 / Python 3.14.6 / QQ 9.9.36-53644）：

- [x] **PASS** `python -m pytest -q`：381 passed / 8 skipped（skip 全是缺 fixture）
- [x] **PASS** doctor 11 项无 fail（live 实测全 `[OK]`）
- [x] **PASS** 明文库 `quick_check == ok`；`c2c_msg_table` 86 行、
      `group_msg_table` 985 行可 `SELECT COUNT(*)`
- [x] **PASS** 最近的消息在（R26/§12.2）：WAL 45 帧、最后 commit 第 45 帧、
      13/20 个已提交页与主库不同、产物 415 页
- [x] **PASS（有条件）** 源文件未被修改：QQ 运行中窗口内 identity 三元组未变；
      严格「QQ 退出后」复测仍建议做一次
- [x] **PASS** 同一快照两次解密流式 SHA-256 一致
- [x] **PASS** 无明文残留：无 `.partial`、临时目录已清理、`git status` 干净
- [x] **PASS** staged/untracked 隐私扫描与全库巡检通过
- [x] **PASS** wheel 构建 + 仓库外干净 venv 安装，`qqexport version` /
      `key show` 退出码 0/0；wheel 不含 tests/fixtures/db/keys.json
- [ ] **SKIP（未验证）** 真库页 1 fixture —— 仍需人工审查后 `git add -f`
- [ ] **SKIP（未验证）** Python 3.12 —— 延后 CI

**结论：P0 部分验收。** 存在 SKIP，不得写成「P0 已完成」（修正案 §13.6）。

---

## 13. 这个项目实际踩过的坑（别再踩）

1. **把自己实测到的真实标识符写进入库文档。** 见 §7.1。写的时候就用合成值
2. **round-trip 测试的假安全。** 加密与解密共用同一套常量理解时，
   同一个错误会两端抵消而通过。必须有独立 known-answer 向量（Task 3 的
   `tests/test_sqlcipher_kat.py` 是范本：RFC 2202 / NIST SP 800-38A 实取值 +
   不碰 `fake_sqlcipher` 的独立页构造）
3. **凭记忆写十六进制常量。** Task 3 评审实取 RFC 2202 后确认：
   凭记忆写出的 case-2 摘要值 `effcdf95...` 与 RFC 实际的 `effcdf6a...` **不同**。
   取不到权威值就改为「本机计算 + 注明 provenance」，并在报告里说明，
   **绝不要填一个看起来合理的值**——假权威比没有测试更糟
4. **测试名字声称的行为没被测到。** 见 §5.2 的三起事故。变异验证是唯一可靠手段
5. **brief/计划自身有 bug。** 已发现 4 例：`struct.unpack_from(">I" + ">I")` 非法格式串、
   `pytest.raises(match="magic")` 与实际消息不符、`add(_run(...))` 少了解包、
   `Config` 相关的跨根合并与 spec §6.1 矛盾。**计划不是圣旨**，
   发现矛盾就按 spec → 修正案的优先级裁定，并写一条 R 编号
6. **WebFetch 大文件会给出自相矛盾的摘要。** Task 5 实现者抓 300 KB 的 `os_win.c`
   得到两处互相矛盾的「4 字节步长」说法，正确地没有采信，改为在本机实测。
   **摘要不能当权威，能实测就实测**
7. **cp936 控制台。** 中文/emoji 直接 print 会 `UnicodeEncodeError`；
   Git Bash 里看到的乱码通常只是显示问题，文件内容是好的。
   判断成败看退出码与数字
8. **`Path.glob` / `os.scandir` 扫 D 盘根目录会撞上 `System Volume Information`
   权限拒绝。** 要限深度、剪枝、逐个吞 `OSError` 继续
9. **`truncate` 向上扩展是零填充**，不是保留原值。写断言时别搞错
10. **Windows 忽略目录的只读属性**（R21）；`msvcrt.locking` 持锁期间
    整个文件对其它句柄不可读（Task 5 实测）
11. **SQLCipher 的密文尾 reserve ≠ SQLite 头第 20 字节。** 真机实测前者 48、
    后者 80；把 48 写进页头会让整库 `quick_check` 报 Fragmentation（R34）。
    解密只负责还原页字节，不要「修正」写入方已认证的头部字段
12. **内存扫描的成本在候选验证而不是读内存。** 2.3 万个不同候选 × 每进程重复
    验证 ≈ 10 万次 PBKDF2，比读 5 GB 慢一个量级；跨进程去重 + 按 kdf 缓存
    派生 + 候选循环内查 deadline 才能稳定在超时内（R33）
