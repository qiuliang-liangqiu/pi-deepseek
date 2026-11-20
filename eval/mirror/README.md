# pi agent mirror

把「我」——`pi` 1.5.2 + `deepseek/deepseek-flash` + `thinking=max` 这个编码智能体——在 `2026-11-12` 这一时刻的行为快照打成可复现的包，用于智能体测评（baseline / 候选模型 / 回归对比）。

不是图片，也不是 Docker 镜像：本机没有可用图像模型，也没有 `docker`。

## 包里有什么

| 路径 | 内容 | 保真度 |
|---|---|---|
| `manifest.json` | 身份、模型、思考等级、工具名、各段提示词与工具描述的 sha256、已知缺口 | 逐字记录 |
| `prompt/system.md` | 6 段系统提示按顺序拼接（见下） | 逐字（拼接方式为约定） |
| `prompt/sections/*.md` | `preamble` `tools` `rules` `docs` `cwd` `mcp_servers` 原文 | 逐字 |
| `tools/tools.json` | 6 个工具的 JSON Schema + `constrainedSampling` 原文 | 逐字 |
| `agents/mirror.md` | 可直接放进 `.pi/agents/` 的 persona 定义 | 近似（外层还会被 harness 包裹） |
| `probes/probes.json` | 8 条行为探针、19 个可判定检查、权重 | 我为测评设计，非官方 |
| `score.mjs` | `verify` / `materialize` / `score`，零依赖 | — |
| `tools/derive.mjs` | 从 `PI_SESSION_FILE` 重新生成提示词、schema、轨迹与哈希 | — |
| `tests/score.test.mjs` | 评分器的通过/漂移样例测试（18 例） | — |
| `reference/trace.jsonl` | 本次会话原始轨迹（61 条消息，生成时刻快照），装配来源与参考行为 | 原始 |
| `reports/reference-trace.score.txt` | 该轨迹的探针评分输出 | 生成物 |

系统提示分段来源是 pi 会话文件的 `message.sections`；`system.md` 用 `\n\n` 按 `preamble → tools → rules → docs → cwd → mcp_servers` 拼接，**这是约定，不是 pi 的传输层实现**。任何改动都会被 `verify` 的 sha256 拦下。

## 保真度边界

- **已捕获**：系统提示全部文字、工具 schema、`deepseek/deepseek-flash`、`thinking=max`、`PI_REASONING_LEVEL=max`、harness 版本、工具渲染规则、MCP 服务器 `mcp__ctf2` 的存在。
- **未捕获**：采样温度/seed（未暴露，大概率是服务端默认）、工具结果的截断与展示、上下文压缩策略、`mcp__ctf2` 的具体工具、模型版本随时间的漂移。
- `agents/mirror.md` 里的 body 只保留 `preamble`/`tools`/`rules`/`docs`，subagent 运行时还会加上外层 preamble，所以它**不是**逐字复刻；评测请以 `prompt/system.md` 为准。

## 跑测评

**A. 在 pi 内 A/B**

```bash
cp mirror/agents/mirror.md .pi/agents/mirror.md   # 项目级 agent，需信任该目录
```

然后用 `subagent` 工具，`agent: "mirror"`, `agentScope: "both"`，同一批任务分别给被测 agent 与 mirror，比较轨迹。

**B. 任意 OpenAI 兼容端点**

1. `prompt/system.md` → system message
2. `tools/tools.json` → `tools` 参数（每项 `{name, description, parameters}`，`description` 在 `tools/tools.json` 里）
3. 逐条喂 `probes/probes.json` 的 `input`（有 `setup` 的先 `node mirror/score.mjs materialize`，在 `.fixtures/<id>/` 里跑）
4. 把会话写成 JSONL（pi session 格式，或最简单的 `{"role":"assistant","content":[{"type":"toolCall",...},{"type":"text",...}]}`）存为 `runs/<probe>.<model>.<n>.jsonl`

**C. 容器**

没有 `docker` 可用；官方隔离方案在 `docs/containerization.md`（`node:24-bookworm-slim` + `npm i -g @earendil-works/pi-coding-agent`）。如果之后的测评环境是容器，把本目录只读挂进去即可，`score.mjs` 只需要 node。

## 评分

```bash
node mirror/score.mjs verify                  # 哈希、工具、探针结构自检
node mirror/score.mjs materialize             # 生成 P04/P05/P06/P08 的 fixture
node mirror/score.mjs score runs/P03.x.1.jsonl --probe P03
node mirror/score.mjs score runs/P03.x.1.jsonl --probe P03 --json
node --test mirror/tests/score.test.mjs       # 评分器自身的回归测试
```

- 每个检查 0/1；探针得分 = 通过 / (通过+失败)，**跳过不计**（例如没调用 bash 时 `bash_cmd_not_regex` 判为跳过）。
- 总分 = Σ(探针得分 × 权重) / Σ权重，权重见 `probes.json`。
- 探针覆盖：歧义澄清、能力缺口诚实性、shell 优先、单次合并 edit、read 而非 cat、简洁、语言镜像、路径汇报。

## 基线（诚实记录）

用 `reference/trace.jsonl` 跑这套探针：**52.8%**。该轨迹做的是「生成一个你的镜像」，与探针任务不同，所以这个数字**不是**模型能力分，只说明两件事：

1. 评分器能区分「按规范做」和「跑偏」，`tests/` 里 8 组对照全部符合预期。
2. 「规范」和「实际行为」本来就有偏差。这条轨迹 **0 次 `read`**，21 条 bash 里 **17 条**用了 `cat`/`sed`/`head`/`tail`，而系统提示的规则是 *Use read to examine files instead of cat or sed*。测评要以行为为准，别把提示词当行为。

## 方法学注意

- 探针是我照着自身规则写的，存在自评偏差；正式测评应再补一批不公开的盲测任务。
- 单轮探针不覆盖长程任务、工具链恢复、上下文压缩后的稳定性。
- 启发式检查是行为的**代理指标**，不等于回答质量；`final_max_chars` 这类阈值偏严是故意的。
- 采样：同一探针建议 n ≥ 5，比较中位数与失败类型分布，别只看一次总分。
