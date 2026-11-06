# 交接文档 —— QQ 聊天记录导出

**文档版本：** 2.0  
**最后更新：** 2026-11-06  
**当前分支：** `main`  
**当前预发布：** `v0.1.0-pre.2`  
**当前源码版本：** `0.1.1rc1`（候选版，尚未发布）。
**当前基线：** P1+ T01–T10 实现完成；正式版真机门槛尚未齐备；P2 T01–T03 完成、T04 起继续。
**验证基线：** 本地普通测试 `576 passed, 10 deselected`；隐私巡检通过；
中文/空格路径、缺失运行库提示、隔离安装/启动/卸载自动冒烟通过；本轮 CI 尚未验证。

> 本文档是当前唯一入口。P0/P1 的历史交接、裁定与踩坑记录已归档到
> [`HANDOFF-p0-p1-archive.md`](HANDOFF-p0-p1-archive.md)，只在追溯历史时阅读。
> 规范与计划优先级见 §3。

---

## 1. 项目是什么

Windows 桌面工具：读取**本机已登录 QQ NT 账号**的本地数据库，解析聊天记录，
在 GUI 中预览并导出 TXT / PDF / HTML。

核心边界：

- 仅限本人自有账号、自有设备、自有数据
- 只读源数据库与 WAL，不修改 QQ 数据
- 不读取 QQ 进程内存（密钥扫描是 P0 的显式能力，不在默认 GUI 流程）
- 所有处理在本机完成，不上传聊天、密钥或图片

完整声明见 [README](../README.md) 与 [DISCLAIMER](../DISCLAIMER.md)。

---

## 2. 当前状态

### 2.1 已完成

| 范围 | 内容 | 状态 |
|---|---|---|
| P0 | 数据目录定位、密钥、SQLCipher 解密、WAL 回放、doctor/CLI | ✅ |
| P1 | 消息解析、联系人、媒体、TXT/PDF/HTML、CLI 导出 | ✅ |
| GUI | 会话列表、聊天预览、头像/图片/表情/回复/撤回、实时追加 | ✅ 界面已定稿 |
| 多选 | 行列表、圆形复选框、整行点击/底色、全选、日期范围 | ✅ |
| P1+ T01 | 三格式同消息快照与一致性测试 | ✅ |
| P1+ T02 | 导出目录原子替换、失败保留旧产物 | ✅ |
| P1+ T03 | 崩溃遗留 `.tmp-*` / `.old-*` 清理 | ✅ |
| P1+ T04 | 加载 generation、异常回调、脱敏日志 | ✅ |
| P1+ T05 | 多选纯模型与边界测试 | ✅ |
| P1+ T06 | 导出基准、表列缓存优化 | ✅ |
| P1+ T07 | CLI 多格式、日期范围、消息 ID 选择；配置兼容与原子写入 | ✅ |
| P1+ T08 | 固定工具链/依赖哈希、版本单一来源、构建脚本与清单 | ✅ |
| P1+ T09 | 中文/空格绿色版、缺失运行库、隔离身份安装/卸载自动冒烟 | ✅ |
| P1+ T10 | 发布门、同版本标签/资产、正式版证据检查、版本规则与文档 | ✅ 实现；正式版证据待补 |

### 2.2 待做

| 任务 | 内容 |
|---|---|
| 正式版门槛 | Windows 10 真机、GUI 交互、真实 PDF、性能和只读性同提交证据 |
| P2 | 搜索、批量/增量导出、归档、媒体缓存、虚拟化列表 |

P2 逐任务证据见 [P2-progress.md](P2-progress.md)，实现前的数据生命周期与界面决策见
[P2-architecture.md](P2-architecture.md)。

任务细节见：

- [P1+ 任务书](superpowers/plans/2026-11-06-p1plus-stabilization.md)
- [P2 任务书](superpowers/plans/2026-11-06-p2-product-evolution.md)

### 2.3 已知限制

- 只能导出 QQ 已缓存到本机的图片/文件；服务器端过期且未缓存的内容无法恢复。
- 合并转发未在线展开；语音只保留原始信息，不转码/转写。
- 普通预览仍使用 `Text`；多选使用独立行列表。P2 计划统一为虚拟化列表。
- PDF 大文件/大量图片的性能尚未做真机基准。
- 安装包已成功编译；绿色版已在含空格路径实测启动；安装包的完整交互/静默安装
  冒烟尚未在自动化中完成（P1P-T09）。
- 实时更新为追加模式；用户离开底部时不会强制跟随。
- 打包 exe 使用 ASCII 名 `QQExport.exe`，避免 Tcl 在中文路径/中文 exe 名下的
  初始化问题；窗口标题仍是中文。

