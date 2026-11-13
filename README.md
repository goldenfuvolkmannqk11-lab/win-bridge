# muse-bridge v0.2 — 远程 Linux 控制本地 Windows（个人原型）

> Remote Linux to local Windows control bridge prototype: outbound polling, token auth, security scanning. 远程 Linux 控制本地 Windows 的个人原型：只向外轮询、token 认证、安全扫描。

> 个人用原型，不是产品。只做最安全的两件事：**白名单文件夹内的文件读写 + 跑批准的 Python / 中望脚本**。

## 架构（文字图）

```
你说话（Muse 聊天）
      |
      v
远程 Linux（这台虚拟机）— relay_server.py（中转，http.server，标准库）
      |  任务存在 jobs.json，重启不丢
      |  POST /job 提交任务 / GET /job 轮询 / POST /result 回传 / GET /result/<id> 查询
      |
      |  Windows agent 每 2 秒主动来问（只向外连，不开端口）
      v
你的 Windows 电脑 — windows_agent.py（Python 3.10+）
      |  只做白名单内的事，写 audit.log
      v
C:\muse-bridge\work（默认白名单文件夹）/ 中望（COM）
```

关键点：**agent 永远是主动向外轮询**，你的 Windows 不对外开任何端口，别人从外面连不进来。中转只是个传话的，不直接碰你的电脑。

## v0.1 能做什么

| 任务类型 | 说明 |
|---|---|
| `list_dir` | 列白名单文件夹里的文件 |
| `read_file` | 读白名单内的文件（超过 5MB 不读） |
| `write_file` | 写白名单内的文件 |
| `run_python` | 跑白名单文件夹内的一个 `.py` 文件，超时默认 120 秒，返回 stdout/stderr |
| `run_zwcad_script` | 同上，但先确认中望已打开，没打开就返回中文错误，不崩溃 |
| `screenshot` | 截屏，保存到工作文件夹，返回路径 |

## v0.1 不能做什么（故意不做）

- **没有鼠标点击、键盘操作**：图形界面点来点去最不稳，v0.1 不做。
- **没有任意命令执行、没有 shell**：`run_python` 只跑指定的 `.py` 文件，不能直接跑 `cmd`、`del`、`format` 这种命令。
- **只能碰白名单文件夹**：默认只有 `C:\muse-bridge\work`，`..` 穿透会被挡住，白名单外的路径直接拒绝。
- **不管 SolidWorks**：v0.1 只接中望，SW 以后再说。
- **没有 Tailscale**：v0.1 / v0.2 都用普通 HTTP + token，先跑通；Tailscale 内网是以后再加的加固项。

## 文件清单

- `relay_server.py` — Linux 中转，标准库 only
- `windows_agent.py` — Windows agent，标准库 + pywin32 + Pillow（只在需要时用）
- `bridge_config.json` — agent 配置：中转地址、token、白名单、轮询间隔
- `install_windows.bat` — Windows 一键装依赖
- `security.py` — 安全模块，标准库 only（禁扩名、文本扫描、哈希白名单、隔离、口令锁定）
- `test_security.py` — 安全测试（Linux 上跑，5 个场景）
- `send_job.py` — v0.2 一键发任务（读配置、发任务、等结果，不用手敲 curl）
- `send_job.bat` — v0.2 双击版发任务（把参数转给 send_job.py）
- `watch_folder.py` — v0.2 文件夹监听（新 .py / .job.json 自动发任务，结果写 .result.json）
- `get_token.py` — v0.2 小助手（只打印 bridge_config.json 里的 token，给 start_bridge.bat 用）
- `start_bridge.bat` — v0.2 一键启动中转 + 代理（两个最小化窗口）
- `stop_bridge.bat` — v0.2 一键停止中转 + 代理
- `setup_autostart.bat` — v0.2 开机自启设置（在启动文件夹里建快捷方式）
- `remove_autostart.bat` — v0.2 取消开机自启
- `test_send_job.py` — v0.2 测试（Linux 上跑，16 个检查）
- `README.md` — 本文件

## Windows 安装步骤

1. 把整个 `win-bridge` 文件夹拷到 Windows 电脑上，例如 `C:\muse-bridge\`。
2. 装 Python 3.10+，安装时勾选 **Add Python to PATH**。
3. 双击 `install_windows.bat`：装 `pywin32`、`Pillow`，建好 `C:\muse-bridge\work`。
4. 用记事本打开 `bridge_config.json`：
   - `relay_url` 改成你的 Linux 地址，例如 `http://你的Linux地址:8765`
   - `token` 改成一个你自己定的长密码，**不要用默认的 CHANGE_ME**
   - `allowed_folders` 默认 `["C:\\muse-bridge\\work"]`，想加文件夹就往里加
