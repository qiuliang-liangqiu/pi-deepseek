# 跑分作业流程（SOP）

> 目标：有限时间内把「得分/分钟」最大化。所有决策都围绕这个比值，而不是「解出某题」。

## P0 前置（失败即终止，别硬跑）

1. `python -m framework.cli preflight` → `status=ok`。不通就**立刻停**，先把 VPN 连上。
   （等价手检：`curl http://<vpn-check-host>`；`preflight` 同时检查 `TSEC_TOKEN`，缺失也退出 1。）
   - Windows 无客户端时：`winget install OpenVPNTechnologies.OpenVPN`，提权后
     `openvpn --config x.ovpn --log vpn.log`，日志出现 `Initialization Sequence Completed` 才算通。
   - 判据只看这个内网地址，不要用平台域名代替（平台在公网可达，会误判）。
   - **有 OpenVPN GUI 的 Windows（本轮实测流程）**：把 `.ovpn` 拷进 `C:/Users/<u>/OpenVPN/config/` →
     **重启 openvpn-gui.exe（不重启它不会重扫 config 目录）** → `openvpn-gui.exe --command connect <name>`，
     日志出现 `Initialization Sequence Completed` + 推送路由 ADD 成功才算通。
   - **僵尸 TAP 抢路由（本轮撞的坑）**：上一轮残留的 TAP 还连著、metric 更低，包全进死接口。
     识别：`ipconfig` 看到两个 `10.254.1.x`；`netsh interface ipv4 show interfaces` 看两个 TAP 都 connected。
     判据：`curl --interface <新IP> http://<vpn-check-host>` 能通、裸 curl 不通。
   - **千万不要用 `netsh interface set interface ... admin=disabled` 停僵尸网卡** —— 它会把推送路由一并删掉，
     整条 VPN 断。正确姿势：`openvpn-gui --command disconnect <name>` 再 `--command connect <name>`，
     恢复后 `route print | findstr <vpn-check-host>` 确认 /32 路由在。
2. `TSEC_TOKEN` 缺失/`task_not_found` → 停止并报告，不重试。

## P1 侦察与排产（1-2 分钟，决定后续 80% 效率）

- `GET /challenges` 拉全量，本地算：
  `待做 = 未通关`，按 `难度(easy→hard) → 分值(低→高)` 排序。
- **先做 easy**：本套题里 easy 200 分往往 1-3 分钟能出，medium 300 分 3-8 分钟，
  hard 500 分可能 20 分钟以上甚至打不动。单位时间收益 easy > medium > hard。
- 记录每题的 `container_status`；平台有**最大并发容器数**（默认 3，409 自动学习并写入 `work/capacity.json`，`TSEC_MAX_ACTIVE` 可覆盖），这是我们并行的硬边界。

## P2 单题循环（核心节奏）

```
start -> 指纹(3-5 个请求) -> 选打法族 -> 打 -> submit -> close
```

纪律：
1. **start 后立刻记地址**，解完/放弃**必须 close**，否则挤占名额（并发上限默认 3，409 自动学习）。
2. 单题设 **分层时间盒**：
   - easy：软 5 / 硬 10；
   - medium：软 10 / 硬 20，允许一次 +10（需新原语/验证假设）；
   - hard：软 30 / 硬 60，允许一次 +30（需书面里程碑）；
   - credential：软 5 / 硬 5，建议 `--tier credential --hard`，找不到凭据来源直接关；
   - intel：软 20 / 硬 45，允许一次 +15（找到 commit/regression/PoC）。
   `run <code> --watch` 默认按难度取 tier；软截止=停止开新方向，硬截止=返回/关闭。
   默认只警告；`--close` 在硬截止+空闲时关，`--hard` 到硬截止无条件关；
   延长用 `extend <code> 10m "新原语"`，主控批、最多一次。
   沉没成本是这类比赛最大的失分点，但“误杀可做题”是更贵的错误。
2b. **止损熔断**：单题两轮 0 新增 → 先 `hold` 或直接 `close`；别在同一个入口反复换 payload。
   “缺凭据型”（要 panel 密码 / SSH 密码 / 不存在的校验接口）最容易无限堆字典：
   本轮 b-01 投了 3 轮 45 分钟、收益 0，同期其他人同样时间拿了 2400 分。
   破局点是问“凭据应该从哪来”，而不是把字典从 4 位堆到 1.4 万条。
2c. 坏题只验一次：`python -m framework.cli check-skip <code>` 先查；
   确认坏题后 `python -m framework.cli skip <code> <证据>` 写入 `work/SKIP.txt`，
   `next`/`run`/`auto` 会自动排除（`--force` 才重试）。
3. 只在"差一点、且分高"时才考虑 `hint`（会按比例扣分，默认不看）。
4. `submit` 返回 `duplicate` 说明该 flag 已计分，直接跳过；`correct:false` 说明思路错了，
   别反复试同一 payload。

