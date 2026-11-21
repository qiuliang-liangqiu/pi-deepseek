# read-guard

把“看文本文件用 `read`，不要用 `cat`”从提示词变成即时反馈，但不误伤管道/拼接/二进制。

## 模式

| `READ_GUARD` | 行为 |
|---|---|
| `warn` | 不改命令；在 bash 结果尾部追加一行 `[read-guard] ...`，提示改用 `read` |
| `block`（默认） | 纯 `cat <单个文本文件>` 直接拦截，reason 给出可复制的 `read(path=..., offset=1, limit=2000)` |
| `off` | 关闭 |

可选：`READ_GUARD_MIN_BYTES`（默认 0）只对大于该字节数的文件提示/拦截。

## 只处理“无歧义的纯 cat”

会处理：

```bash
cat package.json
cat ./src/app.ts
cat "file with space.txt"
```

不会处理（故意放行）：

```bash
cat f | grep x        # 管道
cat a b > c           # 拼接/重定向
cat f >> log
cat <<EOF ... EOF     # heredoc
cat img.png           # 二进制
cat $FILE / cat *.log # 变量/通配符
head/tail/sed ...     # 抽样/脚本场景，误伤成本高
```

## 启用

扩展放在 `~/.pi/agent/extensions/read-guard/`，pi 启动时自动发现；改完代码后 `/reload` 或开新会话。

```bash
# 默认 block
pi

# 需要观察一段时间时切 warn
READ_GUARD=warn pi
```

## 测试

```bash
cd ~/.pi/agent/extensions/read-guard
node test-policy.ts
```

覆盖：纯 cat、带空格路径、管道、多文件、`-n`、二进制、重定向、`&&`、变量、`/dev/null`、目录、阈值。

## 设计边界

这是**行为 nudge**，不是安全沙箱：

- 模型仍可能用 `grep/sed/head` 绕过；正常提示下它会按 `AGENTS.md` 的决策表走 `read`。
- 只拦 bash；PowerShell 工具未覆盖。
- 不做硬性全局禁 cat——`cat` 在管道/拼接/二进制场景是正确工具。
