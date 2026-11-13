# muse-bridge v0.3 — 屏幕监控 + 图形界面控制（个人原型）

> v0.2 的东西一个没动：`list_dir` / `read_file` / `write_file` / `run_python` /
> `run_zwcad_script` / `send_job.py` 一键发任务 / `watch_folder.py` 全部照常工作。
> v0.3 只加了：看屏幕、点鼠标、打字、按键盘，外加一个 MCP 入口。

为什么加这个：SolidWorks 宏在 `FeatureCut4` 那步卡住了，脚本里看不到屏幕，
只能让 Matsuda 手动点。现在桥能自己截屏看、自己点，下一步 SW 出问题时
Muse 可以自己看图、自己点“拉伸切除”那类按钮，Matsuda 只在被问到时帮忙点一下。

## 新增文件

| 文件 | 说明 |
|---|---|
| `screen.py` | Windows 屏幕 + 鼠标键盘实现（标准库 + pywin32 + Pillow） |
| `mcp_server.py` | MCP 入口：stdio JSON-RPC shim（见下面） |
| `test_screen.py` | v0.3 自测，Linux 上跑，不用真屏幕，40 个检查 |
| `enable_gui.bat` | 双击开启 GUI 控制（新建 `gui_enabled.txt`） |
| `disable_gui.bat` | 双击关闭 GUI 控制（删掉 `gui_enabled.txt`） |
| `README_v03.md` | 本文件 |

改动的文件：`windows_agent.py`（加 6 个 GUI 任务类型 + `read_file_base64`）、
`send_job.py`（加 6 个一键命令 + 截屏自动存本地）。

## 开启：先 opt-in，不开就什么都点不了

GUI 控制默认是**关**的。Matsuda 在 `C:\muse-bridge` 里双击
`enable_gui.bat`，就会新建一个空文件 `gui_enabled.txt`，表示
“我同意这台电脑可以被截屏、点鼠标、打字”。删掉这个文件（或双击
`disable_gui.bat`）就立刻停用，不用重启 agent。

没有这个文件时，`screenshot` / `click` / `double_click` / `type_text` /
`press_key` / `get_window` 一律被拒绝，返回中文错误，并记一条
`security.log`（`GUI_BLOCKED`）。`list_dir` / `run_python` 等 v0.2
任务不受影响，不需要这个文件。

## 安全（和 v0.2 一样的规矩，再加三条）

- **还是走 token 认证**：所有 GUI 任务都要先过中转的 token + 锁定，和别的任务一样。
- **脚本哈希白名单不管 GUI 任务**：GUI 任务不是脚本，没有文件可改，
  所以不走哈希检查；守门的是上面那个 `gui_enabled.txt` 开关。
- **每一次 GUI 动作都记 `audit.log`**：点哪里（x, y）、按哪个键、打了几个字。
  打字只记前 20 个字符 + 总长度，**绝不要用 `type_text` 输密码**，
  密码请 Matsuda 自己手输。
- **坐标夹到屏幕内**：`click 99999 99999` 会被夹到右下角，不会点到屏幕外面。
- **一次打字最多 2000 字**，按错键名（比如 `NoSuchKey123`）直接拒绝，不乱按。
- **杀开关还在**：`STOP.txt` 一出现 agent 立刻停，GUI 任务也停。
- **本地 only**：中转默认还是 `127.0.0.1:8765`，远程从云上直连还没通，
  不要把中转暴露到公网。

## 怎么测试（在 Matsuda 的 Windows 电脑上，3 条命令）

先确认中转 + agent 在跑（双击 `start_bridge.bat`），再确认 GUI 已开启
（双击 `enable_gui.bat`，或 `C:\muse-bridge\gui_enabled.txt` 存在）。
然后在 `C:\muse-bridge` 的 cmd 里，一行一行敲：

```bat
python send_job.py screenshot
```
→ 应该返回 `ok: true`，宽高（比如 1920x1080），图片自动存到当前文件夹
（`screenshot_YYYYMMDD_HHMMSS.jpg`）。这张图就是 Muse 看到的屏幕。

```bat
notepad
python send_job.py click 960 540
python send_job.py type_text hello muse-bridge
```
→ 先打开记事本，再点一下屏幕中间，再打一行字。记事本里出现
`hello muse-bridge` 就说明点击 + 打字都通了。
（先点一下记事本窗口再打字，不然字可能打到别的窗口里。）

```bat
python send_job.py get_window
python send_job.py press_key Enter
```
→ 第一行返回当前前台窗口的标题和大小，第二行按一下回车。

