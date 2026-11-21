# 派发决策表（单人 vs 双工）

> 主控（main agent）用这张表决定每题投几个执行体。核心变量不是"题目难不难"，是**分数 / 执行体槽位 / 分钟**。

---

## 1. 两个独立的稀缺资源

| 资源 | 上限 | 谁在用 | 抢爆的症状 |
|---|---|---|---|
| **容器槽位** | 3（平台硬限） | 每个 `start` 出去的题占 1 个 | 409 `max active` |
| **执行体槽位** | 1 主控 + 2 subagent（实际能并行的注意力） | 每个 subagent 占 1 个 | 主控变调度台，没人干活 |

结论：**双工 = 一题吃掉 2 个执行体槽位**，等于把并行度从 3 题降到 1.5 题。
所以双工不是"更难就上"，是"这题的 **单位槽位收益** 是否比拆成两题更高"。

## 2. 基准费率（按题面分值 / 保守耗时）

| 难度 | 题面分 | 时间盒 | 单人速率 | 说明 |
|---|---|---|---|---|
| easy | 200 | 5 min | **~40 分/分** | 1-3 分钟能出，最稳 |
| medium | 300 | 10 min | **~30 分/分** | 3-8 分钟内出，套路熟就快 |
| hard（有源码） | 500 | 20 min | **~50-125 分/分** | 实测 11 道成功的 hard 里 7 道靠源码 4-10 分钟解决 |
| hard（黑盒） | 500 | 20 min | **≈0 分/分** | 黑盒摸索型 hard 基本全部超时 |

⚠️ 两个必须知道的口径问题（会算错账）：
- `total_score` 是**题面满分**，实际到手看 `submit` 返回的 `awarded`；`cli status` 的"总分"是 `sum(total_score)`，**不是真实得分**。
- 时间盒是**上限**不是**预期**。用实测中位数算，别用上限。

## 3. 决策表

| 情境 | 判据 | 派发 | 理由 |
|---|---|---|---|
| easy，任何情况 | `difficulty == easy` | **单人**（Recon 兼 Exploit） | 双工开销 > 题目本身工作量，还会吃掉一个槽位 |
| medium，指纹命中现成 play | `scan` 出 families 且 `PLAYBOOK.md` 有对应族 | **单人** | 一条 `playbooks.auto` 就出，不需要第二个人 |
| medium，跑完 playbooks 未命中 | `auto()` 返回 `(None, None)` | **单人先扛 3 分钟**，仍无进展→**升级双工** | 常见于需要"边扫边打"的题 |
| hard，`S2` 源码探测有 200 | `/src.zip`、`/openapi.json`、`/assets/app.js`、Vite `/src/**` 任一可读 | **单人**，且**优先做**（性价比最高） | 有源码 = 可静态推理，一个人读代码比两个人抢容器快 |
| hard，无源码但攻击面大 | 多端点 + 需同时爆破/构造 | **双工** | 侦察持续采样、利用并行构造，这是双工唯一真正占优的场景 |
| hard，无源码且单点交互 | 只有一个输入框 / 一个上传点 | **单人**，20 分钟到点放弃 | 双工没有并行空间，只会多一份上下文开销 |
| 同一题已被双工占用 | 黑板已存在 `META.owner` | **不再派第三个人** | 3 人同题 = 全员互相 409 |

## 4. 升级 / 降级触发（写死在 prompt 里）

**升级到双工**（主控判断，2 选 1 即可）：
- 单人已耗 `时间盒 40%` 且黑板无 `INTEL` 增量
- 单人连续 2 次同类失败，且失败原因**指向"信息不足"**而不是"payload 不对"
  （典型：不知道真实上下文根、不知道有没有 `/actuator`、不知道版本 → 需要侦察）
- 需要"长枚举 + 短利用"重叠执行（枚举 3 分钟，利用 30 秒）

**降级回单人**（立刻合并，别硬撑）：
- 侦察 2 分钟内 `RECON_DONE status=nothing` → 双工没有输入，等于空转
- 利用侧收到 `INTEL` 后 1 分钟内没有产生 `TRIED` 行 → 它没在干活
- 剩余时间 < 3 分钟 → 直接判死 `close`，不要为了"再看看"占槽位

## 5. 主控 runbook（复制即用）

```bash
# 0) 前置：VPN + token
curl -s http://<vpn-health-host> | head -c 200        # 必须 status:"ok"
cd <workbench>/2 && python -m framework.cli status

# 0.5) 先查库：同族战例比从零摸索快
#      read docs/CASELIB/INDEX.md  → 找到同 family 的笔记直接照搬打法链

# 1) 排产：easy 全拿 → medium → hard（pending() 已按此排序）
python -m framework.cli next

# 2) start + 建黑板（一次做完，别拖）
python -m framework.cli run <code>              # 打印 addr + fingerprint
cp /c/Users/30367/.pi/agent/skills/tsec-bench-run/prompts/BLACKBOARD.template.md \
   work/bb_<code>.md                            # 填 META 段

# 3) 按决策表派活；4) 收到 RESULT 立刻 close，滚动补位
python -m framework.cli close <code>            # ← 自动归档进 docs/CASELIB，黑板同时删掉

# 5) 收尾
python -m framework.cli panic                   # 关掉所有 owned 并逐个归档
```

派活时 prompt 的替换清单（漏一个 subagent 就会乱跑）：

| 占位符 | 填什么 |
|---|---|
| `{CODE}` | `unique_code` |
| `{HOST}` / `{PORT}` | `run` 打印的地址拆开 |
| `{DEADLINE}` | 当前时间 + 时间盒（`date +%H:%M`） |
| `{BB}` | `<workbench>/2/work/bb_<code>.md` |
| `{UA}` | 固定浏览器 UA（见 prompts/README.md） |

## 6. 每轮结束的沉淀（自动，不用管）

`close` / `panic` 已内置归档：`work/bb_<code>.md` → `docs/CASELIB/<family>/<code>.md`，
原始证据 → `_raw/`，索引 → `INDEX.md`，黑板文件自动删除。
所以**不要**另建 `work/bb_done/`、不要手工整理笔记 —— 只维护黑板，其余交给 close。
手动补归档：`python -m framework.cli archive <code> [--family xxx]`（不带参数=重建索引）。

## 7. 本表不能替你做的判断

- **easy 必须真的走完**：历史上跳过 easy 直接切 hard，事后看四个 medium 全在 55-67% 成功率区间。
- **hard 只挑能拿到源码的**：黑盒 hard 是纯沉没成本，`S2` 探测 90 秒无 200 就弃。
- **409 `max active` 不会误伤别人，但会误伤自己**：`api.start` 撞到 409 时，会把**本进程 `owned` 里的全部题目一次性 close**，然后重试一次；`owned` 为空才直接抛错。
  所以两条规矩：① 一个进程里**只驱动一个 worker**（subagent 天然独立进程，不会互相踩）；
  ② **主控自己不要 start 题目再交给别人打** —— 主控一次 409 会把手上所有题全关掉。主控只跑 `status/next/panic`，`start` 交给干活的人。
