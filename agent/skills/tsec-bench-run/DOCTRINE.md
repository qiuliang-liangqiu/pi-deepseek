# 作战条令（源自《Pi 智能体高分训练与架构演进深度实战笔记》v2.0 蒸馏）

> 来源：`%USERPROFILE%\Desktop\PI智能体高分训练与架构演进深度实战笔记.docx`
> 用途：跑分/CTF/渗透任务时的行为准则 + 做训练数据、SFT、GRPO 时的规范。
> 标注：`[可执行]` = 现在就能照做；`[训练侧]` = 做数据集/微调/RL 时才用；`[基建侧]` = 描述 Pi 内核架构，不要假设本地已存在同名机制。

---

## 0. 一句话核心

跑分高低不是"模型不够聪明"或"提示词不够长"，而是**上下文腐败 + 执行阻塞 + 工具认知过载**。
解法 = 轻量无状态执行体 + 角色分治 + 外部知识库接地 + 失败时的自愈策略。

---

## 1. 五条反模式（我自己最容易犯的）

| 反模式 | 表现 | 对策 `[可执行]` |
|---|---|---|
| 上下文腐败 Context Rot | 把 nmap/ffuf 全量回显塞进上下文，15 轮后忘记第 1 轮的端口，开始复读同一命令 | 回显只留**端口/路径/状态码**，其余 `[Filtered N lines]`；关键事实写进外部笔记（黑板/文件），不靠记忆 |
| 工具过载 | 一次性列出上百个工具，注意力花在"选工具" | 每轮只暴露**当前阶段需要的 3-5 个动作** |
| 全能视角幻觉 | 同一个执行体既做地毯式探测又做精细链构造 → 扫不全就打，打穿了忘抓 flag | 拆 **Recon（发散采样）** 与 **Exploit（聚焦验证）**，靠事实板交互，不共享对话历史 |
| 死循环 | 同一报错连续重试到轮数耗尽 | **连续 2 次失败必须换链**：换路径 / 换链 / 反向请求侦察 |
| 外网依赖 | 脱网沙箱里 curl google / 在线查 PoC → 超时崩溃 | 只用本地知识（模板库、字典、离线检索），零公网请求 |

---

## 2. 四步推演法（每个有破坏性/消耗轮数的动作前强制执行）`[可执行]`

1. **现状评估**：现在哪个阶段？上一轮回显是成功还是报错？→ 1~2 句事实判断
2. **假设构建**：目标内部可能是什么情况？有无防御/中间件/版本约束？
3. **最小验证**：最轻量的命令/PoC 验证该假设（优先用现成脚本，再考虑手工构造）
4. **回退预案**：这步失败（403/500/超时）下一步走什么？试错上限 2 次

> 落地：subagent prompt 里显式要求"每步先写这四行再执行"，比只给目标有效得多。

---

## 3. 双工分工（Recon × Exploit）`[可执行]`

- **Recon Specialist**：端口/服务指纹/版本 banner → 目录与接口枚举 → 离线漏洞检索 → `写入事实板` → 响应 Exploit 的 `need_recon`
- **Exploit Specialist**：读事实板 → 装配 PoC/EXP → 抓 flag → 提交；连续 2 次打不通就发 `need_recon` 回给侦察侧
- 两侧**不共享对话历史**，只交换结构化事实：`目标 / 端口 / 指纹 / CVE 编号 / PoC 路径 / 已试过的失败 payload`
- 主控只做排产、派活、回收 —— 主控亲自攻坚是历史已验证的低效行为

---

## 4. 抗逆自愈三模式（训练集必须含 30% 以上，实战遇到即套用）`[训练侧]`+`[可执行]`

- **A 路径偏移**：`/api/v1/user` 404 → 怀疑上下文根/反代前缀 → 用 `common.txt` 爆破真实前缀（如 `/system/api/v1/user`）
- **B 利用链失效切换**：ysoserial CC1 失败（JDK > 8u71）→ 换不依赖 `InvokerTransformer` 的 CC6 / CB1
- **C 情报盲区反向请求**：Exploit 连 2 次打不通 RCE → 发 `need_recon 请深度枚举 /actuator、/heapdump` → 侦察侧定向补探

---

## 5. 数据与训练（做微调/RL 时照此执行）`[训练侧]`

**轨迹清洗四重过滤**：① 冗余回显截断 → ② 试错/语法错误剪除，只留"决策-行动-回显"因果链 → ③ 统一成标准协议（`ACTION shell|http|nuclei|cve|intel|need_recon|submit`）→ ④ 用强推理模型逆向补全 `<think>` 思考链。

