# 绮录QQ聊天记录导出软件

<!-- version:start -->
当前源码版本：**0.3.1**。
<!-- version:end -->
新版 Windows 桌面端使用 Electron，沿用绮录 D 图标与双栏布局。安装版包含卸载程序，
便携版为完整 exe / DLL / resources 目录压缩包；用户电脑无需安装 Python 或 Node.js。
开发、构建和布局说明见 [Electron 桌面端](docs/electron-desktop.md)。



绮录是在 Windows 上收藏和导出**本机已登录 QQ NT 账号**聊天记录的工具，支持 TXT、PDF、HTML，
支持搜索、批量/增量备份、可校验加密归档与离线浏览，也可按日期或逐条勾选消息。
当前采用 Electron 桌面端；已验证平台及离线读取边界见 [桌面端说明](docs/electron-desktop.md)。

左上角展示当前账号头像、用户名和 QQ 号，点击旁边的切换按钮展开本机账号列表。
选择账号后自动准备并加载记录；导出、全选与时间范围集中在底部操作栏。

项目地址：<https://github.com/qingzhi-star/QQ-chat-history-export-software>

---

## 重要声明

> **使用前请务必阅读本节。**
>
> 1. 本工具仅用于**导出本人自有 QQ 账号、自有设备、自有数据**的本地备份。
>    不得用于获取他人聊天记录、账号数据或任何侵犯他人隐私的用途。
> 2. 本工具与腾讯公司及 QQ 官方无任何关联，未获得其授权或认可。
>    使用本工具可能违反 QQ 用户协议或相关服务条款，请自行评估并承担风险。
> 3. 导出的 TXT、PDF、HTML、图片及附件可能包含**聊天原文、账号信息、图片和
>    文件**。请自行妥善保管，不得非法传播、公开或用于其他用途。
> 4. 本工具按“现状”提供，不保证兼容所有 QQ 版本、数据库结构或系统环境。
>    因使用本工具造成的任何数据损坏、丢失、泄露或其他损失，作者不承担责任。
> 5. 请遵守所在国家/地区的法律法规。若你不确定自己的使用是否合法，请停止使用。
>
> 完整免责声明见 [DISCLAIMER.md](DISCLAIMER.md)。

---

## 功能概览

- 自动识别本机已登录的 QQ NT 账号
- 会话按最后消息时间从新到旧排列，后台检测新消息并更新；大列表仅绘制可见会话行
- 左侧会话列表，右侧聊天预览：头像、图片、表情、回复、撤回提示
- 实时追加新消息，并同步昵称、头像变化
- 导出前多选：逐条勾选、全选、按日期范围选择
- 多格式同时导出：TXT、PDF、HTML
- 图片内嵌到 PDF/HTML，无需联网
- 十万级分页预览、会话/全库搜索、发送者/类型/日期筛选、精确/限时正则匹配
- 多会话队列、取消/重试/恢复；显式增量备份保留明文消息快照并校验冲突
- `.qqarchive` 校验、可选密码加密、无需 QQ 的离线浏览/搜索/导出
- 缺失媒体定位、本机重新检测、可清理的缩略图 LRU、本地转发和原始语音下载
- 显式多账号切换、设置恢复；可选明文资料库导入/合并、禁用计划任务文件生成
- 绿色版解压即用；安装包按当前用户安装，无需管理员权限

## 系统要求

- Windows 10 / 11，64 位
- 本机已安装并登录 QQ NT
- 安装包/绿色版无需安装 Python
- 从源码运行需要 Python 3.12 或更高版本（官方安装包默认包含 Tkinter）

## 下载与安装