---

## 3. 规范与文档优先级

冲突时从高到低：

1. **当前用户明确要求**（对话中的最新指令）
2. **P1+ / P2 任务书**（本轮范围与验收）
3. **P1 实现计划**：`docs/superpowers/plans/2026-10-29-p1-message-export.md`
4. **P0 修正案 A**：`docs/superpowers/plans/2026-10-24-p0-amendment-A.md`
5. **设计规格**：`docs/superpowers/specs/2026-10-23-qq-chat-history-export-design.md`
6. **历史交接/实施计划**：仅作追溯，不直接照抄旧代码片段

新增重大偏离必须写进任务书或交接记录，不能只在聊天里说明。

---

## 4. 代码地图

```
qqexport/
  gui.py                  Tkinter 主界面、预览、多选、导出交互
  selection.py            多选纯模型（日期范围、全选、切换）
  cli.py                  version / doctor / key / gui / export
  config.py               配置与应用目录
  doctor.py               全链路自检
  report.py               报告数据结构
  core/
    discover.py           账号与数据目录定位
    keyscan.py            只读内存扫描密钥（显式 opt-in）
    keystore.py           DPAPI 密钥缓存
    decrypt.py            快照解密 + WAL 回放
    snapshot.py / wal.py  WAL/快照一致性
    sqlcipher.py          页级解密
  qq/
    messages.py           行 → Message，撤回/回复/戳一戳
    contacts.py           显示名：昵称（群名片）（QQ号）
    tables.py             表发现、列缓存、按会话读取
    avatars.py            头像/表情/媒体本地解析
    msgmap.py             字段号事实
  export/
    orchestrator.py       导出编排、原子目录、临时清理
    txt.py / html.py / pdf.py
    session.py            ExportSource
    model.py / media_copy.py / report.py
tools/
  launch_gui.py           PyInstaller 入口 + Tcl 路径修正
  installer.iss           Inno Setup 安装脚本
  bench_export.py         合成导出基准
  check_privacy.py        提交/全库隐私巡检
  build_release.py        隔离构建、同源版本、ZIP/安装包与哈希清单
requirements-build.lock   Windows x64 / Python 3.14.6 的完整构建依赖哈希锁
.python-version           固定构建 Python 版本（源码运行仍支持 >=3.12）
build-toolchain.json       平台与 Inno Setup 6.7.3 编译器哈希
tests/                    合成测试；live marker 默认排除
docs/                     任务书、兼容矩阵、性能基线、本交接
```

### 4.1 关键不变量

- 源 QQ 文件只读；唯一例外是 P0 历史原因下的 `-shm` 字节范围锁，默认关闭。
- 解密/导出使用临时副本；退出或失败清理。
- 临时目录名 `.tmp-<pid>-<ns>`；备份名 `<target>.old-<pid>-<ns>`。
- 导出先写临时目录，成功后原子换入；旧目录失败时保留。
- 密钥、完整 uin、昵称、聊天正文不得进入日志或公开文档。
- GUI 异步结果必须带 generation；旧任务结果丢弃。
- 多选状态使用 `selected_msgs: set[int]`，索引对应 `preview` 顺序。

---

## 5. 运行与开发

### 5.1 从源码运行

```powershell
python -m pip install -e ".[dev]"
python -m qqexport gui
```

无缓存口令时：

```powershell
python -m qqexport key scan
# 或
python -m qqexport key set
```

### 5.2 测试

```powershell
python -m pytest -q                 # 默认排除 live
python -m pytest -m live -v -s      # 真机，需 QQ 已登录/缓存口令
python tools/check_privacy.py --audit
```

### 5.3 打包

安装包与绿色版（Windows x64 / Python 3.14.6，Inno Setup 6.7.3）：

```powershell
python tools/build_release.py --out dist/release
```

只构建绿色版加 `--portable-only`；编译器路径可用 `--iscc` 指定；
离线依赖可用 `--wheelhouse` 指定。输出目录必须不存在，构建失败不覆盖旧产物。
工具环境在系统临时目录创建并清理，防止本机 Python 的含空格 venv 启动问题；
日志留在 `build/release-build.log`（仅本机，不入库）。

重复构建验证：

```powershell
python tools/build_release.py --out dist/release-second --compare dist/release/build-manifest.json
```

产物：

- `dist/release/QQExport/`（`QQExport.exe` + `_internal`，必须整体分发）
- `dist/release/QQExport-portable-<版本>.zip`
- `dist/release/QQExport-Setup-<版本>.exe`
- `dist/release/build-manifest.json`、`SHA256SUMS`（本地构建证据，不自动发布为资产）