**SFT 关键：精准 Loss Masking**
- `loss_mask = 0`：System Prompt、环境 stdout、黑板状态（这些是**环境状态**，不是模型该学的内容）
- `loss_mask = 1`：只学 `<think>...</think>` 与 `ACTION ...`
- 收益：快速收敛、语法严格、根治"把系统报错当成自己发言"的幻觉

**GRPO 而非 PPO**：稀疏夺旗任务里 Critic 价值估计易崩且要额外一份同规模权重；GRPO 对同一样本采样一组候选、用组内相对优势更新即可。

**复合奖励（可作模板）**
```
R = w_flag·R_flag + w_recon·R_recon + w_bb·R_blackboard + w_fmt·R_format − P_penalty
R_flag:  真 flag 且成功 submit  +10.0 ；伪造 flag  −2.0
R_recon: 首个存活端口 +0.5 ；精准匹配 CVE + 有效 PoC +1.5 ；敏感路径 200 +0.8
R_bb:    规范 intel +1.0 ；need_recon 被采纳 +0.8
R_fmt:   协议合规 +0.2 ；四步推演完整 +0.3
P:       连续 2 轮同一失败命令 −1.5 ；访问外网 −3.0 ；单轮超长死循环 −2.0
```
设计原则：**稀疏终极奖励 + 稠密过程奖励**，惩罚项只针对"可判定的坏行为"（复读、外联、超长），不要惩罚"没打通"。

---

## 6. 知识底座（脱网沙箱的"外挂武器"）`[基建侧]`

- 全量 YAML PoC 挂 `/root/nuclei-templates/`（`cve/` `vulnerabilities/` `wuying/` `extra/`），默认 `-silent`、**禁止触发云端模板更新**
- CVE 库用 SQLite **FTS5** 建索引，检索按 `poc_ref` 非空优先 + CVSS 降序，返回结果**直接给出可复制的 nuclei 命令**
- 字典按场景分类（`directoryDicts/common.txt`、`spring/`、`apiDict/`）并软链到 `/usr/share/wordlists`
- 战术维基 `/opt/security_wiki/`：反序列化链搭配、盲注脚本模板、提权技巧
- 原则：**参数化记忆负责"想"，外置知识库负责"精确"** —— 具体 Payload 一个字符的幻觉就全盘失败

---

## 7. 部署与混合架构 `[基建侧]`

- TSecBench 镜像：`docker build` → `docker save | pigz -6 > agent.tar.gz`（≤3.0GB）→ 网页上传 → 运行时注入 `OPENAI_API_KEY` / `MODEL_NAME`，`BENCHMARK_TOKEN`、`BENCHMARK_BASE_URL` 由沙箱动态注入，**0 硬编码**
- 生产形态：**Hermes 主控（跨目标编排、状态持久化、凭据、证据链、报告）+ Pi Worker 集群（毫秒冷启、一任务一生灭、只回传核心事实）**
- 自愈：题目槽位看门狗 + `reconcile_orphaned_slots`，网络抖动/靶机异常自动释放重建，避免悬挂占名额

---

## 8. 复盘沉淀（默认行为，不需要额外一步）

库的位置：`<workbench>/2/docs/CASELIB/`（`framework/caselib.py` 实现，`cli close` 自动触发）。

| 来源 | 去向 |
|---|---|
| `work/bb_<code>.md`（解出） | `CASELIB/<family>/<code>.md` + 自动删黑板 |
| `work/bb_<code>.md`（未解） | `CASELIB/unsolved/<code>.md` |
| `work/<code>/FINDINGS.md` | 同上（正文整段保留，不强行结构化） |
| 无记录 | 静默跳过 |

设计约束（这几条是刻意的）：
- **flag 在笔记里脉敏**，原值只存 `_raw/`。库要能被复用/分享，不能变成泄题存档。
- **没解出也要存**。`unsolved/` 里的“这个端口 405 说明只有 GET”比成功案例更省钱。
- **归档失败不影响 close**。释放容器永远优先，库是副产物。
- **不要手写第二个笔记文件**。只维护黑板，close 时自动成型；否则两份记录必然发散。

## 9. 分配纪律（portfolio：时间花在哪，比花得快不快更决定分数）`[可执行]`

