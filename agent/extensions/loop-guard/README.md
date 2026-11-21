# loop-guard

把「工具调用防沉迷」从提示词纪律变成即时反馈：同一条 bash 命令被反复执行时，在结果里插一句干预。

来源：腾讯云智能渗透黑客松冠军复盘（AI 小分队）——「LLM 会沉迷于重复调用同一个工具（尤其 curl）来低效测试」，
以及「连续多轮无新信息 = 该换方向」的止损纪律。

## 开关

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `LOOP_GUARD` | `block` | `warn` / `block` / `off` |
| `LOOP_GUARD_WINDOW` | `12` | 滑动窗口：最近多少次 bash 调用 |
| `LOOP_GUARD_WARN` | `3` | 窗口内出现几次开始提醒 |
| `LOOP_GUARD_BLOCK` | `5` | `block` 模式下几次直接拦（须 > WARN） |

```bash
LOOP_GUARD=block LOOP_GUARD_BLOCK=4 pi     # 对跑分这种长会话可以更硬
LOOP_GUARD=off pi                          # 调试/压测时关掉
```

## 行为

- 只统计 **bash**；key = 去首尾空白 + 压缩连续空白后的命令，所以 `curl  -s x` 与 `curl -s x` 算同一条。
- `warn`：第 N 次（默认 3）起在 tool_result 尾部追加 `[loop-guard] ...`，提醒写结论/换动作/hold。
- `block`：第 M 次（默认 5）起直接 `{block: true}`，reason 里给出替代动作。
- 窗口滑动：执行不同命令会稀释计数，正常的「跑一次再改一次」不会误报。
- 自带 `echo loop-guard-probe` 前缀的探测命令不计数（方便自测）。

## 自测

```bash
cd "$TEMP"
printf '连续执行 4 次完全相同的 bash 命令 `echo loop-probe`，每次单独调用。' \
  | pi -p --mode json --no-session | grep -c loop-guard   # 期望 >= 2
LOOP_GUARD=block LOOP_GUARD_BLOCK=3 LOOP_GUARD_WARN=2 \
  bash -c 'printf "无限重复 echo block-me 共 5 次" | pi -p --mode json --no-session' | grep -c loop-guard
```

## 注意

- 有意的轮询/复现（等容器起来、等 DNS 生效）也会被提醒；提示文本里已说明「如果是重试同一假设…」，不会强制打断。
- 不替代 `touch`/`hold`/`watch` 的决策，只负责在「同一条命令空转」时把成本摆到眼前。
