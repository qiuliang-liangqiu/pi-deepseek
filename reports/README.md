# reports

**这里只放复盘的"怎么写"，不放任何一场具体比赛的战绩。**
真实复盘留在本地（`~/.pi/agent/teams/`、私有笔记），因为里面必然带靶场地址、题目编号和分数曲线。

| 文件 | 用途 |
|---|---|
| `README.md` | 本文件：结构约定 + 脱敏规则 |
| `TEMPLATE-round.md` | 一轮跑分/一场比赛结束后的复盘模板 |
| `TEMPLATE-challenge.md` | 单题复盘（进案例库前）模板 |

## 一轮复盘该写什么

按"下次会改变什么行为"排序，不按时间顺序。四块：

1. **数字**：解出题数 / flag 数 / 总分 / 用时；triage 覆盖时长；开箱次数与重开率；
   失败 stint 中位时长、尾声 30 分钟开箱数（`toolbox` 的 `analyze_events.py` 能直接从
   平台导出的 events.csv 算出来）。
2. **失分点**：每一处"白烧了 N 分钟"，写成「现象 → 根因 → 写死的规则」三行。
3. **可复用链**：这轮跑通的漏洞链/打法，抽象成"指纹 → 动作"，进 `toolbox/docs/PLAYBOOK.md`
   或 `CASELIB`（本地）。
4. **违规清单**：对照 `agent/AGENTS.md` 第 1–7 条，逐条写"违反了/没违反"。
   把违规写成"题目缺陷"等于自我美化。

## 脱敏规则（发出去之前逐条过）

- `flag{...}` → `flag{REDACTED}`；示例里只留 `flag{...}` 这种形态。
- 平台域名、run id、容器 IP/端口 → `<your-platform-base-url>`、`run-events.csv`、`<host:port>`。
- 题目编号（形如「分组-序号」，如 `xxx3-05`）→ `题A`、`demo-01`。
- 个人/团队信息、账号、机器名、绝对路径 → 删除。
- token / Authorization 头 / cookie → 删除（不要"只留前 4 位"）。
- 做完跑一遍：`python scripts/check_no_secrets.py`（exit 0 才算干净）。

## 未包含

- `sessions/`、`teams/`、比赛原始附件、平台导出的事件表。
- docx/pdf 原件（体积）；要就本地用 `build_*.py` 生成。
