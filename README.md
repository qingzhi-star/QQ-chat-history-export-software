# QQ 聊天记录导出

在 Windows 上导出**本机已登录 QQ NT 账号**的聊天记录，支持 TXT、PDF、HTML，
并可在导出前按日期或逐条勾选消息。

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
- 左侧会话列表，右侧聊天预览：头像、图片、表情、回复、撤回提示
- 实时追加新消息，并同步昵称、头像变化
- 导出前多选：逐条勾选、全选、按日期范围选择
- 多格式同时导出：TXT、PDF、HTML
- 图片内嵌到 PDF/HTML，无需联网
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

### 输出目录结构

```
QQ聊天记录导出/
└── 会话名称-QQ号/
    ├── chat.txt       （勾选 TXT 时生成）
    ├── chat.pdf       （勾选 PDF 时生成）
    ├── chat.html      （勾选 HTML 时生成）
    └── media/         （仅 HTML 需要的文件附件）
```

- TXT 中图片记为 `[图片]`、文件记为 `[文件]`
- PDF/HTML 内嵌图片
- HTML 中的文件以链接形式保存在 `media/`

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

运行测试：

```powershell
python -m pytest
```

仅运行需要真实 QQ 的冒烟测试：

```powershell
python -m pytest -m live -v -s
```

## 从源码构建可执行文件

### 绿色版（PyInstaller）

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --windowed --onedir `
  --name QQExport --distpath dist --workpath build/pyinstaller --specpath build `
  --hidden-import PIL._tkinter_finder --collect-submodules qqexport `
  tools/launch_gui.py
```

生成的 `dist/QQExport/` 即绿色版目录。分发时必须把 `QQExport.exe` 与
`_internal` 一起打包。

### 安装包（Inno Setup 6）

安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php) 后执行：

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" tools\installer.iss
```

安装包输出到 `dist/installer/`。

## 常见问题

### 提示“没有缓存口令”

程序需要读取本机 QQ 数据库密钥。先运行：

```powershell
python -m qqexport key scan
```

或手动设置：

```powershell
python -m qqexport key set
```

然后重新启动 GUI。

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
- 程序只读取 QQ 本地数据库及 WAL，不读取 QQ 进程内存
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
