# 为什么这套 Agent 用 DeepSeek 更合适

> 先说清楚：这不是跑分榜单，也没有"某模型强 30%"这种数字。下面是**这套配置的结构性需求**
> 与 DeepSeek 的匹配点，以及你怎么自己量。凡是没量过的，文中都标成"假设"。

## 1. 这套 Agent 的 token 结构和聊天机器人不一样

跑分模式默认 **3 个 worker / 箱、3 箱并行 = 9 个子代理**，每个都是独立的 pi 进程，
每个都在读源码、跑命令、写笔记。一小时的墙钟时间，token 消耗是单次对话的几十倍。

| 模型 | 输入 $/M | 输出 $/M | 缓存读 $/M | 上下文 | 最大输出 |
|---|---|---|---|---|---|
| `deepseek-flash` | 0.3 | 1.2 | 0.006 | 1M | 384K |
| `deepseek-v4-pro` | 1.32 | 3.96 | 0.044 | 1M | 384K |

（数字来自本仓库 `examples/models-store.example.json` 注册的目录，以官方最新价格为准。）

关键不是"谁便宜"，而是 **缓存读比输入便宜 50 倍**：子代理反复读同一份 `AGENTS.md`、
同一批 skill、同一份源码，命中缓存后的边际成本接近 0。这正是"多开几个 worker
去并行试假设"能成立的前提 —— 换成按输入原价计费的组合，9 路并发很快就变成奢侈品。

## 2. 1M 上下文 / 384K 输出，让"不压缩"成为默认策略

这类任务经常要一次吞下：一整个仓库、一份 200KB 的日志、十几轮工具输出。
上下文短就会频繁触发 compaction，而 compaction 恰好会**丢掉"已排除"的结论** ——
那不是省钱，是把之前 20 分钟的判断重新买一遍。

1M 上下文 + 384K 单次输出意味着大多数会话可以整段保留。这也是为什么
`agent/AGENTS.md` 敢写"每箱必留三行笔记"而不是"记得压缩上下文"。

## 3. 显式思考档位，服务的是"纪律"而不是"聪明"

pi 的 DeepSeek 适配不是通用 OpenAI 兼容拼出来的，模型元数据里有专门的字段：

```json
"thinkingLevelMap": { "low": "low", "high": "high", "max": "max" },
"thinkingFormat": "deepseek",
"requiresReasoningContentOnAssistantMessages": true,
"supportsStrictMode": true
```

三个后果：

1. **档位可选**：triage 阶段用 `high`，卡住的 hard 题切 `max`，收尾用 `low` 省钱 —— 同一套配置按阶段调速。
2. **推理内容要保留**：网关/代理如果把 assistant 消息里的 reasoning 字段剥掉，工具调用会变差
   （见 `docs/API-CONFIG.md` 的"常见错误"）。
3. **严格模式可用**：`supportsStrictMode` 让工具的 JSON Schema 真正约束参数，
   少一类"参数名猜错"的失败 —— 这类失败在 `toolbox/docs/PITFALLS.md` 里出现过不止一次。

## 4. 可替换性：OpenAI 兼容 = 不会被一家锁死

`api: "openai-completions"` + `baseUrl` 可覆盖，意味着：

- 自建网关 / 代理 / 中转，只改 `baseUrl`，模型目录和认证方式不变；
- 想 A/B 别的模型，加一个 provider 就能同一套 skill/扩展跑对照；
- 预算紧张时把 hard 题降到 `deepseek-v4-pro`、triage 用 `flash`，这是配置问题不是重写问题。

## 5. 中文语境（**假设，未量化**）

这套 prompt / skill / 复盘文档是中文写的，失败信息、题目描述、平台报错也常是中文。
中文技术语料更密的模型，在"读懂一句含糊的报错"上通常更稳。
**这条我没做过对照实验**，属于设计假设，别当结论引用。

## 什么时候不该用 DeepSeek

- 网络到不了 `api.deepseek.com`，且没有可用网关 —— 先解决连通性，别调模型；
- 需要极强的图片/截图理解：`flash` 支持 image，`pro` 不支持，别想当然；
- 需要模型侧的长时缓存语义（prompt caching 的写入侧优化）—— 按你的账单实测；
- 你的任务 token 量很小（单次问答），那价格差异不构成理由，选顺手的即可。

## 怎么自己验证"更好"

别信这张文档，跑三个实验：

```bash
# 1. 行为快照：改配置前后是否还守纪律
node eval/mirror/score.mjs            # 参考轨迹得分；verify 只查一致性

# 2. 同题 A/B：只改 settings.json 的 defaultModel / defaultThinkingLevel
#    （其余不变），各跑同一批题，记录四个数

# 3. 用日志复盘，别凭感觉
python scripts/ingest_run.py <bundle> --json
python agent/skills/tsec-bench-run/tools/analyze_events.py <events.csv>
```

看这四个指标，而不是总分：

| 指标 | 变好的方向 | 含义 |
|---|---|---|
| 失败 stint 中位 | 下降 | 更早止损，不是更能打 |
| 重开率 | 下降 | 笔记真的在起作用 |
| 未开题数 | 下降 | triage 覆盖到位 |
| 尾声 30 分钟开箱数 | 下降 | 收尾不再摸黑 |

总分会被"运气好碰到高分题"污染，这四个数不会。
