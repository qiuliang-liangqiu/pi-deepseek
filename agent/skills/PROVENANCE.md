# Skill provenance

本目录有 20 个 skill 目录。来源分三类，**升级或再分发时请保持这份对应关系**。

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
