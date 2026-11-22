# tool-check

会话启动时**自动判断工具装在哪**（本机 Linux / WSL / SSH 主机 / Windows），只在缺工具时弹提示，
并给「一键自动安装 / 手动安装 / 稍后」三个选项。只读探测，装软件一定先问。

## 启动流程

1. **Agent 本身跑在 Ubuntu / Kali / macOS**
   - 查当前系统的工具完整性。
   - 齐全：footer 显示 `tools ok`；第一次检测、状态从缺→齐、隔 24h 会弹一条
     `工具齐全：<环境>（N 项）`，之后只留 footer（`PI_TOOL_CHECK_VERBOSE=1` 每次会话都提示）。
   - 缺：警告列出缺什么 → `一键自动安装 / 手动安装（给命令） / 稍后`。

2. **Agent 跑在 Windows**
   - 先找 Linux 环境：
     - **WSL**：`wsl -l -v` 里有发行版；**没启动就自动启动**（`wsl -d <distro> -- true`），
       再以 root 进去探针检查工具。
     - **SSH**：`~/.ssh/config` 里能免密（BatchMode）登录的主机，进去探针检查。
   - 找到且工具齐全：footer 显示 `tools ok · WSL: Ubuntu`；同样只在状态变化/隔 24h 时弹一条确认。
   - 找到但缺工具：警告 + `一键自动安装 / 手动安装 / 稍后`（装进 WSL / SSH 那台）。

3. **找不到 Linux 环境**
   弹「未检测到 Linux 环境（WSL / 虚拟机 / SSH 都不可用）」：
   - `工具装在 Ubuntu/Kali（我先去启动它）` → 给出启动步骤；选「已启动」后**重新检测**（最多 3 轮）。
   - `工具用本机（Windows）` → 查 Windows 本机，缺了同样给自动/手动；选择会记住，下次不再问。
   - `稍后` → 6 小时内不再弹（footer 仍显示 `tools !N`）。

### 安装选项

| 选项 | 行为 |
|---|---|
| 一键自动安装 | 后台跑 `scripts/setup-tools.py --target <key> --only <缺失> --yes`，完成后复查并通知；日志 `~/.pi/agent/tools-setup-*.log` |
| 手动安装 | 只打印命令：先 `--list` 看计划，再 `--allow-sudo` 安装 |
| 稍后 | 记住 6 小时，不弹对话框 |

需要 sudo 密码或 SSH 密码时，自动安装会拒绝并转成手动命令（扩展没有 TTY，不会吞密码）。
Linux 目标（WSL/apt）自动安装用 root / 免密 sudo；SSH 目标要求免密。

## 命令

```
/tools-check     按同样顺序复检（WSL/SSH/本机）并报告，不安装
/tools-setup     手动选目标（Windows 本机 / WSL / SSH / Kali / 本机 apt）→ 计划 → 确认 → 安装
```

## 开关

| 变量 | 作用 |
|---|---|
| `PI_TOOL_CHECK=off` | 整个扩展不加载（命令也不可用） |
| `PI_TOOL_CHECK=debug` | 脚本/探针失败时打印原因 |
| `PI_TOOL_CHECK_ASK=off` | 不弹对话框/警告，只更新 footer（`tools !N` / `tools ok`；检测到跑分/triage 环境变量时也会自动视为 off，避免打断全自动跑分） |
| `PI_TOOL_CHECK_VERBOSE=1` | 工具齐全时每个会话都弹一条确认（默认只在状态变化/隔 24h 时弹） |
| `PI_SETUP_TOOLS=<path>` | 安装脚本路径（默认按 `PI_WORKBENCH`、cwd 自动探测；探测到一次就缓存） |

## 缺失清单与探针（87 项）

清单在 `bin/tools_check.py` 的 `CATALOG`，与 `scripts/setup-tools.py` 的安装目录对齐，
覆盖 base / recon / web / pwn / forensics / crypto / rev / android / ad / qol 共 87 项，
不是只查 12 个核心工具。

- 可执行文件：`shutil.which`（PATH 里的 MSYS2/Git-Bash 工具也算已装）。
- pip 工具（pwntools、pycryptodome、z3、angr…）：用 `importlib.util.find_spec` 检查，不 import。
- Windows 上没有原生安装路径的工具（gdb、radare2、hashcat…）归 `needs_alt`，提示走 WSL / VM / Docker。
- **Kali 专属工具有等效替代**：rizin→radare2、bulk-extractor→scalpel/foremost/sleuthkit、
  metasploit→impacket/nuclei、netexec→impacket、responder→bettercap。替代装了就显示
  `已用替代：…`，不再算缺失；`setup-tools.py` 在 Ubuntu 上也会自动改装甲乙。
- WSL/SSH：把 probe 拼成 `command -v` 循环 + `python3 find_spec` 模块检查在远端跑，
  缺哪个回哪个 id，不会为了查工具去装任何东西。
- `--json` 同时给 `env`（OS、WSL 状态、SSH 主机）、`targets`（安装目标）、`probes`、`groups`。

`python bin/tools_check.py env` 可以单独看环境；`--catalog my.json` 换自定义清单：

```json
[{"id": "ffuf", "probe": "ffuf", "group": "web", "platforms": ["windows", "linux"]}]
```

## 不做的事

- 不安装、不下载任何东西，除非用户在对话框里选了「一键自动安装」。
- 不联网（除了用户自己配置的 SSH 主机）。
- 不改配置，只写状态文件 `~/.pi/agent/.tools-check.json`（`pref` / `snooze` / `script`）。
