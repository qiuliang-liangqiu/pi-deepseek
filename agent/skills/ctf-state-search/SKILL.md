---
name: ctf-state-search
description: 把 CTF/靶场当成状态空间搜索而不是照脚本做题：能力原语拼链（RCE 等式 A–F）、事实台账（只记 delta、负结果也记）、正反向搜索中间相遇、识别组件必查情报、被拒必换路、三个闸门才敢说 confirmed。当题目没有现成思路、单点大洞不存在、需要把一串低危凑成链、或者反复卡在同一假设上时使用。【中文触发】状态空间/原语拼链/凑链/没思路/换路。不负责开局分流（solve-challenge）、时间分配（portfolio）、提交与关箱（platform_do / run-gate）。
metadata:
  tags: [ctf, state-space, chaining, evidence]
---

# CTF 状态空间搜索

> 蒸馏自 CyberStrikeAI（pentest-agent-os / pentest-blackboard / capability-primitive-search /
> pentest-verification / zero-day-discovery）与 ARTEX planner 的 hard-floor 语义。
> 本文件是**打法层**，题型分流仍走 `solve-challenge`，时间分配走 `portfolio`，平台动作走 `tsec-bench`。

## 0. 一句话心法

**RCE 不是一个漏洞，是一组能力凑齐后的涌现。** 没单点大洞时，几个 info 级照样能拼出执行。
不问"这有没有已知 CVE"，问"基于手上已确认的事实，下一步最有效的动作是什么"。

## 1. 事实台账（每箱一个 notes.md，边打边写）

不落盘 = 不存在。上下文压缩、换箱、子代理交接都会丢。

> **格式必须交给 `stall_gate.py` 写，不要手搓标记。** 闸门只认两种行：
> `- HH:MM [P] <新原语>` 与 `- HH:MM [-] 无新原语`（正则 `^-(\d{2}:\d{2})\s+\[(P|-)\]`）。
> `[F]` / `[C]` 之类**不会被解析**（台账行数为 0 → 退化成 mtime 兜底，闸门失效）；
> 而且 `[-]` 在脚本里的语义是「**本次无新原语**」，**不是**「排除结果」。

```bash
G="$USERPROFILE/.pi/agent/bin/stall_gate.py"
python "$G" open  <work>/<code> "<target>"        # T0 + 格式说明
python "$G" beat  <work>/<code> "新端口 8080 -> Tomcat 9.0.45"   # 有新原语
python "$G" beat  <work>/<code> -                # 没新原语（只刷新动作时间）
python "$G" check <work>/<code>                   # 退出 1 = 停滞，先写三行
```

`[P]` 行里写清：**什么 + 在哪 + 怎么验证的**（命令/响应片段），链的依赖用 `link:` 字段
（`beat "... link: finding/ssrf -> chain/redis-rce"`）——这样既保住攻击图信息，又不破坏闸门解析。

**负结果不在台账行里**，写到：
- 单机：notes.md 末尾的 `## 已排除` 小节（`CC` 时间戳 + 测了什么 + 结果 + 为什么排除）；
- 多 Agent：`teams/<code>/tried.md`（team skill 的既有约定，别另造文件）。

规则：
- **只写 delta**：先扫一眼已有行，只记新东西；同一结论换个说法再记 = 制造假进展。
- **一个发现一个 key，覆盖更新**，不要拆成十个 key。
- **负结果必须记**。没记的负结果会被下一轮当成没试过，重打一遍。
- 摘要不足时回读正文/证据，**禁止凭摘要臆造细节**。

## 2. 能力原语（把"漏洞"翻译成"我拥有了什么"）

```
read(path)  write(path)  exec(cmd)  ssrf(url)  sqli(read|write)  idor(id)
cred(svc,priv)  oracle(yes|no)  eval_expr  redirect(url)  upload(ext,dir)
```

RCE 只需满足任意一条等式，缺哪个原语就把哪个设成子目标：

| 等式 | 条件 | CTF 常见路径 |
|---|---|---|
| A | 能写文件 + 文件被当代码执行 | 上传 + 解析差异 / 日志投毒 + LFI / 会话文件 + LFI |
| B | 能控配置/env + 配置指向你的代码 | 模板编辑 / 连接串 / 反代配置 / `auto_prepend_file` |
| C | 能进管理面 + 面板自带执行功能 | 后台任务计划 / 插件 / SQL 控制台 / 文件管理器（**功能即原语**） |
| D | 有凭据 + 服务有合法执行入口 | 弱口令进后台 / CI runner / webhook |
| E | 能任意读 + 读到凭据 + 凭据可登录执行点 | 源码泄露 / `.git` / `backup.sql` / `proc/self/environ` |
| F | 能控数据 + 数据流入危险 sink | SSTI / 反序列化 / 原型污染 / SQLi 进 eval |

低危 → 原语的翻译表（这是"鸡肋变碎片"的关键）：

