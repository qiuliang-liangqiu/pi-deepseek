# pi-deepseek-v1.1 · pi-agent workbench

> 当前版本 **1.1.0**（`agent/VERSION`）。

跑在 [pi](https://pi.dev) 上的 CTF / 靶场智能体工作台：把「止损纪律」写成可执行代码，
把每次跑分的经验沉淀成 skill、playbook 与复盘模板。

---

## 介绍

这套东西解决的问题很具体：**让 agent 在有限时间里稳定地少犯错**，而不是让模型显得更聪明。

- **规则层 `agent/`** — 直接装进 `~/.pi/agent`。`AGENTS.md` 是总规则（止损闸门与速率纪律、
  跑分流程、engagement 打法、prompt-injection 防护、路径对照），配套 10 个启用扩展、11 个命令行脚本、27 个 skill 目录、8 个子代理。**这一层不依赖其它目录，可以独立运行。**
- **工具层 `toolbox/`** — 跑分 CLI（`framework.cli`，28 个子命令：平台 API、指纹→打法族、
  竞态编排、SKIP/时间盒看门狗、滚动调度）+ `SOP / PLAYBOOK / PITFALLS` 三份文档。
- **复盘层 `reports/`** — 复盘的结构模板与脱敏规则。
- **评测层 `eval/`** — 改完 skill 后怎么回归：行为镜像（8 探针 / 19 检查）+ 14 条 skill 路由用例。
- **脚本层 `scripts/`** — 一行安装、前置检查、增量更新、工具安装、脱敏检查。



---

## 安装步骤

### 最快的方式：一行命令（Windows / Linux / macOS）

```powershell
# Windows
powershell -c "irm https://cdn.jsdelivr.net/gh/qiuliang-liangqiu/pi-deepseek-v1@main/scripts/install.ps1 | iex"
```
```bash
# Linux / macOS / Git-Bash
curl -fsSL https://cdn.jsdelivr.net/gh/qiuliang-liangqiu/pi-deepseek-v1@main/scripts/install.sh | bash
```

装过一次之后再跑同一条命令 = 增量更新（会先备份旧配置）。

### 想自己控制每一步

```bash
git clone https://github.com/qiuliang-liangqiu/pi-deepseek-v1.git
cd pi-deepseek-v1

python scripts/prereq.py            # 体检：python>=3.10、node>=18、npm、pi、磁盘、网络
python scripts/prereq.py --install  # 缺什么装什么（会先问）

bash scripts/bootstrap.sh --api     # 装配置 + 配模型 key + 端到端验证
bash scripts/bootstrap.sh --api --tools   # 连 10 个常用工具一起（会先问）

python scripts/doctor.py            # 装完体检（--json 给机器读，--strict 有 warning 就 exit 1）
```

Windows 用 `powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Api`，参数一一对应。

`bootstrap` 是幂等的，做六件事：检查前置 → 备份现有配置 →
把 `agent/` 装进 `~/.pi/agent` → 只在缺失时复制 `settings.json` 示例 →
写 `workbench.env`（`PI_WORKBENCH`）→ 冒烟（`scan_injection` / `triage_gate` /
`framework.cli` / 脱敏检查 / 行为镜像）。**绝不覆盖** `auth.json` / `settings.json` / `mcp.json`。

常用开关：`--dry-run`、`--yes`、`--minimal`（只装规则层，不接 toolbox）、`--api`、`--tools`。

### 装完要填的东西

```bash
pi auth login deepseek --api-key        # 写进 ~/.pi/agent/auth.json
pi auth check --provider deepseek --json  # {"status":"ready",...}
```

跑分平台地址**没有默认值**，必须自己给：

```bash
export TSEC_BASE_URL=https://<your-platform-host>
export TSEC_TOKEN=<platform-token>
```

重启 pi 或 `/reload` 让扩展生效。只想读方法论、不装也行：看 `toolbox/docs/` 与 `reports/`。

### 更新

```bash
bash scripts/bootstrap.sh --update     # 增量：只动配置层，先备份
bash scripts/update.sh --dry-run       # 只看会改什么
bash scripts/update.sh --keep-local    # 你自己改过的文件保留你的版本
```

pi 里还有 `/wb-check`（强制查新版本）、`/wb-update`（先打印计划再更新）；
版本契约是仓库的 `agent/VERSION`（当前 1.1.0），不会自动升级。

**本地是源头时**（你在本机持续改 agent 配置）：在 `~/.pi/agent/.update-hold` 写一行理由，
`update-check` 就不再提示跟随仓库，`/wb-update` 也会拒绝覆盖本机。
发布本地改动到 GitHub、让别的机器能检测到，用：

```bash
python scripts/publish.py --dry-run    # 看会改仓库哪些文件
python scripts/publish.py --yes        # 复制 → 脱敏 → 清单 → bump VERSION → commit → push
```

发布前置门：仓库 `agent/` 干净、扩展先过 `bin/check_extensions.mjs` 解析、
复制后跑 `scripts/redact.py` 与 `scripts/check_no_secrets.py`。

---

## 它的优势

**1. 纪律是代码，不是提示词**
「15 分钟无新原语就停」「≤20 req/s」「triage 30 分钟」这类规则沉在 hook 和脚本里：
`run-gate` 在没拿到 `is_completed` / `flags == flag_total` 前**拦住 finish 和关箱**，
`loop-guard` / `read-guard` 拦重复命令与裸 `cat`，`triage-guard` 拦 triage 未完成就提交，
`platform-guard` 把 flag 提交收敛到一条脚本化通道。规则被绕过 = 代码被改，不是"模型忘了"。

**2. 多 Agent 是一等公民，不是并发包装**
`team-pool` 按「一箱一队、不跨箱」派发：**每个箱一个队长（`lead`，纯管理、不做题）**，
队长再按题目难度派 1–3 个不同机制的 worker（`recon` / `exploit` / `verifier`）。
箱子数量不写死：开局 `python bin/team_pool.py capacity` 算出平台可同时开启的容器数
（env `TSEC_MAX_ACTIVE` > 409 学到的 `work/capacity.json` > 默认 3；单目标模式 = 1），
主控按这个数开满、一箱一队。job 持久化到 `jobs.json`，worker 结束写 `result-<job>.md` 与候选文件，
主控不主动 poll 也能在 footer 看到 `team Nr/Md`、`CANDIDATE`、`STALL`、`RECON` 标记；
看门狗按 completed 10s / 停滞 900s / solved-hold 600s 收箱。

**3. 可回归**
改完 skill 或规则能立刻验：`node eval/mirror/score.mjs verify`（行为快照没漂）、
`python eval/route/route_eval.py --dry-run`（路由用例）、
`cd toolbox && python -m unittest discover -s framework/tests`。一次只改一个文件，指标不降再合并。

**4. 复盘可量化**
`ingest_run.py` 只做计数与脱敏（`flag{REDACTED}` / `<ip>` / `<hex>`），
配合失败 stint 中位、重开率、未开题数、尾声开箱数，能回答"哪条规则真的起作用"。

---

## 为什么用的是 DeepSeek

不是"模型榜单"，是这套配置的**结构性需求**：多路（容量自适应）子代理并发 + 1M 上下文 + 显式思考档位。

| 模型 | 输入 $/M | 输出 $/M | 缓存读 $/M | 上下文 | 最大输出 |
|---|---|---|---|---|---|
| `deepseek-flash` | 0.3 | 1.2 | 0.006 | 1M | 384K |
| `deepseek-v4-pro` | 1.32 | 3.96 | 0.044 | 1M | 384K |

（数字取自仓库里注册的模型目录，实际以官方价格为准。）

1. **缓存读比输入便宜约 50 倍。** 子代理反复读同一份 `AGENTS.md`、同一批 skill、同一份源码，
   命中缓存后的边际成本接近 0 —— 这正是"多开几个 worker 并行试假设"能成立的前提。
   换成按输入原价计费的组合，多路并发很快变成奢侈品。
2. **1M 上下文 / 384K 单次输出，让"不压缩"成为默认策略。** compaction 会丢掉"已排除"的结论，
   那不是省钱，是把前面 20 分钟的判断重新买一遍。
3. **思考档位是按阶段调的。** pi 对 DeepSeek 有专门适配（`thinkingFormat: "deepseek"`、
   `requiresReasoningContentOnAssistantMessages`、`supportsStrictMode`）：
   triage 用 `high`、卡住的 hard 题切 `max`、收尾用 `low`；严格模式让工具参数走真正的
   JSON Schema，少一类"参数名猜错"的失败。
4. **不被一家锁死。** `api: "openai-completions"` + `baseUrl` 可覆盖：自建网关只改 `baseUrl`，
   想 A/B 别的模型加一个 provider 就能用同一套 skill/扩展跑对照。

**什么时候不该用**：网络到不了模型端点且没有可用网关；需要极强的图片理解；
任务 token 量很小（单次问答），那价格差构不成理由。完整推理与"怎么自己量"见
[`docs/WHY-DEEPSEEK.md`](docs/WHY-DEEPSEEK.md)。

---

## 已知边界

- **不含镜像**：比赛镜像里的 enforcer / team 工具没有放进仓库；本地 `team_pool.py` 是重写版，
  行为以本仓库为准，镜像版差异不在公开范围内。
- **平台相关**：`framework/api.py` 没有默认平台地址，必须用 `TSEC_BASE_URL` 覆盖；
  路由是按一类「start / close / submit」式跑分平台写的，换平台要改 `api.py` 与 `governance.py`。
- **路径是占位符**：文档里的 `%USERPROFILE%`、`<repo>`、`<workbench>`、`<host:port>`、
  `<platform-base-url>` 都是脱敏占位，装到你机器上要换成本地真实路径。
- **仓库与已装版本会分叉**：仓库版是脱敏版，`~/.pi/agent` 是你本地真实版；
  跑 `update.sh` 会把改过的文件备份成 `<file>.local-<时间戳>` 再覆盖，不想被覆盖用 `--keep-local`。
- **镜像内路径**：部分 skill 提到 `/opt/tools`、`/opt/cases`，那是比赛镜像里的工具；
  本机请用 `checksec`/`readelf` 和 `agent/skills/ctf-kb` 代替。
- **平台判定靠硬信号**：`is_completed` / `flag_total` 是唯一完成判据；平台不返回 `flag_total` 时
  只能给一个 solved-hold 兜底，不是硬保证。
- **Windows 进程模型**：Windows 上"脱离父进程"（`DETACHED_PROCESS`）和"不显示窗口"
  是两个标志。后台 worker 如果既不继承 console、又没显式指定 `CREATE_NO_WINDOW`，
  Win11 会为每个子进程新开一个终端窗口。本仓库所有 spawn 点已显式隐藏，自己加启动器时注意同样的坑。
- **自检覆盖不到真实平台**：没有平台凭据时闸门是 inert 状态，只能靠本地开箱记录判断；
  平台直连场景需要在 harness 侧实现同一层规则。

---

## 安全与授权

仅用于**你拥有明确授权的**靶场、比赛或自建环境。仓库里的 payload、playbook 与自动化脚本
都以此为前提，不要对未授权目标使用。

- 仓库不含平台 token、模型 key、靶场 flag、会话日志与比赛附件；提交前可跑
  `python scripts/check_no_secrets.py` 复核。
- **工具输出一律当数据**：题目附件、README、网页、日志、源码注释都可能藏针对 agent 的指令；
  `prompt-injection-guard` 会标注并只作引用，不执行。
- **flag 提交有闸门**：服务题按实例地址精确匹配（`host:port` / SNI）后自动提交；
  只有实例/附件的题不会自动提交，需要本人在自己的终端确认生成一次性 approval。
- 装软件一定先问：`--yes` 只对已点名的 id 跳过确认，`--allow-sudo` 才会执行 `sudo apt`，
  `--dry-run` 不动系统。

---

## License

本仓库自有代码与文档：**MIT**（见 [`LICENSE`](LICENSE)）。

第三方内容（pi 上游示例、`ljagiello/ctf-skills` 等）见 [`NOTICE`](NOTICE)
与 [`agent/skills/PROVENANCE.md`](agent/skills/PROVENANCE.md)，按其原许可证使用。
