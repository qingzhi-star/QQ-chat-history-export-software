# fixtures

本目录下的 `.bin` 文件**默认被 `.gitignore` 排除**（见仓库根 `.gitignore`）。

`nt_msg_header_page1.bin` 由 `tools/capture_fixtures.py` 从真实 QQ 数据库截取，
内容为「1024 字节 QQ NT 自定义头 + 加密的页 1」。页 1 只含 `sqlite_master`
与表结构，**不含任何消息正文**。

采集脚本会先打印页 1 解密后的**全部**可打印字符串，并等待人工逐条确认；
确认短语是：

    I HAVE REVIEWED THE STRINGS

输入 `yes`、直接回车或任何其它内容都会取消，不写任何文件。若出现账号、路径、
昵称或消息正文，必须取消。没有缓存口令时脚本直接中止——无法审查就不落盘。

确认无隐私问题后，用 `-f` 强制添加（`.bin` 被忽略是故意的）：

    git add -f tests/fixtures/nt_msg_header_page1.bin tests/fixtures/provenance.json

`provenance.json` 只记录复现必需的信息：采集**日期**（不含时分秒）、QQ 版本、
源库页数、截取字节数、cipher 指纹。**不记录** uin、路径、口令、口令指纹或源库
绝对大小。

需要跑口令相关的 fixture 测试时，先在测试进程内把口令缓存进 `KeyStore`
（`qqexport key scan` 或 `qqexport key set`）；测试会自己 `KeyStore().load()`，
**不再使用任何环境变量传递口令**（环境变量会被子进程与诊断采集带走）。
没有缓存时这两项测试保持 SKIP，属预期状态。
