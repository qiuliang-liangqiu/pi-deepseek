# 运行时配置（agent/）

把本目录整体放进 `~/.pi/agent/` 即可。安装脚本会自动做备份：

```bash
bash scripts/install.sh
```

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

## 目录

| 路径 | 作用 |
|---|---|
| `AGENTS.md` | 用户级总规则（止损闸门、速率、triage、团队、注入防护） |
| `agents/*.md` | 子代理定义；`recon/exploit/verifier` 自研，`planner/reviewer/scout/worker` 来自 pi 示例 |
| `bin/*.py` `bin/triage.sh` | 团队调度、止损台账、triage 闸门、注入扫描、浅指纹 |
| `extensions/*` | pi 自动加载（`~/.pi/agent/extensions/*/index.ts`） |
| `prompts/*.md` | 斜杠模板（pi 示例） |
| `knowledge/*.md` | 工作台说明 + 同步记录 |
| `skills/*` | 技能包；来源见 `skills/PROVENANCE.md` |
| `extensions/parked-ctfy/` | 未启用的 CTFY 工具草稿（不会被自动加载，因为不在 `index.ts` 路径） |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `PI_WORKBENCH` | — | 指向仓库的 `toolbox/`；`tsec-bench-run` 的文档与命令用它 |
| `PI_AGENT_DIR` | `~/.pi/agent` | 安装脚本的目标目录 |
| `TSEC_TOKEN` | — | `framework` 的平台 token（本地跑分） |
| `TSEC_BASE_URL` | 靶场平台 | 平台地址 |
| `TSEC_WORKDIR` | `./work` | framework 的状态目录 |
| `LOOP_GUARD` | `warn` | `warn` / `block` / `off` |
| `READ_GUARD` | `warn` | `warn` / `block` / `off` |
| `TEAM_JOB_TIMEOUT` | `1800` | `team_pool` 单 job 超时（秒） |
| `TBENCH_TRIAGE` | `1` | `team_pool adopt` 时是否自动进入 triage |
| `TBENCH_TRIAGE_PROBE_S` | `120` | triage 单箱探针秒数 |
| `TBENCH_STALL_CLOSE_S` | `900` | 无新原语自动关箱阈值 |

## 注意

- `bin/team_pool.py` 的本地状态在 `~/.pi/agent/teams/`（jobs.json、notes、candidate），不要提交。
- `extensions/subagent` 是 pi 上游示例的改造版；升级 pi 后如果接口变化，以 pi 的 `examples/extensions/subagent` 为准对照。
- `triage-guard` 读取 `~/.pi/agent/triage_state.json`，由 `triage_gate.py` 维护。
- 扩展与脚本都按 Windows + UTF-8 调试；Linux 下需要 `chmod +x bin/*.sh bin/*.py`（安装脚本会做）。
