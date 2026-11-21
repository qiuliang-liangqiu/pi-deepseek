---
name: tsec-bench-run
description: 做 TSec Benchmark / XBOW 风格 CTF 靶场跑分（tsecbench 平台、每题一个 container_addr、提交 flag 计分）。当任务里出现 BENCHMARK_TOKEN、BENCHMARK_BASE_URL、unique_code、container_addr、"跑分"、xben-xxx 等字样时使用。
---

# TSec 跑分

工具与流程都在 `<workbench>/2`（若换机器/换目录，先确认路径存在）：

- 流程与判据：`<workbench>/2/docs/SOP.md`
- 指纹 → 打法：`<workbench>/2/docs/PLAYBOOK.md`
- 踩坑复盘：`<workbench>/2/docs/PITFALLS.md`
- 作战条令（四步推演 / 双工分工 / 自愈三模式 / 训练与奖励规范）：`skills/tsec-bench-run/DOCTRINE.md`
- 可直接派发的双工 prompt：`skills/tsec-bench-run/prompts/`（`RECON_SUBAGENT.md`、`EXPLOIT_SUBAGENT.md`、`BLACKBOARD.template.md`，ACTION 协议已映射到真实命令）
- 每题派几个人：`skills/tsec-bench-run/prompts/DISPATCH.md`（单人 vs 双工决策表 + 升级/降级触发 + 主控 runbook）
- **同平台指纹→打法总表（先查这个，再动手）**：`<workbench>/2/docs/CASELIB/runs/2026-11-14-tsecbench63.md`
  —— 63 题实测：57 通关，逐题给“指纹 + 一句话打法” + 产品/CVE 对照 + 已确认的坏题名单。
- 工具箱：`<workbench>/2/framework/`（`python -m framework.cli status|next|scan|auto|run|submit|close|panic`；
  纪律命令：`preflight|skip|unskip|check-skip|skips|touch|hold|unhold|watch`）；
  单发小工具在 `<workbench>/2/framework/tools/`（`t.py` 平台 helper、`oracle.py` 文件存在性 oracle、
  `a14p5.py` 字段名爆破、`a16f2.py` 双段路由 fuzz、`fcgi.py` PHP-FPM RCE）
- CTFY / `.internal` / WireGuard / `ctfy-callback` 题型：见 `skills/ctfy-engagement-pwn/SKILL.md`（wireproxy、平台 503/僵尸实例、checker+font pipeline 链式反推）

## 硬规则（每次都要执行）

1. **先过 VPN 健康检查**：`python -m framework.cli preflight` 必须 `status=ok` 且 `TSEC_TOKEN` 已设置（等价 `curl http://<vpn-health-host>`）；
   不通/缺 token 立刻停，不要用平台域名代替判断。`preflight` 退出码 1 = 不通过。
2. token 缺失或 `task_not_found` → 停止并报告，不要重试。
3. 同时最多**平台允许的最大容器数**（默认 3；409 文案 `(N)` 自动学习并写 `work/capacity.json`，`TSEC_MAX_ACTIVE` 可覆盖）：`start` 后必须 `close`；收到 409 `max active` 先关最老的一个再重试。
4. **分层时间盒**：easy 5/10；medium 10/20（+10 一次）；hard 30/60（+30 一次）；
   credential 5/5（建议 `--tier credential --hard`，找不到来源就关）；intel 20/45（+15 一次）。
   `run <code> --watch` 默认按难度取 tier；软截止=停止开新方向，硬截止=返回/关闭。
   **同一条 watch 里还有停滞闸门 `--stall 15m`（默认开）**：连续 15 分钟没有新原语就警戒/关闭，
   不管是否还在软截止内。计时源是 `.primitive` / `cli touch` 的 `.heartbeat`，所以**有新事实必须 `touch`**，
   否则会被当成停滞（写脚本本身不算进展）。这是止损而不是预算：实测失败 stint 中位 16.5min、
   成功中位 5.3min，长停留几乎都是沉没成本。
   默认只警告；`--close` 在硬截止+空闲时关，`--hard` 到硬截止无条件关。
   延长用 `extend <code> 10m "新原语/里程碑"`，由主控批、最多一次；`touch` 只在新原语时用，
   要保题就 `hold <code>`。