- info 泄露（.git/备份/堆栈/trace）→ 源码、路径、密钥 → 喂 B/E/F
- LFI/任意读 → 读配置/密钥 → E；或日志/会话投毒 → A
- SSRF（哪怕只 GET）→ 打本机 Redis/Consul/K8s/云元数据 → C/D；gopher 扩成任意协议
- 弱/默认/复用凭据 → 进带任务/webhook/插件/CI 功能的后台 → C
- 可控上传（哪怕限扩展名）→ 配路径穿越/双扩展名/`.htaccess`/空字节 → A
- SQLi（哪怕只读）→ 读 hash/密钥 → E；或 `OUTFILE`/`COPY TO` → A
- 模板可控 → SSTI → F；可控 JSON → 反序列化 → F

## 3. 搜索：正向 / 反向 / 中间相遇

- **正向**：对每个原语问"能解锁什么"；对每对原语问"组合出什么"（read+write=改配置；ssrf+redis=RCE；sqli+FILE=webshell；idor+massassign=改他人 admin）。
- **反向（卡住时主用）**：锁定 Goal=执行命令 → 选最接近现状的等式当模板 → 缺的原语设成子目标 → 手上哪个低危/功能/泄露能凑出它 → 凑不出就递归拆或换等式。
- 正反向在中间相遇 = 完整链浮现 → **逐段验证**，每段都要有真实输出钉住。

## 4. 突破口清单（看到别走开）

- **功能即原语**：导入导出、webhook、模板/主题编辑、任务计划、SQL 控制台、插件安装、备份恢复 —— 合法功能登进去就是现成的读写/执行。
- **跨协议跳**：SSRF 的 `gopher://dict://file://`；上传 → phar；LFI → `php://filter`、`data://`。
- **解析差异**：校验器 vs 执行器（WAF vs 后端、nginx vs Tomcat、`getPathInfo` vs `getServletPath`）。
- **输入契约误读**（最高性价比）：先发最小可判别对 `10` vs `0x10`、`0` vs `00`、`1` vs `1.0`、裸行 vs 带长度 —— 一次进制误读能废 1 小时。
- **时间维度**：TOCTOU、token 可预测、缓存投毒、竞态窗口；把"偶尔"变"稳定"。
- **跨域兑现**：每拿一个能力问"它在别的域值多少"（web 读文件 → 云元数据 → 接管）。
- **创造模式**：重审能力边界（只能读 `/var/log`？那 `/proc/self/environ` 呢）；列"我以为不可能"逐条问"凭什么不可能"。

## 5. 三条强制触发器（违反=幻觉/放弃/遗漏）

1. **识别 → 搜（按网络条件分叉）**：识别出组件/框架/版本 → 暂停利用，先查情报。
   - **有网**：CVE 库 / 厂商 advisory / 公开 PoC / 中文社区（查产品级情报，不查本题）。
   - **脱网（TSec 镜像、闭卷跑分）**：**不要**去 curl NVD / GitHub / 搜索引擎，也不要 pip/apt 装东西
     （`skills/tsec-bench/SKILL.md` 明写 No public internet）。改用本地情报：
     `<workbench>/docs/PLAYBOOK.md`、`docs/PITFALLS.md`、`docs/CASELIB/INDEX.md`（若已生成；没有就跳过）、
     `python -m framework.cli status|next` 的 SKIP 名单、`skills/ctf-kb`（`python scripts/kb.py find …`）。
   - 搜完才继续；不搜就说"无已知漏洞"= 幻觉。
   - **允许按题名/比赛名搜 writeup 与公开题解**（2026-11-22 用户改口），但只能**蒸馏成方法**，
     不抄它的字面 flag（实例常随机化）；平台单独禁某题时平台优先。吸收流程见下面「writeup 吸收」。
     **禁止**把"搜到 writeup 里有这个漏洞"当成 target 侧已验证（仍要自己发一次拿到回显）。
2. **被拒 → 换路（先分清是谁拒的）**：
   - **平台侧**（`503 platform_busy`、`20488 字节 HTML`、`code=000`、`409 max active`）→ 这是限流/维护/槽位满，
     **按平台规则退避重试**（或先关最老的箱），**不是**"目标拒绝"，别去换出口。
   - **目标侧**（403/429/WAF 拦/CDN 502/空响应/RST）→ 禁止同条件重试 → 换编码 → 换方法 → 换路径
     → 换协议 → 换端口。全换完仍不通才写负结果并换面。
   - 换出口/代理只在**平台允许且不突破速率纪律**时用（默认 ≤20 req/s、并发 ≤10；平台有上限取 60%）。
3. **新认知 → 横向（先小样本）**：拿到凭据/新域名/新路径 → 立刻验证复用性（**≤10 次单发**：同服务换账号、
   同账号换服务），有新信号再按 AGENTS「加码前置门」走批量（先写结构性证据，否则单次 ≤5 分钟/2000 请求）。
   "全端口扫"只在有可观测窗口或授权范围内做，不等人指示 ≠ 不限速。

## 5.1 writeup 吸收（找到题解之后怎么用）

