---
name: vuln-hunt-code
description: 白盒/源码题与代码审计题的标准管线——枚举每一个用户可控输入（含 handler file:line）、建共享防御目录、按漏洞类前向追踪到 sink、三个闸门（可达/可控/新增能力）、用 mock 危险函数的 Fuzz Harness 动态验证、影响必须带分子分母、全库同模式 sweep 并交叉核对计数。当拿到源码仓库、插件、jar/apk 解包、或需要判断"这段代码能不能打"时使用。【中文触发】白盒/审源码/代码审计/找漏洞/数据流。不负责黑盒在线打点（web/pwn 技能）与提交。
metadata:
  tags: [audit, whitebox, taint, fuzz-harness]
---

# 源码找洞管线

> 蒸馏自 ARTEX `codeaudit`（源自 VulnHunter / bug-bounty-hunting-prompts）+ DeepAudit
> （三闸门、文件验证防幻觉、Fuzzing Harness、证明基准分档）。

## 0. 范围硬规则

只审**生产代码**。不报告、也不追踪穿过：测试代码、构建/打包脚本、vendored/第三方、生成代码、文档。

**例外 1**：仓库里签入的**安全相关基础设施配置**在范围内（反代/nginx 指令、trust proxy、header 透传、
TLS 终止、CORS 策略）——一条把"可信值"变成"攻击者可控"的指令是漏洞，不是运维备注。

**例外 2（CTF / 靶场覆盖，优先级高于上面）**：做题时**范围就是题目给的全部东西**。
CTF 里 vendored 的库、题目自带插件、甚至 `test/` 就是漏洞本体或 flag/oracle 所在；
而且容器里常常 `ls -l /challenge` 列一下、再用 `read` 逐个读 `/challenge/flag*` 就能拿到 flag
（tsec-bench-run v12.5.2 硬规则；**别用 `cat` 读文本**，`read-guard` 会 block 单文件 cat）。
所以：**"不报 vendor/test"只作为降噪优先级**（先审第一方调用方），**不是禁止范围**；
在 CTF 里判断“值不值得看”的标准是“能不能通向 flag/目标”，不是“是不是生产代码”。

## 1. Phase 1：输入清单与切分

枚举**每一个**用户可控输入：HTTP 路由与参数、header、cookie、body、REST/RPC 方法、队列/事件消费者、
CLI 参数、请求时读取的配置与环境变量、文件上传、导入导出端点、webhook/回调、可被外部影响的定时任务。
每个入口记 `handler:file:line`。

同时建**共享防御目录**：中间件、认证包装器、sanitizer、ORM 基类、框架校验器。
（大仓按模块切分并行审时，每个分片只看自己那块，但 Phase 3 验证必须全库搜这些共享防御。）

清单写成工件落盘。**每个输入必须以明确处置收尾**：追到 sink / 测过安全 / 有证据排除。
候选是假设不是结论；清单是"没有静默丢弃"的完整性证明。

## 2. Phase 2：按类前向追踪

一次只追**一个漏洞类**（注入 → 导航/XSS → 业务逻辑与授权 → 文件操作 → 反序列化 → 密码学 → SSRF）。
每个输入固定工具顺序：

1. 读入口 handler，列出该变量**每一处**使用（赋值、传参、插值、拼接）。
2. 逐函数前向追到 sink / sanitizer / 代码边界。
   - **永不停在抽象边界**：router、dispatcher、中间件链、data fetcher、策略选择器、回调 = 分叉，
     每条都追到底。
   - **审每个出站调用点的所有参数**，不只是被追的那个。校验过的 scope 参数（tenant/user/account）
     在校验与调用之间被替换、丢弃、取默认、或被通配符（`~`/`*`/`-1`/`all`）顶掉 = 授权绕过候选，高危。
   - **穷举所有路径**：一个输入用在三处就是三条链；一条安全不能清除另外两条。
   - **早返回只保护它真正校验的东西**：`not null` 挡不住注入。检查的语义必须匹配 sink 的上下文。
   - **跟穿存储**：写进 DB/缓存/队列的输入，要继续追到读它的地方。
   - **跟穿响应**：输入控制出站请求的 scheme/host/port 时，响应体就是攻击者影响的（下游 DOM XSS、
     open redirect）；输入选择资源时，枚举响应映射字段看有没有敏感数据回流调用方（CWE-200）。
   - **每个候选 sink 做传递调用者搜索**：grep 所有 caller，再 grep caller 的 caller，直到入口或穷尽。
3. **先读 sink 源码再下结论**。搜索结果只用来导航，判断必须读源码。

## 3. 三个闸门（全过才算候选漏洞）

1. **可达**：生产环境可达（注册了路由 ≠ 可达；handler 自己的 dev-only 守卫会消掉可达性）。
2. **可控**：攻击者可控的数据确实到达 sink（只有服务端写的值不算，除非它的所有写路径都可控）。
3. **新增能力**：给攻击者带来它本来没有的东西（权限、他人数据、代码执行、越权状态变更）。

任何一个不过 → 只是加固建议，**永远不是 finding**。注释和命名（"by design"/"sanitized downstream"/
"internal only"）不满足任何闸门，只有读到的代码算。