5. 在 Linux 上启动中转（token 要和上面一致）：
   ```
   BRIDGE_TOKEN=你的密码 python3 relay_server.py
   ```
6. 在 Windows 上启动 agent：
   ```
   python windows_agent.py
   ```
   看到“中转: … 轮询间隔: 2 秒”就说明在跑了。

## 端到端测试

在 Linux 上提交一个 `list_dir` 任务：

```bash
curl -X POST http://127.0.0.1:8765/job \
  -H 'Content-Type: application/json' \
  -d '{"token":"你的密码","type":"list_dir","params":{"path":"C:\\muse-bridge\\work"}}'
```

返回一个 `id`，记下来。再查结果：

```bash
curl "http://127.0.0.1:8765/result/<id>?token=你的密码"
```

等几秒（agent 每 2 秒轮询一次），`status` 变成 `done`，`result.items` 里就是 Windows 那个文件夹的文件列表。`audit.log` 里也会多一行。

也可以先在 Linux 上跑自测，不用 Windows：

```bash
python3 test_relay.py
```

它会启动一个临时中转，走一遍 提交→轮询→回传→查询，6 步都通过才算过。

## v0.2 自动化（新增）

> v0.2 只做本地自动化：少敲命令、少复制 id、不用每次开三个窗口。
> **远程连通还是没解决**：Muse 这边云端 Linux 没有一个你 Windows 能直接连上的公开地址，
> 所以 v0.2 的中转默认还是跑在你自己电脑上（127.0.0.1:8765），任务还是由你在电脑上发。
> 想让 Muse 直接发任务，还得再加 Tailscale 或公网隧道，那是下一步的事。

### 1. 一键发任务（send_job.py / send_job.bat）

不用再手敲两遍 curl、不用再复制 id：

```cmd
python send_job.py list_dir
python send_job.py run_zwcad_script flange_zwcad_v3.py
python send_job.py run_python hello.py
python send_job.py read_file hello.txt
python send_job.py write_file hello.txt 你好
python send_job.py screenshot
```

它会自己读 `bridge_config.json` 里的 relay_url 和 token，发任务、每 1 秒查一次结果，
最多等 120 秒（`--timeout 秒数` 可以改），然后把结果打出来。
退出码：0 = 成功，1 = 任务失败 / 连不上中转 / 401 / 429 / 等超时，2 = 用法不对。

- 相对路径（比如 `flange_zwcad_v3.py`）当成工作文件夹里的文件；
  绝对路径直接用，但必须在白名单内，否则发之前就拒绝。
- `send_job.bat` 是双击版：双击后按提示敲参数，或者在 cmd 里直接用
  `send_job.bat run_zwcad_script flange_zwcad_v3.py`，跑完会停一下让你看结果。
- 如果 cmd 里中文显示成乱码，先敲 `chcp 65001` 再跑。

### 2. 文件夹监听（watch_folder.py）

```cmd
python watch_folder.py
```

盯着工作文件夹（默认 `C:\muse-bridge\work`），有新文件就自动发任务：

- 新 `.py` 文件：先过安全检查，高危的直接隔离到 `quarantine/`、不提交；
  干净的按名字决定类型——名字里带 `zwcad`（比如 `flange_zwcad_v3.py`）
  就发 `run_zwcad_script`，否则发 `run_python`。
- 新 `.job.json` 文件：里面写 `{"type": "...", "params": {...}}`，
  按里面写的类型发任务；params 里的相对路径按工作文件夹解析。
  例如 `{"type": "list_dir", "params": {"path": "."}}`。
- 文件要先“稳定”才处理：大小 2 秒没变才算写完了，避免拿到写一半的文件。
- 同一个文件只跑一次：处理过的哈希记在 `processed.json` 里，
  改过内容（哈希变了）才会再跑。想重新跑一个文件，改一下内容或删掉
  `processed.json` 里它那一行再丢进去。
- 每个文件处理完，在它旁边写一个 `<名字>.result.json`，里面是任务结果。
- 只看工作文件夹顶层，不看子文件夹，不看 `quarantine/` 隔离区。
- 在桥文件夹里新建 `STOP.txt` 就停下，Ctrl+C 也能停。
- `python watch_folder.py --once` 只扫一轮就退出（测试用）。

### 3. 开机自启（start_bridge.bat / stop_bridge.bat / setup_autostart.bat）

```cmd
start_bridge.bat    双击：一次启动中转 + 代理（两个最小化窗口），并检查中转通不通
stop_bridge.bat     双击：停掉中转 + 代理（先建 STOP.txt，再按窗口标题 / 命令行关进程）
```