证据来自 2026-11-16 闭卷一轮（50 题 / 7h51m，28 解 / 12000 分，
原始 `<run-id>-events.csv`，可用 `skills/tsec-bench-run/tools/analyze_events.py` 复算）
与 2026-11-17 的 `<run-id>`（2.94h，9 解 / 2000 分；Saltern 已拿 2 flags 后重开 3 次，
88 分钟 0 分）、2026-11-18 的 `<run-id>`（7.89h，37 解 / 47 flags / 13800；
triage 50 题 15 分钟全开、20:06 提前结束；剩 53 flags；tch3-05 得分后 71 分钟才关）：

- 28 次**做出题**的 stint：中位 **5.3 分钟**，合计 6.09 容器小时；
- 39 次**没做出题**的 stint：中位 **16.5 分钟**，合计 **12.47 容器小时**（≈5.3h 注意力，占全场 2/3）；
- 61% 的开箱（41/67）是重开旧目标；最后 30 分钟开 10 个箱、0 分。

→ 手不慢，是钱花错了地方。以下六条按“最容易犯”排序：

| 规则 | 硬阈值 | 本轮依据 |
|---|---|---|
| **30 分钟 triage** | 开局 2h/3min 改为 30min/30–60s；3 条 lane 一箱一个，`triage.sh` 过 50 题浅指纹（title/headers/5 路径/JS/banner），写 A/B/C/D；20 分钟紧、30 分钟稳 | 官方 difficulty/score 是白送的；真正缺的是“有没有 5 分钟入口”；旧 2h 规则太慢 |
| **已完成题不重开** | 只有 `is_completed: true` 或 `flags >= flag_total` → 不 start；**不是 2 flags**；`force` 仅用于有证据的剩余 flag | Saltern 2 flags 后重开 3 次，88 分钟 0 分；Aperture 解出后停槽 5h |
| 止损即关箱 | 12–15 分钟无新原语 → 三行笔记 + `close` | 失败 stint 中位 16.5min，是成功中位的 3 倍 |
| 禁止空挂 | 不在打的箱一律关；并发槽只留给后台任务（ffuf/hashcat/长扫/编译） | Network Monitor 空挂 131min、Publishing Platform 63min，均 0 分 |
| 重开必须有新招 | 重开前先读旧笔记，写下“这次的不同点”，否则不重开 | 41/67 次开箱是重开；Upload Board 被开 6 次 4/28/2/1/3/19min |
| 收尾冻结 | 最后 45 分钟不开新侦察，只执行队列里已写好的“下一招” | 末尾 30 分钟 10 开 0 分 |
| **解出后按 flag_total 找剩余 flag** | 单 flag 题 `is_completed` 后 10 秒关；多 flag 题（截图 2/6、3/6）继续到 `is_completed`/`flag_total`，不要 `flags>=2` 就关 | 100 flags/50 题；49545 只拿 47，剩 53；旧逻辑 2 flags/180s 提前关 |
| **close 失败要重试** | 非 404 失败保留 owned、退避重试；adapter 轮询平台 `is_completed` | tch3-05 得分后 71 分钟才关；单 flag 题 180s 白等 3 分钟 |

配套硬要求：
- 每箱关闭时必留三行笔记（入口 / 版本 / 已试+反例 / 下一招），否则重开就是重新热身；
- 复盘必须量化：跑完把 `run-*-events.csv` 丢进 `tools/analyze_events.py`，看四个数
  —— 失败 stint 中位、重开率、未开题比例、尾声 30 分钟开箱数。只看总分会把“运气好碰到高分题”当成进步。

### 适用范围与冲突消解（别把纪律用成自残）