找到题解只是开始，不蒸馏 = 下次还得再搜一遍。四步：

1. **只抽方法与坑**：输入契约（进制/长度/编码/截断）、入口与参数名、绕过手法、关键命令、失败原因。
   跳过叙事、跳过“我当时怎么想的”。
2. **写成可执行卡片**：`原语/漏洞类 → 具体命令或 payload → 适用条件 → 怎么验证成功`。
   例：`session.upload_progress + LFI → 文件名注入 PHP → 需要 session.upload_progress=On`。
3. **本机化**：换成这台机器的命令/路径/工具（镜像缺 tools 时用 python 等价实现）；
   题解里的密钥/端口/路径常量一律当**未知**，标 `?` 等实测。
4. **验证后落库**：自己发一次、拿到回显 → 记 `[P]` 台账行 → 再写进
   `~/.pi/agent/playbooks/` 或对应 skill（带来源 URL + 适用条件）。下次直接调用。

四个坑：
- **版本不同**：题解里的绕过可能在新版已修（先确认 banner/版本）。
- **字面 flag 不可信**：实例常随机化（先 `work/flag_randomized.py` / `work/calibrate.py`）。
- **题解 ≠ 证据**：写进 notes 的只能是“我自己跑出来的”。
- **版权**：只吸收方法/命令，不复述大段原文（ARTEX/DeepAudit 类文档还分 AGPL/MIT）。

## 6. 验证铁律（全系统最高规则）
```
1. 搜到 ≠ 打穿。公开 PoC/版本匹配只能写 tentative，不能写 confirmed、不能当 flag。
2. 每个 confirmed 必须附证据：命令输出 / HTTP 响应 / 文件内容 / 回连记录。
3. 禁止"可能/疑似/应该是"。要么确认+证据，要么标 tentative，要么不写。
4. 验证失败 → 写 [−] 负结果，注明条件。
5. 想象力拉满，单步验证零容忍：整条链每段都钉死才算成立。
```

**反向证据同样要够硬**：说"这条路死了"必须引用具体的请求/响应/控制；"大概下游校验了""看着像设计如此"不算排除。

## 7. 时间盒（统一 15 分钟，别再记 14）

| 层 | 阈值 | 谁在管 |
|---|---|---|
| 人工判据（AGENTS 硬规则） | **15 分钟**无新原语 → 三行笔记 + 换目标 | 你 |
| `stall_gate.py check` | 默认 `--minutes 15` | 脚本退出码 1 |
| `run_gate.py abandon` | notes **≤900s（15 分钟）**内更新 **且** 含 stall/无新原语/放弃 字样 | 脚本拒/放 |
| clock / `auto` / watch | 15 分钟（`TBENCH_STALL_CLOSE_S=900`，`--stall 15m`） | 平台/harness |
| 分层时间盒 | easy 5/10 · medium 10/20 · hard 30/60 · cred 5/5 · intel 20/45 | `framework.cli run --tier` |

- 新原语 = 新端口/新参数/新报错差异/新凭据/新路径/响应内容或长度变化/通过性变化。**写脚本、跑爆破、看日志不算。**
- 触发后写三行 `当前假设 / 已排除 / 下一招`，然后二选一：换目标，或给出一条**具体**的新原语说明为何继续。
- 写不出新原语，就不许继续同一个假设；不许用"再跑一组字典"代替判断。
- **多 flag 未完成箱**：不能靠时间盒关箱（`run-gate` 会 block）。要么继续到 `is_completed`/`flag_total`，
  要么 `run_gate.py abandon --code <c> --stall --reason '...'`（notes 必须新鲜），要么等用户 TTY approval。
- 加码（爆破/批量/并发）前必须先写**结构性证据**：不限流 / 空间 ≤ 2^16 / 有明确 oracle / 窗口可观测。
  没有证据 → 单次 ≤ 5 分钟 / 2000 请求。

## 8. 收尾与交接

- **先落盘再总结**：任何"只在我脑子里"的结论都算没做过。
- **落盘 ≠ 提交 ≠ 关箱**：本 skill 不碰提交。TSec 走 `framework.cli submit` / `work/sub.py`，
  CTFY/CTF2 走 `bin/platform_do.py submit`（`platform-guard` 会拦裸 curl）；关箱受 `run-gate` 管。
- **收尾 ≠ finish**：能不能结束由 `bin/run_gate.py` 判（所有已开箱 `is_completed`/`flag_total`，
  或平台 `invalid_state`，或 env-fail，或用户 TTY approval）。本 skill 只负责"这一条假设该不该继续"。
- 收尾只写真实做过的事：拿到了什么 flag/证据 / 走了哪几条链 / 哪条被排除及原因 / 下一步建议。
- 交给子代理或主控时，交付物必须带「待落库」条目：`key / summary / body(证据) / links`，不要假设对方已经记了。
- 零结果也是结果：`class X 在 surface Y 上用方法 Z 测过，干净` 要写出来，别让"没找到"读成"干净"。