- `start_bridge.bat` 会先删掉 `STOP.txt`（不然 agent 一启动就停），
  再从 `bridge_config.json` 里读 token（通过 `get_token.py`，口令不写进 bat 文件），
  然后在两个最小化窗口里分别启动中转和代理。
- `stop_bridge.bat` 停完之后 `STOP.txt` 还留在文件夹里，
  下次用 `start_bridge.bat` 启动时会自动删掉。
- 开机自启：
  ```cmd
  setup_autostart.bat     在启动文件夹里建一个 muse-bridge.lnk 快捷方式
  remove_autostart.bat    删掉它，取消自启
  ```
  快捷方式指向 `start_bridge.bat`，下次开机登录后中转和代理自动启动。
  开自启之前必须先把 `bridge_config.json` 里的 token 改好（不能是 `CHANGE_ME`），
  否则 `setup_autostart.bat` 会拒绝并提醒你。

### 401 / 429 怎么办

- `token 不对（401）`：`bridge_config.json` 里的 token 和中转的 `BRIDGE_TOKEN`
  不一致（区分大小写，`abc123` 和 `ABC123` 是两个口令）。
  停掉中转和代理，删掉 `lockout.json`，两边改成完全一样再启动。
- `被锁定了（429）`：口令连续错 5 次，中转锁定 10 分钟。
  等 10 分钟，或者停掉中转、删掉 `lockout.json` 再重启。不要反复重试，
  每错一次都算一次，锁只会更久。
- `连不上中转`：中转没在跑。双击 `start_bridge.bat`，或检查中转那个窗口
  有没有报错、是不是被关掉了。

## 安全说明（请认真看）

- **token**：中转和 agent 必须用同一个长密码，默认 `CHANGE_ME` 一定要改掉。token 错了，中转直接 401 拒绝。
- **白名单**：agent 只能读写 `bridge_config.json` 里列的文件夹，白名单外的路径、带 `..` 的路径一律拒绝。
- **审计日志**：每个任务都记一行到 `audit.log`（时间、任务类型、路径、成功与否），随时能查谁让它干了什么。
- **杀开关**：在 agent 文件夹里新建一个 `STOP.txt`，agent 下一轮轮询就立即停止；删掉它再重启。Ctrl+C 也能停。
- **只向外连**：agent 不开端口，不接受别人主动连它。v0.1 是明文 HTTP，**不要把它暴露到公网**，只在你自己的网络 / 云主机上用；想更安全就上 Tailscale（v0.2）。
- **不要把 token 写进聊天记录、截图里发给别人。**

## 防病毒 / 防入侵（security.py，v0.1 新增）

> 先说清楚：**这一层是基础纵深防御，不能替代 Windows Defender 或任何正经杀毒软件。** 它能挡“顺手牵羊”式的入侵，挡不住零日漏洞、免杀木马。Defender 请保持开启。

**查什么（agent 和中转两边都查）：**

| 检查 | 在哪 | 命中了怎么办 |
|---|---|---|
| 禁扩名 `.exe/.dll/.sys/.bat/.cmd/.ps1/.vbs/.js/.scr/.msi/.com/.pif` | 写文件、跑脚本、中转收任务 | 直接拒绝，记 `security.log` |
| 可疑文本特征：`powershell -enc`、编码命令、`Invoke-Expression`、`rundll32`、`certutil` 下载、`bitsadmin`、`curl/wget 管道执行`、`mshta`、写注册表自启动等 | 写文件内容、要跑的脚本 | 高危的直接拒绝；一般的记日志放行（因为正经脚本也可能用到 `subprocess`、`urllib`，不能一刀切） |
| 脚本哈希白名单 | 跑 `run_python` / `run_zwcad_script` 前 | 首次运行记录哈希并放行；哈希变了（脚本被改过）就**拒绝运行**，等你批准 |
| 工作文件夹新文件扫描 | agent 每轮轮询时 | 新出现或被改过的文件先扫一遍，禁扩名 / 高危的移入 `quarantine/` 隔离，永不执行 |
| 任务体上限 1MB、读文件上限 5MB | 中转、agent | 超了直接拒绝 |
| 口令连续错 5 次 | 中转 | 锁定 10 分钟，期间正确口令也进不来，记 `security.log` |

**Defender 挂钩：** 脚本第一次运行前，如果电脑上有 Windows Defender（`MpCmdRun.exe`），agent 会先让 Defender 扫一遍这个脚本：返回 0 是通过，返回 2 是发现威胁并已处理、拒绝运行。没有 Defender 就记一条“Defender 未找到，已跳过”，不会崩溃。

**怎么看日志、怎么处理：**