版本只修改 `pyproject.toml`，随后运行 `python tools/build_release.py --sync-version`；
README、运行时、EXE 文件版本、安装脚本和清单标题从同一版本生成。

### 5.4 性能基准

```powershell
python tools/bench_export.py --messages 10000 --formats txt
```

当前基线：10k TXT 约 1.1s、TXT+HTML 约 1.6s；细节见
[perf-baseline.md](perf-baseline.md)。

---

## 6. 本轮重要实现决定

### 6.1 导出原子化（P1P-T02）

- 所有格式先写临时目录，不再直接写最终目录。
- 成功后旧目录改名为 `.old-*`，临时目录换入；失败恢复旧目录。
- PDF 失败且没有其他格式时不发布空目录。
- 旧产物在失败/取消时保持完整。

### 6.2 崩溃遗留清理（P1P-T03）

- 导出前扫描根目录 `.tmp-*` / `.old-*`。
- 解析文件名中的 PID；进程仍存活则保留，避免误删并行导出。
- 无法解析 PID 的目录不处理。

### 6.3 GUI 异步与错误（P1P-T04）

- `_load_chats` 使用 `_load_gen`；旧加载完成回调不生效。
- `_start_preview` 的 worker 持有自己的队列和数据源；旧任务不会写入新队列。
- 错误写 `%LOCALAPPDATA%\QQExport\logs\app.log`，消息脱敏；UI 只显示原因。
- Tk `report_callback_exception` 兜底，避免静默吞异常。

### 6.4 多选（P1P-T05）

- 多选视图是独立行列表，不是 `Text` 内字符；避免文本重排和位置漂移。
- 圆形复选框：未选空心，选中灰底白勾。
- 整行（含图片、右侧空白、复选框外侧）可点击。
- 进入多选按当前顶部消息 + 像素偏移恢复，不跳顶。

### 6.5 性能（P1P-T06）

- `tables.columns_of` 按连接缓存；`iter_chat_rows` 不再每行执行 PRAGMA。
- 修复前 1k 条 TXT 5.5s，修复后 0.14s；10k 1.1s。

### 6.6 CLI 对齐与配置（P1P-T07，2026-11-06）

- `export --format txt,html,pdf` 复用 GUI 编排器的 `formats` 与同一消息快照；
  单格式及默认 HTML 保持兼容。
- 新增 `--start` / `--end YYYY-MM-DD`，按本机时区包含完整日界；GUI 日期选择
  与 CLI 共用 `selection.day_timestamp`，不修改已定稿布局。
- 新增可重复 `--message-id`，与日期范围取交集；使用消息 ID 而非行号，只允许
  单个会话，避免跨会话 ID 重叠。多会话仍可用重复 `--chat` 或 `--all`。
- 无效参数在打开数据前以退出码 2 拒绝；部分会话/PDF 失败退出 1，成功格式保留。
  空筛选沿用编排器语义生成空聊天文件，不将“不指定选择”误当成空选择。
- 旧配置的缺省字段与未知字段兼容；损坏、错误类型或不可读配置抛出脱敏的
  `ConfigError`，CLI 无堆栈报错，GUI 沿用加载失败提示；拒绝覆盖损坏配置。
- 配置保存用唯一的同目录临时文件、flush/fsync、原子替换；写入/替换失败保留
  旧文件并清理临时文件。使用方法见 README 的“命令行导出”。
- **验证：** 本地 Windows / Python 3.14 普通测试 `543 passed, 10 deselected`，
  包含 CLI 合成数据库集成、三格式同快照、单日/跨日/单端/空范围、选择交集、
  参数拒绝、失败退出与数据源关闭、配置损坏及写入/替换失败注入；默认及全库隐私
  巡检通过。测试在可写临时目录运行，DPAPI 测试在沙箱外通过。
- **未验证：** 本轮未运行真实 QQ 的 10 项 live 测试；PDF 一致性使用替身验证输入，
  未启动真实 Edge；GUI 快速切会话/多选/图片/实时追加未做真机复测；T07 的
  Python 3.12/3.13 和 CI 未运行；未重新构建或发布预发布资产。

### 6.7 可复现构建与同源版本（P1P-T08，2026-11-06）

- 固定 Python 3.14.6 / Windows x64；20 个直接及传递依赖均有精确版本和 wheel
  SHA-256，`pip --require-hashes` 安装到独立临时 venv；不使用系统额外包。
