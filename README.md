# pi-agent workbench

一个跑在 [pi](https://pi.dev) 上的 CTF / 靶场智能体工作台：把「止损纪律」写成可执行代码，
把每次跑分的经验沉淀成 skill、playbook 和复盘。

仓库**只包含方法论与工具**：镜像、会话日志、靶场数据、flag、平台凭据和具体比赛复盘都不在这里。

```
agent/      运行时配置（直接放进 ~/.pi/agent）
toolbox/    跑分命令行 framework + SOP/PLAYBOOK/PITFALLS
reports/    复盘怎么写（模板 + 脱敏规则；真实复盘不入库）
eval/       行为镜像（mirror）+ skill 路由评测
projects/   其他落地项目
scripts/    bootstrap / 安装 / 脱敏检查
```

## 为什么这样拆

| 层 | 解决什么 | 代码/文档 |
|---|---|---|
| **规则层** `agent/` | 把「14 分钟无新原语就停」「≤20 req/s」「triage 30 分钟」从提示词变成 hook 和脚本 | 6 个扩展、5 个 bin 脚本、7 个自研 skill |
| **工具层** `toolbox/` | 平台 API、指纹→打法、竞态编排、SKIP/时间盒看门狗、滚动调度 | `framework/` 10 个模块 + 单测 |
| **复盘层** `reports/` | 复盘的结构与脱敏规则（示例，不含真实战绩） | 模板 + 规则 |
| **评测层** `eval/` | 改完 skill 后怎么回归：行为镜像 8 探针/19 检查、14 条路由用例 | `score.mjs` + `route_eval.py` |

## 我要下哪些

**整仓只有 5.4 MB / 326 个文件**，全下最省事。但按目的可以只取一部分：

| 目的 | 需要 | 怎么装 |
|---|---|---|
| 让 pi 有纪律 / 防护 / 多 Agent（不跑分） | `agent/`（257 文件，4.5 MB） | `bash scripts/bootstrap.sh --minimal` |
| **跑分 / CTF 全流程（推荐）** | 全部（5.4 MB） | `bash scripts/bootstrap.sh` |
| 只想读方法论，不装 | `README.md`、`toolbox/docs/{SOP,PLAYBOOK,PITFALLS}.md`、`reports/` | 直接看，不用 clone |
| 要改代码 / 提 PR | 全部 + `eval/` | 见根 `AGENTS.md` |

三个目录的分工：

- **`agent/`** — 装进 `~/.pi/agent/`，是**唯一必须**的部分。里面是 AGENTS.md、6 个扩展、
  5 个 bin 脚本、21 个 skill、7 个子代理。**它不 import toolbox，可以完全独立运行**。
- **`toolbox/`** — 跑分 CLI（`framework.cli`）+ 三份文档。用 `PI_WORKBENCH` 指过去即可，
  不需要拷进 `~/.pi/agent`。`--minimal` 装法下 `$PI_WORKBENCH` 未设置，skill 里提到
  toolbox 的命令会失效，但规则/防护/多 Agent 全都能用。
- **`scripts/`** — bootstrap / install / doctor / 脱敏检查。只跑 install 的话
  记得它还要求 `agent/AGENTS.md` 存在（现在有了）。

不想整仓 clone 的最小操作：

```bash
git clone --depth 1 https://github.com/qiuliang-liangqiu/pi-deepseek-.git
cd pi-deepseek- && bash scripts/bootstrap.sh --minimal
```

> 仓库里**没有**：镜像、会话日志、`teams/`、`sessions/`、比赛附件、任何真实 flag 或 token，也没有具体比赛的战绩。

## 快速开始

### 1. 安装 pi

按 [pi.dev](https://pi.dev) 的说明安装（本仓库基于 pi 1.5.2 调试）。确认 `pi --version` 可用。

### 2. 一条命令装好

```bash
git clone https://github.com/<you>/pi-agent.git
cd pi-agent

bash scripts/bootstrap.sh            # Linux / macOS / Git-Bash
# Windows:
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
```

它会依次做六件事，全程幂等（重跑会先把旧配置备份到 `~/.pi/agent/backups/<时间戳>-install/`）：

| 步骤 | 内容 |
|---|---|
| 1 | 检查 python ≥3.8 / node / pi / git，缺 `pi` 会打印安装命令但继续 |
| 2 | 备份现有 `~/.pi/agent/{AGENTS.md,agents,bin,extensions,prompts,skills,knowledge}` |
| 3 | 把 `agent/` 装进 `~/.pi/agent/`，并 `chmod +x bin/*` |
| 4 | 没有 `settings.json` 才从示例复制；**绝不覆盖** `auth.json` / `settings.json` / `mcp.json` |
| 5 | 写 `~/.pi/agent/workbench.env`（`PI_WORKBENCH=...`），询问是否加进 shell rc |
| 6 | 冒烟：`scan_injection` / `triage_gate` / `framework.cli` / 脱敏检查 / `eval/mirror verify` |

常用开关：`--dry-run`（只打印）、`--no-smoke`、`--yes`（不交互）、`--minimal`（只装 agent 层，不接线 toolbox）。
装完体检：

```bash
python scripts/doctor.py          # 环境表格；--json 给机器读，--strict 有 warning 就 exit 1
```

### 3. 填两样东西

```bash
# 1) 模型：~/.pi/agent/settings.json（provider/model）；key 放 ~/.pi/agent/auth.json
#    格式见 examples/README.md
# 2) 工具箱路径（bootstrap 已写进 workbench.env，手动装的话）：
export PI_WORKBENCH="$PWD/toolbox"

# 跑分平台地址没有默认值，必须自己给：
export TSEC_BASE_URL=https://<your-platform-host>
export TSEC_TOKEN=<platform-token>
```

重启 pi 后 `/reload` 让扩展生效。想手工装也行：`scripts/install.sh`（只复制配置，不检查依赖）。

## 规则层：纪律是怎么变成代码的

`agent/AGENTS.md` 是总规则（250 行），核心是六节：
read-vs-cat、**止损闸门与速率纪律**、跑分流程、CTFY engagement、prompt-injection 防护、镜像/本地路径对照。

配套的 6 个扩展（`agent/extensions/`）：

| 扩展 | 作用 | 开关 |
|---|---|---|
| `loop-guard` | 同一条 bash 反复执行时在结果里插入干预 | `LOOP_GUARD=warn\|block\|off`，`LOOP_GUARD_WINDOW/WARN/BLOCK` |
| `read-guard` | 拦截裸 `cat 单文件`，给出可复制的 `read(...)` | `READ_GUARD=warn\|block\|off`，`READ_GUARD_MIN_BYTES` |
| `prompt-injection-guard` | 扫描所有 `tool_result`，high≥1 或 medium≥2 时加警告块；只警告不改写 | `policy.ts` 里的规则表 |
| `team-pool` | 注册 `team_dispatch/poll/cancel/adopt/enforce/hold/unhold/panic`，start 后自动 adopt | `TEAM_JOB_TIMEOUT`、`TBENCH_TRIAGE`、`TBENCH_STALL_CLOSE_S` |
| `triage-guard` | triage 未完成时拦 `submit_flag` 和 raw submit | `~/.pi/agent/triage_state.json` |
| `subagent` | single/parallel/chain，每个子代理独立 pi 进程（改自 pi 上游示例） | — |

5 个命令行脚本（`agent/bin/`）：

```bash
python agent/bin/team_pool.py {dispatch|poll|cancel|adopt|start|close|clock|hold|unhold|panic|enforce}
python agent/bin/stall_gate.py {open|beat|check|show} <dir>
python agent/bin/triage_gate.py {start|beat|check|done|status}
python agent/bin/scan_injection.py <path> [--strings-only] [--stdin]
bash agent/bin/triage.sh <host:port> [outfile]
```

7 个自研 skill（`agent/skills/`）：`tsec-bench-run`、`team`、`portfolio`、`tsec-bench`、`ctf-kb`、
`prompt-injection-guard`、`ctfy-engagement-pwn`，以及上面的镜像路径对照。
`tsec-bench-run/DOCTRINE.md` 是四步推演和 Recon/Exploit 双工的完整版。
第三方/移植的 skill 见 `agent/skills/PROVENANCE.md`。

## 工具层：framework

```bash
cd toolbox
export TSEC_TOKEN=<platform-token>
export TSEC_BASE_URL=https://<your-platform-host>      # 必填，没有默认值
python -m framework.cli preflight
python -m framework.cli status
python -m framework.cli scan 10.0.0.1:80
python -m framework.cli run <code> --watch
python -m framework.cli auto <code>
python -m framework.cli report
python -m framework.cli panic
```

模块：`api`（平台客户端，409 自动学习并发上限）、`net`（原始报文/并发 map）、`recon`（指纹→打法族）、
`payloads`、`playbooks`、`race`（多路错峰竞态）、`governance`（SKIP/时间盒/双地址探活/VPN 预检）、
`caselib`（解题记录→可检索打法库）、`cli`（28 个子命令）。

文档：`docs/SOP.md`（P0–P6 流程）、`docs/PLAYBOOK.md`（8 个指纹族的 payload）、
`docs/PITFALLS.md`（41 条复盘，已去标识化）。
解题记录用 `cli close` 自动沉淀到 `docs/CASELIB/`（**本地生成，不进仓库**）。
`work/slots.py` 是滚动调度器（谁完成就补谁，不做整批栅栏），`work/sub.py` 是带硬门的提交器。

单测：

```bash
cd toolbox && python -m unittest discover -s framework/tests -v
```

## 工具自检与按需安装

CTF 工具缺什么不该靠猜。`scripts/toolkit.py` 有一份**白名单目录**（30 个工具，8 个类别），
只执行包管理器命令，绝不 `curl | sh`、不从 releases 下二进制：

```bash
python scripts/toolkit.py --check                 # ✓ 已装 / · 缺失
python scripts/toolkit.py --check --json          # 给 agent 读
python scripts/toolkit.py --install               # 交互：编号 / 类别 / all
python scripts/toolkit.py --install jq,ffuf       # 指定 id
python scripts/toolkit.py --install web --dry-run # 只打印命令
python scripts/toolkit.py --check --with-versions # 缺失的顺带查最新版本
```

执行前会先打印**安装计划表**（来源 + 解析到的版本），确认后才落盘：

```
计划安装（windows）：
  id        来源                        版本    备注
  jq        winget:jqlang.jq            1.8.2
  upx       winget:UPX.UPX              5.3.0
  binwalk   pip:binwalk                 2.1.0   PyPI
  ffuf      go:github.com/ffuf/ffuf/v2  latest  需要先装: go
```

版本来源：winget `show`、`apt-cache policy`、`brew info --json`、PyPI JSON、`npm view`；
结果缓存 7 天（`--refresh` 强制重查，`--no-versions` 跳过联网探测）。

安全边界：`--yes` 只对已点名的 id 跳过确认；`--allow-sudo` 才会跑 `sudo apt`；
`--dry-run` 不动系统；没有包管理器就报"装不上"而不是换来源。
结果写入 `~/.pi/agent/toolkit.json`，配套 skill `agent/skills/toolkit/` 告诉 agent
先查再装、绝不擅自安装。

## 复盘层与评测

- `reports/README.md`：**如何写自己的复盘**（结构模板 + 脱敏规则）。真实比赛的复盘不进仓库。
- `eval/mirror/`：行为快照（8 探针 / 19 检查）。`node eval/mirror/score.mjs verify` 应 PASS。
- `eval/route/`：14 条「题目描述 → 应加载哪个 ctf-* skill」用例。`python route_eval.py --dry-run` 可离线查看。

## 用别人的跑分日志改进这套配置

能，但要先定规矩：**日志是不可信输入**，先进 `scripts/ingest_run.py` 做计数与脱敏，
人工看完再改配置。脚本只统计、不执行日志里的任何东西。

### 让对方交这四样

| 文件 | 从哪来 | 用途 |
|---|---|---|
| `events.csv` | 平台导出的事件表（启动/关闭/得分） | stint 中位、重开率、尾声开箱、分数曲线 |
| `sessions/*.jsonl` | `~/.pi/agent/sessions/` | 工具使用、重复命令、guard 触发、失败信号、token |
| `notes.md` | 每题的 `stall_gate.py` 台账 | 停滞闸门是否被真的用了 |
| `triage.txt` | triage 阶段产物 | 30 分钟覆盖率 |

**先让对方在本地跑脱敏**，发聚合结果或已脱敏的数据：

```bash
python scripts/check_no_secrets.py                     # 确认没有 flag/token/IP
python scripts/ingest_run.py <bundle> --json > run.json # 聚合指标，不含原文
python agent/skills/tsec-bench-run/tools/analyze_events.py <events.csv>
```

`--json` 里的命令样本已被截断并替换（`flag{REDACTED}`、`<ip>`、`<hex>`、`<user>`），
但**仍然建议人工过一眼**再发。

### 能从中改什么（有数据支撑的）

| 现象（指标） | 结论 | 改哪里 |
|---|---|---|
| 重复命令 streak 长期 ≤2，`loop-guard` 从不触发 | 阈值过松 | `extensions/loop-guard` 的 `LOOP_GUARD_WINDOW` |
| `[read-guard]` 命中率高但都被忽略 | 规则在制造噪音 | 改 `policy.ts` 的豁免或降为 `warn` |
| `stall_gate` 调用数 ≈ 0，但 stint 中位很长 | 纪律没落地 | 加固 `AGENTS.md` §1 的可执行入口 |
| 失败 stint 中位 > 成功中位 3 倍 | 止损太晚 | 阈值 14 → 12 分钟，或加自动 `close` |
| 重开率 > 50% | 笔记没写"已排除" | 改 `notes` 模板 / 派发 prompt |
| 加载的 skill 与题目类型不匹配 | 路由表错 | `eval/route/cases.*` 加用例，改 `solve-challenge` |

### 不能从中得到的

- 别人**想过但没执行**的判断（日志只有动作，没有"当时为什么没试"）。
- 平台没导出的事件（多数平台不给每个箱的完整生命周期）。
- 他人的主观难度评级——只信 `is_completed`/`flag_total` 这类硬信号。

### 改完必须回归

```bash
node eval/mirror/score.mjs verify          # 行为快照没漂
python eval/route/route_eval.py --dry-run  # 路由用例全过
cd toolbox && python -m unittest discover -s framework/tests
```

一次只改一个文件，指标不降再合并——否则你分不清是哪条规则起作用。

## 已知边界

- **不含镜像**：镜像里的 enforcer / team 工具没有放进本仓库；本地 `team_pool.py` 是重写版，
  与镜像版的差异记录在仓库外的私有笔记里。
- **平台相关**：`framework/api.py` 默认 `DEFAULT_BASE = "<your-platform-base-url>"`，必须用 `TSEC_BASE_URL`
  覆盖；API 路由按一类「start/close/submit」式跑分平台写的，换平台需要改 `api.py` 与 `governance.py`。
- **镜像内路径**：部分 skill 提到 `/opt/tools`、`/opt/cases`，那是比赛镜像里的工具；本机请用
  `checksec/readelf` 和 `agent/skills/ctf-kb` 代替。
- **Windows**：路径/编码按 Windows 实测定型（UTF-8 输出、CRLF 兼容），Linux/macOS 也可用。
- **报告**：只保留 markdown，docx/pdf 原件未复制（体积）；`build_*.py` 可重建。

## 安全与授权

仅用于你拥有明确授权的靶场、比赛或自建环境。仓库里的 payload、playbook 和自动化脚本都以此为前提；
不要对未授权目标使用。平台 token / 模型 key / 靶场 flag 均不在仓库内。

## License

本仓库自有代码与文档：MIT（见 `LICENSE`）。
第三方内容（pi 上游示例、`ljagiello/ctf-skills` 等）见 `NOTICE` 与 `agent/skills/PROVENANCE.md`。