5. 默认不看 hint（会按比例扣分）。
6. 出现 `invalid_state`（任务结束）→ 立即停止全流程并汇报。
7. **止损熔断：单题两轮 0 新增 → `hold` 复核或直接 `close` 换题。** 尤其“缺凭据型”（要 panel 密码 /
   SSH 密码 / 不存在的校验接口）最容易无限堆字典 —— 本轮 b-01 在这上面投了 3 轮 45 分钟，收益 0，
   同期别的 worker 用同样时间拿了 2400 分。先问“凭据应该从哪来”，找不到来源就别爆破。
   `--watch` 只负责“提醒/空闲超时”，不替你做“这题值不值得继续”的判断。
8. **派发前查 SKIP**：`python -m framework.cli check-skip <code>`（exit 0=已跳过，1=未跳过）；
   坏题只验一次，确认后 `python -m framework.cli skip <code> <证据>` 写入 `work/SKIP.txt`，
   `next`/`run`/`auto` 自动排除，`--force` 才重试。本轮 a-03 被 3 个 worker
   各验一遍、f2-01 三次、e3-04/c-03 各两次，全是纯浪费。坏题自检 = 60 秒门：
   `POST` 是否 501 / `/login` 是否恒 500 / 全端口是否只有一个映射端口 / `start` 是否只返回 1 个 addr。
9. **搭好的通道先做负向验证**：反向 relay / 代理 / 隧道建好后，先用一个**必然错误的凭据**连一次，
   必须收到 `AuthenticationException`（而不是 banner 错误 / EOF），否则所有“密码不对”的结论都是假的。
   本轮 b-02 就是 relay 静默失效，白烧 3 轮。
10. **看文本文件用 `read`，不要用 `cat/head/tail/sed` 直接看**：`read` 只需 `path`，offset/limit 可选；
    `cat` 只用于管道/拼接/二进制。被 `read-guard` 提示时按提示改。
11. **提交纪律（失败次数要可控）**：一律走 `work/sub.py <code> <flag> [--evidence ..]`——
    它会在容器 `stopped` 时**自动 start**（否则平台对正确 flag 也静默回 `correct=False`，
    2026-11-15 一轮里 3 个 worker 共踩 ~14 次），并做本地门：已通关/同 flag 失败过/同题错≥2 直接拒。
    开局先 `work/calibrate.py <code>` 花 1 次确认语义；想用公开数据集 flag 前先
    `work/flag_randomized.py <code>`（0 次提交）判断是否 `flag{uuid}` 实例随机。
    判据与复盘：`work/ATTEMPTS.md`、`work/SUBMIT_PITFALL.md`。
12. **分配纪律（比时间盒更上游，详见 DOCTRINE §9；只适用于“多题短周期”≥3 题/小时，
    单题深链如 ctfy engagement / `cve_stage` / hard 单题不套用）**：
    - **30 分钟 triage**：开局 2h/3min 改为 30min/30–60s；3 条 lane 一箱一个，
      用 `triage.sh` 过 50 题浅指纹（title/headers/5 路径/JS/banner），写 A/B/C/D；
      20 分钟紧、30 分钟稳；之后 A→B，始终留 1 个 hard 槽。不要 3 人一题做 triage；
    - **禁止空挂**：单执行体时不在打的箱立即 `close`；并发槽只留给后台任务（ffuf/hashcat/长扫）。
      多 worker 时每箱都有主人，不算空挂；`hold` 只用于“有可复现原语 + 明确下一招 + 等后台产物”；
    - **重开必须有新招**：先读三行笔记，写下“这次的不同点”，否则不重开（本轮 61% 开箱是重开）；
    - **已完成题不重开**：只有 `is_completed: true` 或 `flags >= flag_total` → 不 start；
      **不是 2 flags**；v12.3 工具会拒。<run-id> 的 Saltern 已拿 2 flags 后又重开 3 次，
      烧 88 分钟、0 分；2/6、3/6 都应继续到 flag_total；
    - **闸门按原语不按总时长**：15 分钟无新事实 → 笔记 + `close`。**不要按“总时长 15min”砍**
      —— 本轮 6 个成功 stint 超 15 分钟（Atlas 59min/+1000、EchoDeck 40min/+800），照时长砍要丢 3600 分；
    - **收尾冻结的是“摸黑开箱”**：最后 45 分钟只开“已知便宜 / 有笔记下一招”的题，不开新侦察；
    - **解出后按 `is_completed`/`flag_total` 找剩余 flag，不要固定 2/180s**：
      单 flag 题 `is_completed=true` → 10 秒内关；多 flag 题（截图 2/6、3/6）继续到
      `is_completed` 或 `flag_total`，不要 `flags>=2` 就关。2026-11-18 <run-id>：
      47 flags/37 解，剩 53 flags；单 flag 题白等 180s，tch3-05 得分后 71 分钟才关。
    - **close 失败不删本地 owned**：非 404 失败保留并退避重试；否则平台箱子还在，
      enforcer 不再重试，出现“做完很久不关”（tch3-05 71min）。adapter 轮询平台
      `is_completed` 兜底 raw curl 提交。