## P3 并行化（本套题最大提速点）

- 平台允许的最大容器数（默认 3，409 自动学习）→ **开对应条数的并行流水线**（主 agent + N-1 个 subagent）。
- 给每个执行体的约束必须写死：
  - 同时只保留 **1 个**容器（避免互相抢名额导致 409）
  - `start -> 打 -> submit -> close` 一步不落
  - 单题用 `run <code> --watch` 托管；有活动就 `touch <code>`，要保题就 `hold <code>`
  - 坏题先 `check-skip`，确认后 `skip`，不要重复派发
  - 只能访问分给自己的 code
- **关键收益来自"套路共享"**：第二轮开始把上一轮验证过的套路写进派发 prompt
  （指纹特征 + payload 模板），后续 agent 的命中率会显著上升。

## P4 收尾

- `close` 所有自己开过的容器（框架里有 `panic` 子命令）。
- 汇报：`通关数/总数`、`总分`、`未解项 + 归因（能力/环境）`。
- 归因必须可验证：环境类问题要给出**证据**（端口扫描、DNS 解析失败、源码/compose 差异），
  不要用"可能"糊过去。

## 决策树：什么时候放弃一道题

| 现象 | 判断 | 动作 |
|---|---|---|
| 30s 内能看出漏洞类型，payload 一两次没中 | 继续 | 换 payload 变体 |
| 依赖的服务/端口不存在（DB 不解析、端口全关） | 环境缺失 | 立刻 close，别再投入 |
| 需要的凭据只存在不可达的数据里 | 环境缺失 | 同上 |
| 竞态/概率型，单发命中率 <5% | 提高并发而不是加时间 | 见 `race.py` |
| 一直 409 max active | 名额问题 | close 自己的题再 start |

## P5 结论纪律（本轮最贵的两条，别再犯）

1. **"不可解"必须本人复现后再下**。
   - 概率型失败 → 先换并发编排（`framework/race.py`），再谈"理论不可行"。
   - 环境缺失 → 必须给证据：同平台对照组 / 端口扫描 / 解析错误原文。
2. **子任务隔离**：subagent 用独立 `work/<code>` 目录；只回流结论与脚本。
3. **探测截断**：`| head -30` 或 `Response.brief()`，避免大响应烧上下文。

## P6 代码强制的纪律（2026-11-14 起）

| 规则 | 命令 | 行为 |
|---|---|---|
| VPN 预检 | `python -m framework.cli preflight` | 必须 `status=ok`，否则退出码 1 |
| 坏题登记 | `skip <code> <证据>` / `check-skip <code>` / `skips` | 写入 `work/SKIP.txt`，`next/run/auto` 自动跳过 |
| 分层时间盒 | `run <code> --watch` / `--tier` / `extend` | easy 5/10、medium 10/20、hard 30/60；软截止停新方向，硬截止关 |
| 活动心跳 | `touch <code>` | 更新 `work/<code>/.heartbeat`，看门狗据此判断“还在做” |
| 保题 | `hold <code>` | 看门狗禁止关闭；`unhold` 恢复 |
| 双地址探活 | `run` / `auto` 自动执行 | start 返回多个地址时逐个 TCP 探活，选可达的那个 |
| 容器上限 | `status` / `work/capacity.json` / `TSEC_MAX_ACTIVE` | 默认 3；409 文案 `(N)` 自动学习；缓存只做提示，start 仍先尝试 |
| 强制关闭 | `watch <code> --close` / `--hard` | `--close` 仅空闲超时；`--hard` 到点无条件（不推荐） |
| **停滞闸门** | `watch <code> --stall 15m [--close]` | 按“无新原语”计时（`.primitive`/`.heartbeat`），优先于时间盒；默认只警告。有新事实必须 `touch` |
| **笔记闸门** | `close <code>` | 投入 >5min 无笔记 → exit 4 拒关（`--force` 放弃记录；`panic` 不受限） |
| **纪律面板** | `report [--stall 15m]` | 本地不需 token：按停滞降序列出投入/停滞/笔记/hold，已关（`.closed`）的不入表 |

> 原则：**时间盒是提示，不是铡刀**。默认 `--close off`，只警告；宁可多花 2 分钟确认，也不要误杀一道能做出来的题。
> 但**停滞闸门不是时间盒**：15 分钟没有新原语（`touch`/`.primitive`）就是沉没成本，关箱写笔记比换 payload 便宜。

## 时间预算（按"总时限未知"设计）

默认假设总时长 ~3 小时：
- 0–10min 前置 + 侦察 + 排产
- 10–150min 三路并行刷题（easy→medium）
- 150–170min 收尾：hard 兜底、close 全部容器、汇报

> 不知道总时限时，**按最坏情况排产**，宁可早收尾也不要最后 10 分钟还在啃 hard。
