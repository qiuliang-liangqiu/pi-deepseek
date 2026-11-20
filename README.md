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

常用开关：`--dry-run`（只打印）、`--no-smoke`、`--yes`（不交互）。
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

## 复盘层与评测

- `reports/README.md`：**如何写自己的复盘**（结构模板 + 脱敏规则）。真实比赛的复盘不进仓库。
- `eval/mirror/`：行为快照（8 探针 / 19 检查）。`node eval/mirror/score.mjs verify` 应 PASS。
- `eval/route/`：14 条「题目描述 → 应加载哪个 ctf-* skill」用例。`python route_eval.py --dry-run` 可离线查看。

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