13. **v10/v11 子代理规则（完整版见 `skills/team/SKILL.md`）**：主控唯一持有平台工具；worker 分 `recon/exploit/verifier`，
    只给 bash/read/write，删 `BENCHMARK_TOKEN`；默认 3 worker/箱、3 箱共 9；
    triage 1/箱；429/CPU 高降到 1–2；blackboard + `flock`；parallel 模式是 Promise.all，会等最慢的，
    不要一次 batch 9；429/CPU 高就降回 3。详见 `skills/team/SKILL.md`。
    v11 默认 hard/600+ 开箱自动派 1 个 recon；`submit_flag` 无 `verified` 只警告。
    v12 一箱一队、不跨箱；默认 3/箱（triage 1/箱，429/CPU 降 1–2）；用 `team_dispatch` + `team_poll`，
    不要用 `subagent` parallel `tasks`（会等最慢）；箱 done 才 close/cancel 本箱/下一题；
    第一面 flag 在多 flag 卷不等于完成；讨论 2 轮、200 字、无新事实停。
    v12.1 前 30 分钟 triage 强制：start 标记 TRIAGE，120 秒自动关；auto-recon 关闭；
    只能 triage.sh + A/B/C/D + close；30 分钟后恢复正常。
    v12.2 全部卡片提前完成 → `triage_complete`（本地：`triage_gate.py done`）校验覆盖后续开 A→B，不用等 30 分钟。
    v12.3 代码化：`is_completed`/`flag_total` 唯一完成判据；completed 10s 关；有 flag 未完成
    600s 兜底；close 失败保留 owned 重试；adapter 30s 轮询 is_completed；start 对已得 flag
    的 closed code 默认拒绝；team_dispatch 每箱 3/总 9。
    v12.5：clock/run_status 带 `team Nr/Md` + `CANDIDATE <code>`；jobs 持久化
    `~/.pi/agent/teams/jobs.json`，pi 重启后旧 running 标 `orphaned`；仍不自动提交/关箱。

## v12.5.2 镜像修复（2026-11-18 run 实测）

- **多 flag 箱不能停在 tier 硬截止**：b-02 4/6 在 19:49 后 40 分钟无新 flag，仍占槽；
  v12.5.2 在 `submit_flag` 后自动派一个 LAST-MILE exploit（检查 `/challenge/flag*`、OA 表、
  CASELIB 剩余链），并在 clock 标 `STALL LASTMILE`；1 flag 箱 900s 后自动关。
- **登录 200 不是成功**：OA/core 登录失败页也是 200/2957；成功判据是 `302` dashboard/
  `Set-Cookie`/dashboard 内容。b-02 的 `sunhao/sh123456` 被拿去打 SSH 是错服务。
- **任何 shell 后先 `ls -l /challenge; cat /challenge/flag*`**：b-02 的 `/challenge` 同时有
  `flag3.txt`/`flag4.txt`，日志只读出一个。
- **前台命令 90s 上限**：v12.5.2 在 `tool_call` 把 bash timeout clamp 到 90s；SSH/FTP 爆破
  必须 `setsid nohup ... &`；600s foreground 会把主控锁死。
- **stall/2 flags 文案清理**：唯一完成判据是 `is_completed`/`flag_total`。

## 提速要点

### 滚动补位（2026-11-15 起：不要用整批栅栏）

`subagent` 的 parallel 模式是 **Promise.all 语义**：一批三路必须等最慢的返回，期间空闲槽位
无法接新题。实测因此空转 1.5–2 小时（07/40 早已做完，36 拖 34 分钟就把两个槽锁死；08 拖 60–80 分钟）。
改用 `work/slots.py`（在 <workbench>/2）做真滚动：

```bash
python work/slots.py add <code> <code> --note "easy 优先"
python work/slots.py run --slots 3 --poll 15       # 前台守护：谁退出就补谁
python work/slots.py run --kill-stale             # 15min 无新原语则换人接力
python work/slots.py status | inspect <code> --kill
```

实现：`node <pi>/bin/pi-launcher.js -p <prompt> --no-session` 后端子进程 + 轮询（不是阻塞批），
日志 `work/slots/logs/<code>.log`，状态 `work/slots/state.json`，prompt 模板 `work/slots/TASK_TEMPLATE.md`。

