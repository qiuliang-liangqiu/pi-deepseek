---
name: team
description: "多 Agent 作战规则（v12.5/49545 复盘）：一箱一队、不跨箱；默认 3 worker/箱、最多 9；team_dispatch/team_poll/team_cancel；主控独占平台工具；triage 30min/120s 提前退出；按 is_completed/flag_total 收 flag；close 失败重试。Load before dispatching or reviewing any subagent run."
---

# team — 多 Agent 规则（以本卡为准）

## 0. 当前 Tsecbench v2 参数

- 50 题、100 flags、3 个同时打开的靶场、8h；
- 每题 flag 总数 **1–6 不等**（截图见过 2/6、3/6、1/6），**不是固定 2**；
- 闭卷不给日志：只能看模型调用分布、启动/关闭/得分事件、容器状态；
- 当前镜像 v12.5（3/箱、triage 1/箱、flag_total、jobs 持久化）。

## 1. 角色与权限

| agent | tools | 职责 |
|---|---|---|
| `recon` | read, bash, write | 指纹/端点/版本/JS/凭据，写 facts |
| `exploit` | read, bash, write | 按假设打；写 tried/candidate |
| `verifier` | read, bash | 第二路径复核 flag；不改状态 |

- **只有主控**有 `list/start/close/submit/touch/run_status/finish`；
- worker 只 `bash/read/write`，**删 `BENCHMARK_TOKEN`**，不能 start/close/submit；
- worker 结果只写黑板/返回摘要，flag 由主控复核后提交。

## 2. 一箱一队、不跨箱

```
3 个容器 = 最多 3 队同时存在
默认:        3 worker/箱（recon + exploit + verifier）
triage:      1 worker/箱（一箱一个，避免重复指纹）
429/CPU 高:  降到 1–2
总 worker 上限 9；9 是默认配置，不是临时模式
```

- worker 只服务当前箱，不跨箱；
- 箱 done（`is_completed`/`flag_total`/放弃/时间盒）→ 关箱、cancel 本箱 job、下一题、新队；
- 一个 worker 完成 ≠ 箱子完成：如果箱里还有别的 job，不能因为一个 worker 完成就关箱；
- 容器槽位是箱子，不是 worker；1 箱 3 worker 不会让同时推进的题变多。

## 3. dispatch / poll，不要用 `subagent tasks`

`subagent` 的 parallel `tasks` 是 `await` 全部完成（Promise.all），会等最慢的。
用 v12 的：

```
team_dispatch(unique_code, agent, task, timeout_s) -> job_id
team_poll(wait_s=0..30) -> [{id,code,agent,status,summary,artifact}]
team_cancel(job_id | unique_code)
```

- clock 行末尾会带 `team Nr/Md` 和 `CANDIDATE <code>`；`/work/teams/jobs.json`
  持久化 job，pi 重启后旧 running 标记 `orphaned`（读 artifact 再 cancel）；
- 谁先 done 先处理；不要 batch 等最慢；
- 箱 done 立刻 close，close 只 cancel 本箱 job，B/C 不受影响；
- 子进程超时 kill；不要空转。

## 4. 30 分钟 triage（v12.2）

- 前 30 分钟强制 triage：`start_challenge` 标记 `TRIAGE`，clock `TRIAGE auto`；
- `TBENCH_TRIAGE_PROBE_S=120` 秒自动关；
- 这期间 **auto-recon 关闭**；不能长打；
- 50 题浅指纹：3 条 lane，一箱一个，30–60s/题，`triage.sh` + A/B/C/D；
- 全部 open 题都有 `teams/<code>/triage.txt` → `triage_complete` → `triage_done=true` → **立即 A→B，不等 30 分钟**；
- 30 分钟是 cap，不是最短；20–30 分钟正常，15 分钟也见过（49545）。
- 官方 difficulty/score 是白送的，不用开箱；开箱只为“有没有 5 分钟入口”。

## 5. flag 语义（49545 最重要）

- 只按 `is_completed` / 每题 `flag_total` 判完成；
- **`flags>=2` 不是完成**：2/6、3/6 的题还有 4/3 面；
- 第一面 flag 在多 flag 卷不等于完成；不要关，不要丢后面两面；
- 单 flag 题 `is_completed=true` → 10 秒内关；
- 多 flag 题：继续到 `is_completed`/`flag_total`，或 tier/stall 截止；不要固定 180s；
- 180s 第二 flag 窗口只作“flag_total 未知/单 flag 平台”的兜底；
- worker 只写 `FLAG_CANDIDATE`；主控去重、第二路径复核、带 `verified:true` 提交；
- duplicate = 已计分，不是关箱理由（多 flag 继续）；
- close 非 404 失败：**保留 owned、退避重试**；不要先删本地（tch3-05 71min）；
- 模型可能用 raw curl 交 flag：adapter 应轮询平台 `is_completed`，true 就关。

## 6. 讨论与黑板

- 最多 2 轮，每条 200 字，`FACT/HYPOTHESIS/TEST/RESULT/NEXT`；
- 没有新 `FACT` 就停，不要自由聊天；
- `/work/teams/<code>/{facts,hypotheses,tried,candidates}.md`；
- 主控只读摘要，不读完整轨迹；
- 破坏性动作（上传/reset/DB 写）用 `flock`，只允许箱主；
- 每个 worker 一个假设，禁止重复扫同一目录。

## 7. 时间盒（与 team 配套）

- 15 分钟无新原语 → 笔记 + close；
- tier hard：easy 10、medium 20、hard 60；`touch` 一次延长（medium +10、hard +30）；
- 完成（`is_completed`/`flag_total`）→ 10s 关；有 flag 未完成 → 已知 total 只有
  abandon/approval 才允许 close，未知 total 走 600s solved-hold；主动 close 优先；
- 最后 45 分钟不开新侦察；
- 3 容器上限；409 先关最老的。

## 8. 复盘口径

`analyze_events.py` 现在输出 unique 解、flags、总分（49545: 37/47/13800），
首开/额外开箱分开；不要再用“第一面 flag 之和”当总分，也不要把 triage 首开算成重开。

## 9. 适用/不适用

- 适合：50 题短周期、3 容器、8h；
- 不适合：单题深链（ctfy engagement/cve_stage）——那里按“新原语”计时，不套 15 分钟；
- v11 没有 team_dispatch/triage_complete：只能单 worker/手动，别假设 v12 行为；
- 闭卷无日志：看模型调用分布 + 事件计数，不看 stdout。