如果 `screenshot` 返回“GUI 控制未启用”，就是 `gui_enabled.txt` 还没建，
双击 `enable_gui.bat` 再试。

## 新任务类型（给 Muse / 脚本用）

| 任务 | 参数 | 返回 |
|---|---|---|
| `screenshot` | `{}` 或 `{"window": "记事本"}`（只截标题含这几个字的窗口） | `ok`、`path`（工作文件夹里的 JPG）、`width`、`height`、`size_bytes`，<=700KB 时还带 `image_base64`（远端直接看图） |
| `click` | `{"x": 960, "y": 540}` | `ok`、`x`、`y`、`clamped`（坐标有没有被夹到屏幕内） |
| `double_click` | `{"x": 960, "y": 540}` | 同上 |
| `type_text` | `{"text": "hello"}` | `ok`、`chars`、`method`（`keyboard` 还是 `clipboard`） |
| `press_key` | `{"key": "Enter"}` | `ok`、`key`、`vk`、`modifiers` |
| `get_window` | `{}` | `ok`、`title`、`x`、`y`、`width`、`height` |
| `read_file_base64` | `{"path": "screenshot_xxx.jpg"}` | `ok`、`size_bytes`、`data_base64`（<=700KB 才给） |

`send_job.py` 的一键命令和上面一一对应：
`screenshot` / `click <x> <y>` / `double_click <x> <y>` /
`type_text <文字>` / `press_key <按键>` / `get_window` /
`read_file_base64 <文件>`。

## 截屏是怎么回传的（为什么不用再下一次）

中转的任务体上限是 1MB，一张全屏 PNG 很容易超。所以 agent 侧先把图
缩到最长边 1280、存成 JPG（quality 75，一般几百 KB），再把 base64
塞进任务结果里一起回传（<=700KB 才带，太大就不带，只留本地文件）。
`send_job.py screenshot` 收到后自动把 base64 存成本地 JPG，
不再把几百 KB 的 base64 打到屏幕上。

## 中文打字

纯 ASCII（英文字母、数字、符号）走键盘事件。中文 / 其它非 ASCII
走剪贴板粘贴：先把文字放进剪贴板，再按 `Ctrl+V`。**会改剪贴板内容，
用完不恢复**。如果目标软件不支持粘贴，中文就打不进去，只能手输。
`type_text` 返回的 `method` 会告诉你这次走的是哪条路。

## MCP 入口（mcp_server.py）

这台 Linux 上没装官方 `mcp` 包，也不要全系统装，所以这里是
一个 **stdio JSON-RPC shim**，不是官方 MCP SDK 的服务器：
客户端发 `initialize` / `tools/list` / `tools/call`，它就把调用
转成一次桥任务（读本机的 `bridge_config.json` → relay → agent），
等结果回来再按 MCP 格式吐回去。工具名和参数和上面那张表一致，
共 9 个：`screenshot`、`click`、`double_click`、`type_text`、
`press_key`、`get_window`、`list_dir`、`run_python`、`read_file_base64`。

在 Windows 的 `C:\muse-bridge` 里跑，中转 + agent 要先起来：

```bat
python mcp_server.py
```

Claude Desktop 配置（`claude_desktop_config.json` 里加一段，路径按你的改）：

```json
{
  "mcpServers": {
    "muse-bridge": {
      "command": "python",
      "args": ["C:\\muse-bridge\\mcp_server.py"],
      "cwd": "C:\\muse-bridge"
    }
  }
}
```

想换成官方 `mcp` 包的正式服务器：`pip install mcp`，然后把
`handle_tools_call` 里的逻辑搬进 `@server.call_tool()`，工具名和参数不变。
日志只走 stderr，不走 stdout，不然会污染 MCP 协议流。

## 限制（老实写出来）

- **截屏是静态图，不是直播视频**：每次 `screenshot` 是一张当时的图，
  看连续动作要多截几次。
- **坐标是屏幕像素**：分辨率变了、窗口挪了，坐标就得重看一遍图再定。
  先 `screenshot` 看图，再定 `click` 的坐标，别凭感觉点。
- **中文打字走剪贴板**：见上面，会改剪贴板，个别软件可能不支持粘贴。
- **本地 only**：`127.0.0.1:8765`，云上直连还没通。
- **没在真 Windows 上测过**：`screen.py` 的截屏、点击、打字
  只在 Linux 上做了逻辑自测（`test_screen.py` 40 个检查全过），
  真点鼠标那一下必须在 Matsuda 的电脑上按上面 3 条命令测。