前往 [Releases](https://github.com/qingzhi-star/QQ-chat-history-export-software/releases)
下载最新版本。

### 方式一：安装包

下载 `QQExport-Setup-*.exe`，双击并按向导完成安装。

- 默认安装到当前用户目录，不需要管理员权限
- 安装完成后可从开始菜单或桌面快捷方式启动

### 方式二：绿色版

下载 `QQExport-portable-*.zip`，解压到任意可写目录，运行 `QQExport.exe`。

> **不要删除或移动 `_internal` 文件夹。**
> 它是程序运行所需的 DLL、Python 运行库和 Tcl/Tk 数据；`QQExport.exe` 必须与
> `_internal` 保持在同一目录。

## 使用方法

1. 保持 QQ NT 已登录。
2. 启动程序，等待左下角显示“已识别 QQ …”。
3. 点击左侧任意会话，右侧显示聊天记录。
4. 点击底部 **导出…** 进入多选模式：
   - 直接点击消息任意位置可勾选/取消
   - 右上角 **全选 / 全不选**
   - 右上角 **日期选择**：在日历中选开始、结束日期，自动勾选范围内消息
5. 点击 **下一步**，选择导出格式（TXT / PDF / HTML，可多选）和输出目录，
   点 **确定**。
6. 导出完成后到输出目录查看。

头像沿用原版 QQ 公开头像入口，在后台加载，会话列表与消息头像均作圆角裁剪；
失败时显示圆角占位，不影响读取聊天。离线归档不联网补头像，导出默认不联网。
文件消息统一显示 `[文件] 文件名`，缺失时追加说明。
点击图片使用 Windows 默认图片查看器：程序先准备独立副本，保护 QQ 原图及归档媒体。
副本位于应用 cache/opened-images，按内容哈希命名，自动清理目标为 256MiB，当前图片
与被系统锁定的文件可能暂时超额；右键更多功能里的“清理图片缓存”可手动清理。

### 更多功能（右键菜单）

主窗口恢复为 0.1.0 的双栏、原版聊天气泡和多选布局，不增加菜单栏或按钮。
右击 **导出…**、会话标题或预览空白处打开更多功能；消息右键菜单也有“更多功能”。
`Ctrl+F` 打开搜索，`Ctrl+Shift+E` 打开批量导出。单会话仍走原来的多选→格式/位置→
完成提示流程，只有明确打开批量导出时才显示队列窗口。

- **搜索消息**：可选当前会话/全库及组合筛选；首次在后台建立内存索引，可取消或
  清除。双击结果跳转并高亮，支持匹配结果 TXT/HTML 导出。历史关键词默认不保存。
- **批量导出**：主界面 Ctrl 多选会话或在批量窗口选择；全部会话需明确确认。
  队列可取消、重试失败项，可选择保存检查点以恢复。增量模式额外保存明文消息快照，
  校验旧产物后只追加新消息；冲突停止，全量重建必须明确选择。
- **导出 / 打开归档**：可选密码加密；打开时校验全部文件后离线浏览。密码不会保存，
  遗忘无法恢复。[格式与隐私](docs/archive-format.md)列出算法、清理和容量限制。
- **缺失媒体**：按当前会话列出缺失项，双击定位；在 QQ 下载后重新检测。本工具
  不自动访问 CDN 补文件。语音仅保留原始音频，不转码/转写。
- **资料库**：归档导入前确认长期保存明文，即使原归档已加密；按账号隔离、冲突回滚。
  可离线打开，更多功能右键菜单可清空；使用中的资料库会阻止删除。
- **设置**：保存上次输出目录、格式、窗口状态、密度与缩略图上限；缩略图默认
  2GiB，可清空。多账号始终明确选择，上次账号只作标记。
- **生成定时导出任务**：为所选会话生成默认禁用的 Windows XML 和计划 JSON，
  不注册、不常驻。核对账号/程序/输出路径后在系统任务计划程序导入启用；须本人
  登录且有本机缓存密钥。移动软件后重新生成；系统触发仍属未完成真机验证的可选预览。

升级前阅读 [迁移说明](docs/P2-migration.md)，逐项实施状态见
[功能验收表](docs/P2-feature-status.md)。恢复旧源码外观后，滚动工作区实测约 45FPS，
含图首屏/冷缩略图约 346ms，仍有性能预算例外；Windows 10 与目标中配机证据待补，保持 rc 标记。

### 输出目录结构

```
QQ聊天记录导出/
└── 会话名称-QQ号/
    ├── chat.txt       （勾选 TXT 时生成）
    ├── chat.pdf       （勾选 PDF 时生成）
    ├── chat.html      （勾选 HTML 时生成）
    └── media/         （HTML 需要的文件、原始音频、视频附件）
```

- TXT 保留非文本类型标记，并显示可用文件名、语音时长、本机缺失说明
- PDF/HTML 内嵌图片
- HTML 中的文件/音频/视频以独立副本保存在 `media/`，按内容哈希去重

### 显示名称

- 私聊：`昵称（QQ号）`
- 群聊：`昵称（群名片）（QQ号）`

## 从源码运行

```powershell
git clone https://github.com/qingzhi-star/QQ-chat-history-export-software.git
cd QQ-chat-history-export-software
python -m pip install -e ".[dev]"
python -m qqexport gui
```

### 命令行导出

先用 `python -m qqexport list` 查看会话 ID。默认导出 HTML；`--format` 可用逗号
同时指定多个格式，输出目录结构与 GUI 相同：

```powershell
python -m qqexport export --chat c2c:u_example --format txt,html,pdf --out "D:/backup"
python -m qqexport export --chat c2c:u_example --format txt,html --start 2026-11-01 --end 2026-11-05
python -m qqexport export --chat c2c:u_example --format txt --message-id 1 --message-id 3
```

- `--start` / `--end` 使用本机时区，包含开始日 00:00:00 至结束日 23:59:59；
  可只指定一端，不指定则不限。
- `--message-id` 可重复，使用数据库的消息 ID（非预览行号）；与日期范围取交集，
  按原消息顺序导出。为避免不同会话的 ID 重叠，只能用于单个 `--chat`。
- `--chat` 可重复；也可用 `--all` 导出全部会话，两者互斥。
- 空筛选沿用导出管线行为，生成空聊天文件；默认格式仍为 HTML。
- TXT 可用 `--encoding gbk`，默认 UTF-8。退出码：`0` 全部成功，`1` 操作失败
  （包括部分会话失败或 PDF 失败，其他成功格式会保留），`2` 参数错误。

旧版 `config.json` 的缺省字段使用默认值，未知字段忽略；损坏或字段类型错误时
明确报错并保留原文件，请先备份后修复。配置保存使用同目录临时文件原子替换。

### P2 搜索、队列与增量

更多功能右键菜单提供消息搜索、批量导出和队列恢复；左侧 Ctrl 点击可多选会话。搜索索引
默认只在内存，可清除；关键词历史须显式勾选保存。结果可跳转高亮或导出 TXT/HTML。
普通与多选预览共用分页虚拟列表，图片只在可视区附近按需加载。

```powershell
python -m qqexport search --query "关键词" --chat c2c:u_example --match contains
python -m qqexport batch --all --yes --format txt,html --out "D:/backup" --checkpoint "D:/backup/job.json"
python -m qqexport batch --resume "D:/backup/job.json" --retry-failed
python -m qqexport batch --chat c2c:u_example --format txt,html --out "D:/backup" --incremental
```

批量窗口可取消、重试并选择保留未完成队列；队列只存会话标识/输出参数，恢复时
核对账号和已完成产物哈希。勾选“增量备份”后会保存**明文消息快照和媒体**，请整体
保管目标会话目录。以后用相同格式、编码、账号和目录运行，只追加新 TXT/HTML，
PDF 重生。旧文件校验、首尾/计数或游标冲突时停止；旧普通导出没有增量清单，须
另选空目录，或明确选择全量重建（CLI 使用 `--incremental --rebuild`）。

运行测试：

```powershell
python -m pytest
```

仅运行需要真实 QQ 的冒烟测试：

```powershell
python -m pytest -m live -v -s
```

## 从源码构建可执行文件

构建固定使用 Windows x64、`.python-version` 指定的 Python，以及
`build-toolchain.json` 指定的 Inno Setup 6.7.3。Python 依赖及其 SHA-256
完整锁定在 `requirements-build.lock`，构建会创建隔离环境，不使用系统中额外的包。

```powershell
python tools/build_release.py --out dist/release
```

默认从当前用户的 `Programs/Inno Setup 6/ISCC.exe` 查找编译器，也可用 `--iscc`
指定；编译器哈希必须匹配。只构建绿色版可加 `--portable-only`。
输出目录须不存在，失败不覆盖旧产物；构建日志在 `build/release-build.log`。

产物：`QQExport/`、`QQExport-portable-<版本>.zip`、`QQExport-Setup-<版本>.exe`，
以及 `build-manifest.json` 与 `SHA256SUMS`。清单记录源码指纹、工具链与依赖指纹、
各文件和分发资产的哈希，不包含本机绝对路径。绿色版必须整体分发。

验证重复构建（保证相同输入的目录结构一致，不承诺所有二进制逐字节一致）：

```powershell
python tools/build_release.py --out dist/release-second --compare dist/release/build-manifest.json
```

版本只在 `pyproject.toml` 修改，然后运行 `python tools/build_release.py --sync-version`
同步 README；运行时版本、安装包、EXE 文件版本及清单标题均从同一来源生成。
手动传给 Inno Setup 的版本不再有默认值，避免误用旧版本。

构建后执行自动冒烟：

```powershell
python tools/smoke_release.py dist/release --report build/smoke-report.json
```

测试使用合成启动入口，不发现 QQ 账号；验证中文/空格路径、缺少 `_internal` 的提示，
并以独立测试 AppId/快捷方式完成安装、启动和卸载，不覆盖现有安装。
版本更新与标签规则见 [VERSIONING](docs/VERSIONING.md)。

## 常见问题

### 提示“没有缓存口令”

GUI 中点击左下角“选择/切换账号…”，选中本人账号并确定。软件会自动验证缓存，
缺失或失效时在后台只读获取口令、验证并加密缓存，再加载会话。请保持该账号已在
本机 QQ 登录；失败会保留当前会话并给出原因，不需要手动运行命令。

CLI 用户也可显式扫描：

```powershell
python -m qqexport key scan
```

或手动设置：

```powershell
python -m qqexport key set
```

命令行操作完成后可重新选择该账号。

### 部分图片显示“本机无原图”

只能导出 QQ 已缓存到本机的图片。请先在 QQ 中打开对应聊天、让图片加载出来，
再重新导出。

### 识别不到账号 / 会话列表为空

- 确认 QQ NT 已登录，且使用的是当前 Windows 用户
- 程序按最近活动的 QQ 数据目录自动识别账号
- 多账号时可关闭其他账号后重试
- 确认没有把程序放在只读目录

### 绿色版启动失败

- 确认解压完整，`QQExport.exe` 与 `_internal` 在同一目录
- 不要只拷贝 exe
- 不要放在只读目录或权限受限的路径

### 杀毒软件误报

PyInstaller 打包程序偶尔会被误报。你可以：

- 将程序加入杀毒软件信任列表
- 或自行从源码构建

## 数据与隐私

- 所有处理均在本机完成，不上传聊天记录、密钥或图片
- 普通启动/后台刷新只读取本地数据库与 WAL；GUI 明确选择账号或 CLI 显式扫描时，
  可只读扫描本机 QQ 进程取得口令，并用所选账号数据库认证
- 不修改原始数据库
- 解密使用临时工作副本，程序退出时清理
- 导出的文件可能包含敏感内容，请自行妥善保管

## 项目结构

```
qqexport/           核心包（解密、解析、导出、GUI）
tests/              测试
tools/              打包入口、安装脚本、隐私巡检
docs/               设计与兼容性文档
```

## 路线图

- [P1+ 任务书：稳定性、导出正确性与发布工程](docs/superpowers/plans/2026-11-06-p1plus-stabilization.md)
- [P2 任务书：功能扩展与性能架构](docs/superpowers/plans/2026-11-06-p2-product-evolution.md)

## 许可

本项目使用 MIT 许可证，详见 [LICENSE](LICENSE)。

第三方组件与参考说明见 [NOTICE](NOTICE)。