sanitizer 的作用域与 sink 上下文不匹配（HTML 编码器去守 URL、URL 编码去守 SQL）本身就是漏洞。

## 4. 验证：Fuzz Harness（不用跑起整个项目）

这是最高杠杆的一步——**即使整个项目跑不起来，也要能验证**：
1. `extract_function` / `read_file` 取出目标函数。
2. **Mock 掉依赖**（DB、HTTP、文件系统），特别要 **mock 危险函数把调用记下来**。
3. 写测试脚本打多组 payload（正常值 + 每一类分隔符/连接符/编码变体）。
4. 看执行结果判定。

命令注入的最小 harness 骨架：

```python
import os
seen = []
os.system = lambda c: (seen.append(c), 0)[1]     # 记录而不执行
def target(u): os.system(f"echo {u}")            # 项目里的目标函数
for p in ["x", "; id", "| whoami", "$(id)", "`id`", "&& ls"]:
    seen.clear(); target(p)
    print(p, "->", seen)                          # seen 非空 = 注入成立
```

SQL 注入：mock cursor 记录 `(query, params)`；**`params is None` 且 query 里出现引号/`OR`/`--` = 拼接。
XSS/模板：payload 原样出现在输出里且含 `<`/`{{` = 未转义。
反序列化：把 payload 传给 mock 的 `pickle.loads`/`unserialize` 看是否走到危险路径。

**判据**：机制触发 ≠ 目标达成。harness 只证明"危险调用发生了"，最终 finding 仍要按第 6 节给端到端证据。

## 5. 静态结论的最低要求（跑不动时）

静态 PoC 必须含：入口处的**具体攻击者输入值** + 每一步传播的 `file:line` + 到达 sink 的**字面危险值** +
具体影响。标签写 `proof basis: static-analysis-unexecuted`，结论写 **potential**，不许写 confirmed。

## 6. 影响必须可测量

每条 finding 带**分子/分母**："20 个 payload 变体里 3 个返回了他人数据，17 个被拒"；
"命令执行 2 次，有回显：是"。"攻击者可以拖库"是断言，"3/20 命中"是测量。拿不到数字就写清试了什么、为什么不可测。

证明基准三档：`executed-live` > `executed-lab` > `static-analysis-unexecuted`。

## 7. Sweep 与计数交叉核对

对每个确认的根因，全库 grep 同模式；**每个实例都要有独立处置**（单独 finding，或带引用证据排除）。
写总结前自查：Phase 2 的每个候选**恰好出现一次**（confirmed / eliminated / downgraded），
每个带通过 PoC 的确认项各自成一条 finding。**静默丢弃 = 审计失败。**

## 8. 报告结构（缺证据/复现/修复不算报告）

`Overview` → `Impact`（业务后果 + 严重度理由）→ `Measured impact`（数字+分母+防御挡住了什么+证明基准）
→ `Affected scope` → `Reproduction steps` → `Evidence`（原始请求/响应/命令输出，代码块贴原文）
→ `PoC`（可直接跑）→ `Root cause`（哪行/哪个假设坏了）→ `Remediation`（可执行）。

## 9. 防幻觉（DeepAudit 的硬规则，写进每次报告前）

- 报之前**必须** `read_file`/`ls` 确认文件存在；禁止按"典型项目结构"猜路径。
- `code_snippet` 必须是真实读出来的内容；行号必须在文件范围内。
- 匹配技术栈：Rust 项目里不会凭空出现 `.py`；前端仓里不会有数据库 DSN。
- `read_file` 说不存在 → 该候选就是 **false_positive**，不要"猜一个正确的路径"再报。
- **宁可漏报，不可误报**：高置信度少数 > 低置信度一堆。

## 10. 与 CTF 的接法

- 源码题/审计题：第 1–3 节就是主流程；第 4 节 harness 常常比"部署整站"快一个数量级。
- 判断题（"这代码有洞吗"）：先做第 1 节清单，再按类扫；不要一上来精读某个文件。
- 有 diff 的题（补丁/CVE）→ 走 `patch-diff-nday`。
- 审计结论要交 flag/证据时，回到 `ctf-state-search` 的台账格式记录（用 `stall_gate.py beat`，别手搓标记）。

### 10.1 工具缺失时的降级链（本机已确认没有 semgrep/bandit/gitleaks）

```
semgrep/bandit/gitleaks/kunlun  不在 → rg/grep -n 危险函数 + read 读 sink
fuzz 框架              不在 → 手写 python mock harness（第 4 节的 20 行骨架）
ghidra/ghidriff        不在 → objdump/strings/nm + 读源码（或题目自带 diff）
```

**不要为了装工具烧时间**：AGENTS 规定装工具必须先问用户（`bin/tools_check.py` 只提示不安装），
且 TSec 镜像无公网，`pip install` 只会卡住。

### 10.2 边界

- 本 skill **不碰提交**：TSec 走 `framework.cli submit` / `work/sub.py`，CTFY/CTF2 走
  `bin/platform_do.py submit`；裸 curl 提交会被 `platform-guard` 拦。
- 审出来的"漏洞"要拿去拿 flag 时，记 `[P]` 台账行（`stall_gate.py beat`），再回 `ctf-state-search`。