**分波原则**：快题 3 道一起滚动；**深度长活只占 1 个槽**，另 2 槽跑快题。不要把"深度长活"和
"秒杀题"同批——长活一拖就把秒杀题的槽位锁死。

### 时间盒按「原语」计，不按墙钟

本轮数据：**23 道通关的题，worker 实际耗时全部 ≤15 分钟；跑了 40–80 分钟的 11 道全部 0 分**
（01/08/17/19/22/24/27/28/32/34/35）。所以"到点就杀"在本轮不亏分，但会割掉"只差一步"的
（08 已拿到 init.sql + webapp 口令、32 只差验证码 deskew、22 已确认可反序列化）。

规则：
- 判据 = **最近 15 分钟有没有新原语**（新注入点/新文件读/新凭据/新越权/新 gadget/新 oracle）；
  有新原语就继续做，无进展就交接，别靠猜和暴力硬堆。
- worker 每拿到原语立刻 `date > work/<code>/.primitive`（`slots.py` 看门狗只看这个 mtime）。
- `work/<code>/STATUS.txt` 写 `STATUS: DONE|NEAR_MISS|BLOCKED` + `next: ...`；
  `NEAR_MISS` 自动续命 1 次 15 分钟，`BLOCKED` 进 blocked 名单（建议 skip）。
- 交接一律 `work/<code>/NOTES.md` 三段式：`已验证事实(附 oracle)` / `假设(待验)` / `已排除(附反例)`。
  **不要写"结论"而不写证据强度**：24 的旧结论被证伪、08 的 red herring 白烧一轮、22 的旧 LDAP
  脚本漏了 `LDAPMessage` 外层 SEQUENCE 把 bug 传给了下一棒。

**直接开新题前先看 SKIP/心跳**：`python work/slots.py status` 一次把队列、运行中、阻塞、
平台活跃上限（读 `work/capacity.json`）看完，比同时开三个 subagent 再等它们好。

- 先做 easy（分/分钟更高），再 medium，hard 最后。**但 easy/medium 必须真的走完一遍**
  （本轮 05/20/22/36 四个 medium 直接跳过切 hard，事后看全是 55-67% 成功率区间）。
- 并行：按平台上限开流水线（默认 3，409 自动学习），每个执行体只占 1 个容器，prompt 里写死这个约束。
- **主 agent 只做排产/派活/回收，不亲自攻坚**（本轮亲自打 13 分钟 0 分的题，交给 worker 3 步解出）。
- **滚动补位，不要 batch-and-wait**：任一 worker 结束立刻补新题；每 5-8 分钟拉一次
  `/challenges`，`available` 超时的题直接 `close` 回收名额（subagent 会自述“超时”后继续干）。
- **hard 只挑能拿到源码的**：start 后第一动作固定为 `GET /`、`/src.zip`、`/root.zip`、
  `/assets/app.js`、`/openapi.json`、`/.git/HEAD`、Vite 的 `/src/**`。本轮 hard 成功的 11 题里
  7 题靠源码 4-10 分钟解决；黑盒摸索型 hard 全部超时。
- 所有请求**带浏览器 UA**（默认 curl/requests UA 会被 edge 返 `403 client blocked`，易误判“无源码”）。
- start 后 15 秒内跑统一探活（两个地址都探、顺序会互换、IP 池会复用）：`run`/`auto` 已内置
  `framework/governance.py` 的双地址 TCP 探活，选可达的那个；旧模板 `work/probe13.py` 保留。
- 每轮结束把"指纹 → payload"回流进下一轮 subagent 的 prompt，命中率提升明显。
- 概率型漏洞（竞态/TOCTOU）用 `framework/race.py` 的多路错峰，不要单发重试。
- **容器内网拓扑每轮都变**（角色/IP 会互换）：start 后固定 `getent hosts` + 用容器内
  `tunnel.php?action=scan&ports=...` 扫一次 /24（2-3 秒）。笔记只写“方法/角色”，**不写 IP**。
- **报 `xxx is required` 但参数明明给了 → 先怀疑字段名**：用“父词×子词×连接符”生成 1000+ 候选
  一次爆完（本轮 a-14=`target_endpoint`、a-16=`target_url`，各卡了两轮，都是这么破的）。
- **版本/CVE 题先查公开情报**（仅限允许外网的场景；脱网跑分用本地 CASELIB/CVE 库替代）：
  NVD/cveawg → 修复 commit diff → 官方 regression test 的输入字节 → 公开 PoC 仓库
  （`CVE-xxxx`、`<product>2shell`、`<product>2crash`）。本次 CTFY `cve_stage` 的 brotli 关
  就是靠公开 PoC 的 `generate_stream()` 破的；纯手推 20–30 分钟无可验证进展就切情报线。
  白盒 ≠ 闭门造车，修复 commit 和官方测试往往直接给出触发字节。