- Inno Setup 固定 6.7.3，并校验编译器及核心 DLL 哈希；拒绝错误工具链。
- `pyproject.toml` 是版本单一来源：源码直接读取，wheel/冻结程序读取同源
  METADATA，README 自动同步；安装包与 PE 版本由构建脚本注入，无安装脚本旧值兜底。
- 输出先在同卷暂存，成功后整体发布；已有目录拒绝覆盖。ZIP 排序、时间戳固定，
  清单含源码/依赖/工具链指纹及文件/资产哈希；剔除 pip 本机安装路径信息。
- **验证：** 完整普通测试 `553 passed, 10 deselected`；默认与全库隐私巡检通过。
  两轮从同一源码快照创建全新 venv，实际生成绿色版和安装包，1022 个文件的路径、
  大小及 SHA-256 均一致；EXE 的 FileVersion/ProductVersion 均为 `0.1.0`。
- **复现范围：** 门槛为相同构建输入的目录结构一致。本机两轮额外达到了 bundle
  内容一致，不据此承诺跨机器或安装包/压缩包始终逐字节一致。
- **未验证：** 本轮未进行安装/卸载或运行 exe 的冒烟（交给 T09）；未运行 live、
  真实 Edge PDF 或 GUI 交互回归；Python 3.12/3.13 的新增普通测试与本轮 CI
  尚未运行；未改动现有预发布或上传新资产。

---

## 7. 安全与隐私红线

1. 不修改 QQ 数据目录；不读取 QQ 进程内存（除非用户显式执行 keyscan）。
2. 密钥不进入日志、异常、报告、stdout、Git。
3. 导出/解密临时明文必须清理；日志只写脱敏后的异常类型与位置。
4. 测试只用合成数据；真实 uin/路径/昵称/正文不得入库。
5. 提交前必须：

   ```powershell
   python -m pytest -q
   python tools/check_privacy.py
   python tools/check_privacy.py --audit
   ```

6. 公开仓库不要粘贴真机命令输出；需要记录时按 `<uin>`、`<path>` 脱敏。

**已发生过一次真实 uin 入库事故**，详见归档交接 §7。写文档时就用合成值。

---

## 8. 协作与提交约定

- 直接提交 `main`，不开长期分支；发布用 tag。
- 一个任务一个中文提交：`feat:` / `fix:` / `test:` / `docs:` / `build:` / `perf:`。
- 每完成一个任务，验证通过后立即提交并推送 `main`，不积攒多个任务后再推送。
- 版本不随任务提交递增；P1+ 收尾为 0.1.1，P2 为 0.2.0；候选版用 rcN，
  验收通过才转正式版。唯一来源、标签和资产规则见 [VERSIONING.md](VERSIONING.md)。
- 提交 body 写“为什么”，不只重复 diff。
- 每次提交前跑测试 + 隐私巡检；推送前确认没有真实标识符。
- 不 force-push（除非用户明确要求处理历史）。
- 任务完成记录：实现、测试、隐私检查、未验证项；SKIP 不算 PASS。

---

## 9. 发布状态

- 预发布：`v0.1.0-pre.2`（GitHub Release，pre-release）
- 资产：`QQExport-Setup-0.1.0.exe`、`QQExport-portable-0.1.0.zip`
- 源码由 GitHub 自动提供，不重复上传
- 安装包按当前用户安装；绿色版为 `exe + _internal`
- 发布前仍需完成 P1P-T09 的安装/绿色版冒烟与 P1P-T10 检查清单

---

## 10. 接手建议

1. 先读本文件 §2–§4，再读 P1+ 任务书。
2. 不修改已定稿界面布局；只修缺陷或按 P2 评审后的方案演进。
3. 执行 P2 任务书；正式发布仍必须通过 [发布清单](release-checklist.md)，不得把未验证项计为 PASS。
4. 任何导出改动先跑 `tests/test_orchestrator.py`，确认原子替换与旧产物保留。
5. 任何 GUI 改动先验证：快速切会话、进入/退出多选、图片、实时追加。
6. P2 的虚拟化/搜索会触及隐私面，先写数据与索引生命周期，再写 UI。

---

## 11. 交接检查清单

- [x] T01–T10 实现代码已提交并推送 `main`
- [x] 普通测试通过（T10：563）
- [x] 隐私巡检通过
- [x] 预发布资产可用
- [x] 界面定稿
- [x] P1P-T07 CLI 对齐
- [x] P1P-T08 可复现构建
- [x] P1P-T09 安装/绿色版自动冒烟
- [x] P1P-T10 发布检查与文档实现
- [ ] P1+ 正式版同提交真机证据全部齐备
- [x] P2 启动（T01 分页 Repository 完成）
