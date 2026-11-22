# Skill provenance

本目录的 skill 按来源分四类（自研 / 移植压缩 / 导入 / 蒸馏），**升级或再分发时请保持这份对应关系**。
（历史上这里写过固定计数，已改为不写数字，避免每次新增都要改。）

## 自研（7）

| skill | 说明 |
|---|---|
| `tsec-bench-run` | 跑分主流程 + `DOCTRINE.md` + 5 个 worker prompt + `analyze_events.py` |
| `team` | 多 Agent 作战规则（一箱一队、不跨箱、按 `is_completed`/`flag_total` 收 flag） |
| `portfolio` | 多题时间分配（广度指纹、backlog、15 分钟停滞、第二 flag 窗口） |
| `tsec-bench` | 靶场平台 平台契约（6 个工具、3 容器、clock/auto-close） |
| `ctf-kb` | 本地知识库检索器（`scripts/kb.py`），索引的是 `ctf/` |
| `prompt-injection-guard` | 不可信内容的扫描与处置流程 |
| `ctfy-engagement-pwn` | CTFY/VPN 题打法 + `tools/{ch,socks,dnsq}.py` |

## 移植/压缩（13）

`awd` `crypto` `engagement` `forensics` `pwn` `pwn-browser` `pwn-heap` `pwn-kernel`
`pwn-misc` `pwn-vm` `pwn-windows` `rev` `web`

这些是为比赛镜像写的**单页调度卡**：保留 ctf-skills 的分类与参考文件名，去掉了长正文，
并把镜像内路径（`/opt/tools`、`/opt/cases`）标注为「镜像内，本地用 checksec/ctf-kb 代替」。
上游：`ljagiello/ctf-skills`（MIT）。

## 导入（12 + 参考文件）

`ctf/` 是 `ljagiello/ctf-skills` 的 2026-11 快照（MIT，见 `ctf/LICENSE`），做了三类改动：
frontmatter 适配 pi、每个 SKILL.md 顶部加「先看索引 → grep 定位 → 只读命中段」的约定、
脚本路径改为相对 `solve-challenge/`。上游更新时先 diff 再合并。

## 子代理与 prompt（pi 上游）

`agents/{planner,reviewer,scout,worker}.md`、`prompts/*.md`、`extensions/subagent/`
来自 pi 1.5.2 的示例/发行包（MIT，Mario Zechner），按本工作流做过修改。
`agents/{recon,exploit,verifier}.md` 是自研。

## 本仓库原创（非第三方）

`tsec-bench-run`、`tsec-bench`、`team`、`portfolio`、`ctf-kb`、`toolkit`、
`prompt-injection-guard`、`ctfy-engagement-pwn`、`competition-platform-ops` 由本仓库作者撰写，随本仓库 MIT 发布。

## 蒸馏导入（4，2026-11-22）

| skill | 来源 | 说明 |
|---|---|---|
| `ctf-state-search` | CyberStrikeAI (Apache-2.0) `skills/pentest-*` + ARTEX planner 语义 | 状态空间搜索 / 能力原语拼链 / 事实台账 / 验证铁律；时间盒已对齐 AGENTS.md 的 15 分钟纪律 |
| `vuln-hunt-code` | ARTEX `skills/codeaudit` + DeepAudit 三闸门/防幻觉/Fuzz Harness | 白盒审计管线（输入清单→前向追踪→三闸门→harness 验证→sweep 核对） |
| `patch-diff-nday` | ARTEX `skills/patch-diff`（源自 zhaoxuya520/reverse-skill, MIT） | 补丁差分恢复 N-day + CTF 捷径 |
| `agent-self-improve` | ARTEX `wrapup.go`/`rsi-lessons.md`/`coverage_ledger.go`/`toolrag.go` + CyberStrikeAI `agentfinalizer`/`skill_stats` | 收尾闸门 / 三段式 wrapup / 可撤回经验库 / 覆盖台账 / 工具投影 |

上游均为方法论重写（未复制源码）；ARTEX 为 AGPL-3.0，再分发注意。
原始取材在 `E:/pi/蒸馏/repos_local/`，报告在 `E:/pi/蒸馏/DISTILL.md`。