- `security.log`（agent 文件夹和中转文件夹各一份）：每一行是 `时间 | SECURITY | 事件 | 详情`。事件名有 `BLOCKED_WRITE`、`BLOCKED_RUN`、`BLOCKED_JOB`、`HASH_CHANGED`、`HASH_FIRST_RUN`、`QUARANTINED`、`SUSPICIOUS_FILE`、`DEFENDER_SCAN`、`BAD_TOKEN`、`LOCKOUT`、`OVERSIZE_PAYLOAD`。
- `quarantine/`（工作文件夹下）：被隔离的可疑文件都在这里。**不要直接双击打开。** 确认是误报就手动移回原处；确认是病毒就删掉，或让 Defender 扫它。
- **脚本哈希变了怎么办：** 先看 `security.log` 里那行 `HASH_CHANGED`，确认脚本是你自己改的、没被别人动过，再批准新哈希：
  ```bash
  python3 -c "import security; print(security.approve_script_hash(r'C:\muse-bridge\work\你的脚本.py', 'script_hashes.json'))"
  ```
  批准后下次就能正常跑了。如果你没改过这个脚本却收到 `HASH_CHANGED`，**不要批准**，先查电脑。
- 口令被锁了怎么办：等 10 分钟自动解开，或者停掉中转、删掉 `lockout.json` 再重启（只有你自己能碰到这台 Linux 才能这么干）。

**查不到什么（实话）：**

- 抓不住零日漏洞和免杀木马：特征扫描只认得已知的坏套路。
- 不检查网络流量：agent 和中转之间的 HTTP 是明文的，别放公网。
- 不扫整个电脑：只扫白名单工作文件夹和经桥进来的文件，你电脑上别的地方有病毒它看不见 —— 那是 Defender 的活。

## 验证情况（实话）

- Linux 上验证过：`py_compile` 全部 `.py` 通过；`test_relay.py` 实跑通过（提交→轮询→回传→查询 6 步）；token 错误返回 401；`test_security.py` 实跑通过（.exe 写入拒绝、可疑脚本标记、哈希变了拒绝、错口令 5 次锁定、干净脚本放行，见下）。
- **v0.2 在 Linux 上验证过**：`test_send_job.py` 实跑通过 16 个检查——起临时中转（端口 18765）+ 真的 `windows_agent.py`，`send_job.py` 发 `list_dir` 和 `run_python` 退出码 0 且结果正确，错口令返回中文 401 提示退出码 1，`get_token.py` 打印出配置里的 token，`watch_folder.py --once` 丢新 `.py` 自动跑并写 `.result.json`、第二次不重复跑、`.job.json` 按里面写的类型发任务、高危脚本拒绝并隔离到 `quarantine/`。回归：`test_relay.py` 6 步全过，`test_security.py` 5 个场景全过。
- **v0.2 已在真 Windows 上验证过（2026-11-13）**：`start_bridge.bat` 启动两个窗口、健康检查通过；`send_job.py list_dir` 和 `send_job.py run_zwcad_script flange_zwcad_v4.py` 都返回 `ok: true`，中望里法兰盘外圆 D100、内孔 D50、螺栓孔 4×D12 及 3 个标注全部画出来了。bat 文件曾因 UTF-8 中文在 GBK 的 cmd 里乱码，已全部改成纯英文 ASCII。
- **远程连通还没解决**：中转默认跑在你自己电脑上（`127.0.0.1:8765`），任务由你在电脑上发。想让云端直接发任务，还得再加 Tailscale 或公网隧道，那是下一步的事。不要把这个桥暴露到公网。
- **没在真 Windows 上测过的部分**：截屏、Defender 挂钩、真机隔离、`watch_folder.py` 自动跑、`setup_autostart.bat` 开机自启。第一次用时：先双击 `start_bridge.bat`，看两个窗口是不是都起来了、中转检查是不是通了；`setup_autostart.bat` 开自启后重启一次电脑，看它是不是真的自动起来了。

## 以后还可以加什么

- Tailscale 内网：中转只监听 Tailscale 地址，不走公网 HTTP；或者公网隧道，
  让 Muse 这边能直接发任务（v0.2 还是本地自动化，远程连通还没解决）。
- HTTPS / 更强的 token 轮换。
- 白名单里加 SolidWorks 脚本类型。
- 任务审批：高风险任务先等你点确认再执行。
- 文件回传：agent 把生成的文件（DXF、截图）直接传回 Linux，而不是只传路径。

## v0.3 新增：屏幕监控 + 图形界面控制

详见 [README_v03.md](README_v03.md)。一句话：加了 `screen.py`（截屏、点鼠标、
打字、按键盘）+ `mcp_server.py`（stdio JSON-RPC shim），GUI 任务默认关闭，
需在 `C:\muse-bridge` 新建 `gui_enabled.txt` 才启用，删掉立刻停用。
v0.2 的任务一个没动，全部照常工作。