| 冲突点 | 正确用法 | 反例/代价 |
|---|---|---|
| 单题深链（ctfy engagement、`cve_stage`、hard 单题） | **不套用这六条**。判据是“最近 15 分钟有无新事实/里程碑”，不是“有无 flag” | 硬套 15 分钟会把 60–120 分钟的 CVE 链/堆布局切碎 |
| 闸门 = 原语，不是总时长 | 只砍“无新原语”的停留；有新原语就继续到 tier 硬截止 | 本轮 6 个成功 stint >15min（Atlas 59min/+1000、EchoDeck 40min/+800），按总时长砍要丢 3600 分（=29% 总分） |
| `hold` vs `close` | `hold` 仅用于“已有可复现原语 + 明确下一招 + 等后台产物”，并写进笔记；其余一律 `close` | 把 `hold` 当“舍不得”用，就会复现 131min 空挂 |
| 3 并发 vs 禁止空挂 | 上限是“最多”不是“要开满”；单执行体时空槽不扣分；多 worker 时每箱都有主人，不算空挂 | 为了占满槽位而开箱＝让自己的墙钟变长 |
| 第二面 flag 窗口 | **仅当卷面 flag 总数 > 题目数**（如 100/50）才做；单 flag 平台直接跳过 | 单 flag 跑分平台 28×3min = 84 分钟纯浪费 |
| 收尾冻结 | 冻结的是“摸黑开箱/新侦察”，不是开箱本身；已有笔记下一招、指纹已知的低分题仍可开 | 本轮 21:53 开 News Archive、3 分钟 +300，属于应该保留的那类 |
| 代码实现 | 要真执行“15min 无原语关”，得新加 `--stall`；**不要复用 `--idle`**（现语义只在硬截止后才关）也不要用 `--hard`（无条件杀） | 改 `--idle` 会让所有长活被硬截止前的空闲误杀（`governance.decide_watch` 默认只警告就是为这个） |
| 子代理 3/箱 | 默认 3 worker/箱（recon/exploit/verifier）、3 箱共 9；triage 1/箱；429/CPU 降 1–2；主控唯一平台工具，worker 只 bash/read/write、无 token | 用户明确 token 换效率；3/箱不等于 3 题，容器槽位仍是箱子；parallel 是 Promise.all，不要 batch 9 等最慢 |

### v10 增量（2026-11-17）

- **triage 不是 3 人一题**：3 条 lane 一箱一个，才能 20–30 分钟过 50 题；3 人一题只过 3 题。
- **完成闸门**：`is_completed`/`flag_total` 后重开是纯浪费；2/6、3/6 还有剩余，不能按 2 flags 关。
- **子代理**：`recon/exploit/verifier`；主控持有 `start/close/submit`，worker 只打 `container_addr`；
  blackboard `/work/teams/<code>/`，`flock` 串行破坏性动作；flag 只由主控复核后提交。
- **parallel 语义**：一批任务会等最慢的返回；不要为了“同时开 9 个”把 9 个塞进一个 call，
  按箱/按假设分成小批，避免空闲槽被最慢的拖住。
- **20–30 分钟 triage 的边界**：只能做浅指纹/A-B-C-D 分档，不是“50 题全解一遍”；
  真正的深度判断留给开箱后的 5–15 分钟。
- **v11 默认**：15 分钟无新原语自动关（touch 续命）；hard/600+ 自动派 1 recon；
  `submit_flag` 无 verified 只警告；30 分钟 triage 提醒写 `TRIAGE.md`。
- **v12/v12.4 一箱一队、不跨箱**：默认 3/箱（triage 1/箱，429/CPU 降 1–2）；`team_dispatch`/`team_poll` 取代
  `subagent tasks`（后者等最慢）；箱 done 才 close/cancel/下一题；第一面 flag 多 flag 卷
  不等于完成；close 只 cancel 本箱 job；讨论 2 轮、200 字、无新事实停。
- **v12.1 triage 强制**：前 30 分钟 start 标记 TRIAGE，adapter 在 120 秒自动关；
  auto-recon 关闭；只能 triage.sh + A/B/C/D + close；30 分钟后恢复正常做题。
- **v12.2 提前结束**：全部 open 题的 `triage.txt` 就绪 → `triage_complete` 校验通过 →
  `triage_done=true` → 立即 A→B，不等 30 分钟。
- **<run-id> 验证**：triage 15 分钟覆盖 50 题、20:06 提前结束；37 解/47 flags/13800。
  但 `flags>=2` 不是完成：截图 2/6、3/6，剩 53 flags；单 flag 题 180s 白等；
  tch3-05 71 分钟是 close 失败/状态丢失。v12.3 已按 `flag_total`/`is_completed` 关，
  completed 10s、有 flag 未完成 600s 兜底、close 失败保留/重试、adapter 30s 轮询
  `is_completed`、start 对已得 flag 的 closed code 默认拒绝、team_dispatch 3/9 上限。
- **v12.5**：clock/run_status 带 `team Nr/Md` + `CANDIDATE`；jobs 持久化
  `/work/teams/jobs.json`，pi 重启后旧 running 标 `orphaned`；仍不自动提交/关箱。

## 10. 对文中数据的态度（别当圣经）

`18.5%→72~81.5%`、`30MB 内存`、`14.6w PoC`、`95.8% 工具准确率` 属白皮书口径，缺少题目集/模型/并发等实验条件。**方法可学，数字需自证**。对外引用前必须补基准说明，或用本地实测复现。