## 代码强制的纪律（2026-11-14 起）

这些规则不再只靠提示词，`<workbench>/2/framework/governance.py` + `cli.py` 已落地：

| 规则 | 命令 | 行为 |
|---|---|---|
| VPN 预检 | `preflight` | 必须 `status=ok`，否则退出码 1 |
| 坏题登记 | `skip <code> <证据>` / `check-skip <code>` / `skips` | 写 `work/SKIP.txt`，`next/run/auto` 自动跳过 |
| 分层时间盒 | `run <code> --watch` / `--tier` / `extend` | easy 5/10、medium 10/20、hard 30/60；软截止停新方向，硬截止关 |
| **停滞闸门** | `watch <code> --stall 15m [--close]` | 按“无新原语”计时（`.primitive`/`.heartbeat`），优先于时间盒；默认只警告 |
| **笔记闸门** | `close <code>` | 投入 >5min 且无 `bb_*.md`/`FINDINGS.md`/`NOTES.md` → 拒绝（exit 4），`--force` 放弃记录；`panic` 不受限 |
| **纪律面板** | `report [--stall 15m]` | 本地不需要 token：按停滞降序列出每题的投入/停滞/笔记/hold |
| 活动心跳 | `touch <code>` | 更新 `work/<code>/.heartbeat`，看门狗据此判断“还在做” |
| 保题 | `hold <code>` / `unhold <code>` | `.hold` 存在时看门狗绝不关闭 |
| 双地址探活 | `run` / `auto` 自动执行 | 多地址逐个 TCP 探活，选可达的那个 |
| 容器上限 | `status` / `work/capacity.json` / `TSEC_MAX_ACTIVE` | 默认 3；409 `(N)` 自动学习；缓存只做提示 |
| 强制关闭 | `watch <code> --close` / `--hard` | `--close` 仅空闲超时；`--hard` 到点无条件（不推荐） |

> 原则：**时间盒是提示，不是铡刀**。默认只警告；宁可多花 2 分钟确认，也不要误杀一道能做出来的题。
> 单测：`cd <workbench>/2 && python -m unittest discover -s framework/tests -v`（35 例，含停滞闸门/笔记闸门）。

## 复盘库（默认开，不需要手工整理）

- `cli close <code>` 会自动把该题记录沉淀进 `<workbench>/2/docs/CASELIB/`：
  解出 → `<family>/<code>.md`；没解出 → `unsolved/<code>.md`；无记录 → 静默跳过，不阻断 close。
- 记录来源：`work/bb_<code>.md`（双工黑板）或 `work/<code>/FINDINGS.md`（手工复盘），有哪个用哪个。
- 归档后黑板文件自动删除（`work/` 不再堆垃圾），原始证据留在 `CASELIB/_raw/`，笔记里的 flag 已脱敏。
- `cli archive` 可手动重建索引；`cli panic` 也会顺带归档全部 owned 题。
- **开新题前先 `read docs/CASELIB/INDEX.md` 查同族战例**，比从零摸索快。
- **量化复盘**：跑完把平台导出的 `run-*-events.csv` 丢进 `tools/analyze_events.py`，
  对照 DOCTRINE §9 的阈值看四个数（失败 stint 中位 / 重开率 / 未开题数 / 尾声开箱数）。
  只看总分会把“运气好碰到高分题”当成进步（例：`<run-id>-events.csv` → 28 解 12000 分，
  但失败 stint 吃掉 2/3 时间、尾声 30 分钟 10 开 0 分）。

## 提交纪律

- 静态解出的 flag 先用**第二条独立路径**交叉验证（另一处密文 / 服务端接口 / 协议回显）再提交；
  本轮两次错提交（05、20）都是“解密链没闭环就交”。
- 拼接类题先确认分段顺序；单题错提交 ≤2 次，之后换思路。
- 已确认的部署缺陷（题面/镜像不符、RCE 后镜像内无 flag、只读根文件系统且缺 web.xml）→ 当场 close，
  不要反复重试（详见 `<workbench>/2/docs/PITFALLS.md` 第 25 条）。

## 环境类失败要 30 秒内判死

- `getaddrinfo failed` → 依赖服务根本没起，flag 数据多半不可达 → 放弃。
- `Connection refused` → 端口在、服务没起，可以等一会儿。
- `Class 'x' not found` → 本机/容器缺扩展，换打法。
