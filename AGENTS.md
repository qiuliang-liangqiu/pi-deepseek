# 仓库说明（给在这个仓库里干活的 coding agent）

这个仓库是 **pi-agent workbench**：一套给 [pi](https://pi.dev) 用的 CTF / 靶场配置与工具。

> 用户级规则（14 分钟停滞闸门、速率上限、triage、prompt-injection 防护等）在
> **`agent/AGENTS.md`**，安装后是 `~/.pi/agent/AGENTS.md`。本文件只讲"在这个仓库里怎么改代码"。

## 目录约定

| 路径 | 装到哪 | 说明 |
|---|---|---|
| `agent/` | `~/.pi/agent/` | 运行时配置：AGENTS.md、agents/、bin/、extensions/、prompts/、skills/ |
| `toolbox/` | 任意位置，用 `PI_WORKBENCH` 指过去 | 跑分 CLI 与文档（SOP/PLAYBOOK/PITFALLS） |
| `reports/` | — | 复盘**怎么写**（模板 + 脱敏规则），不含真实战绩 |
| `eval/` | — | 行为镜像回归 + skill 路由评测 |
| `scripts/` | — | bootstrap / install / doctor / 脱敏检查 |

## 改完必须跑的

```bash
python scripts/check_no_secrets.py            # 不能有真 flag / token / 绝对路径
python scripts/doctor.py --strict             # 环境自检（未安装时应 warn 不 fail）
cd toolbox && python -m unittest discover -s framework/tests -v
node eval/mirror/score.mjs verify             # VERIFY PASS
python eval/route/route_eval.py --dry-run     # 14 条路由用例
bash scripts/bootstrap.sh --dry-run           # 安装脚本不报错
```

改 skill/prompt 时同步更新 `eval/mirror/manifest.json` 的哈希（见 `eval/mirror/README.md`）。

## 硬约束

1. **不提交真实战绩**：具体比赛的分数、题目编号、靶场地址、flag、token 一律不进仓库
   （`flag{REDACTED}` 是唯一允许的占位）。
2. **不复刻平台身份**：不能出现具体平台的域名、比赛名、run id、容器 IP；
   需要时写 `<your-platform-base-url>`、`demo-01` 这类占位。
3. **代码要能离线跑**：`toolbox` 的 `DEFAULT_BASE` 是占位符，必须靠 `TSEC_BASE_URL` 覆盖。
4. **Windows/Linux 都要能用**：路径用 `pathlib`，脚本给 `.sh` + `.ps1` 两份。

## 提交规范

- 一个提交一件事；标题用 `area: what`（`toolbox:`、`agent:`、`docs:`、`eval:`）。
- 提交前跑上面的检查；`git diff` 里出现 `flag{`（非 REDACTED）、`sk-`、`ghp_` 直接回退。
