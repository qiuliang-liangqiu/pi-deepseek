# 配置 API：让 pi 跑在 DeepSeek 上

三条路线，从最省事到最可控。任选一条即可，**不要三条混着填**（优先级见文末）。

## 0. 只给一个 key 的时候：用脚本（推荐用这种方式分发给别人）

如果你手上只有一串 key，要把它变成"能跑的 pi"，不要直接把 key 贴在聊天/工单里，走这个：

```bash
bash scripts/set-api.sh                       # 交互：provider + 隐藏输入 key
bash scripts/set-api.sh --key-file ~/k.txt    # 从文件读，key 不进 shell 历史
bash scripts/set-api.sh --provider myproxy --base-url https://gw.example.com/v1 \
     --key-file ~/k.txt --model my-model
bash scripts/set-api.sh --dry-run             # 只打印会写什么
```

它做四件事，**不会**打印 key：

| 动作 | 细节 |
|---|---|
| 备份 | `auth.json` / `settings.json` / `models.json` → `*.bak-<时间戳>` |
| 写 key | 合并进 `auth.json`（其他 provider 保留），文件权限 600 |
| 定默认 | `settings.json` 的 `defaultProvider/defaultModel/defaultThinkingLevel` |
| 可选端点 | 给了 `--base-url` 才写 `models.json`，`api: openai-completions` |

写完自动跑 `pi auth check` 复核（仅当写的是默认目录 `~/.pi/agent`）。
用完记得删 key 文件：`shred -u ~/k.txt`（或 `rm`）。

## A. 用 pi 自己登录（最快）

```bash
pi auth login deepseek --api-key
```

交互式粘贴 key，pi 会写进 `~/.pi/agent/auth.json`。之后：

```bash
pi auth check --provider deepseek --json
# {"status":"ready","provider":"deepseek","authType":"api_key"}
```

> 在 TUI 里也可以直接 `/login` 选 DeepSeek。

## B. 用环境变量（CI / 不想落盘）

```bash
export DEEPSEEK_API_KEY=sk-...
pi --list-models          # 能看到 deepseek 的模型就说明认到了
```

适合容器、临时机器、以及"不想让 key 留在磁盘上"的场景。缺点：每个新 shell 都要设一次。

## C. 手写 auth.json（多 provider / 自己管文件）

`~/.pi/agent/auth.json`：

```json
{
  "deepseek": { "type": "api_key", "key": "sk-..." }
}
```

- 这个文件**绝对不要提交**（本仓库 `.gitignore` 已忽略 `auth.json`）。
- 有多家就并列加 key，例如 `{"deepseek": {...}, "openai": {...}}`。
- 换 key / 掉权限后跑一次 `pi auth check --provider deepseek --json` 确认 `ready`。

## 选默认模型

`~/.pi/agent/settings.json`：

```json
{
  "defaultProvider": "deepseek",
  "defaultModel": "deepseek-flash",
  "defaultThinkingLevel": "max"
}
```

`defaultThinkingLevel` 可选 `off | minimal | low | medium | high | xhigh | max`。
这套工作台默认用 `max`：跑分场景下"想清楚"省下的重试时间，比多花的那点 token 值钱。
想省成本就降一档，行为差异用 `eval/mirror` 量（见 `docs/WHY-DEEPSEEK.md`）。

## 想用自建网关 / 代理 / 别的 OpenAI 兼容端点

pi 1.5.2 读 `~/.pi/agent/models.json`（`providers` 形状）：

```json
{
  "$schema": "https://pi.dev/schemas/models.schema.json",
  "providers": {
    "deepseek": {
      "baseUrl": "https://gateway.example.com/v1",
      "apiKey": "$DEEPSEEK_GATEWAY_KEY",
      "api": "openai-completions"
    }
  }
}
```

- 只写 `baseUrl` / `headers` 时，内置模型目录和认证方式保持不变（推荐这种"只改出口"的写法）。
- `apiKey` 支持 `$NAME` / `${NAME}` 取环境变量、`!command` 取命令输出；`$$` 表示字面 `$`。
- 自己要加模型时才写 `models: [{ "id": "..." }]`，否则容易把内置目录整个覆盖掉。

本仓库的 `examples/models-store.example.json` 是另一条路：它把 DeepSeek 的模型元数据
（1M 上下文、384K 输出、`thinkingLevelMap`、`thinkingFormat: "deepseek"`）注册成
`~/.pi/agent/models-store.json`。**只有需要覆盖内置模型时才用**，正常接官方 API 不需要。

## 验证清单

```bash
pi --list-models                                  # 模型出现在表里
pi auth check --provider deepseek --json          # {"status":"ready"}
pi -p "reply with the single word: pong"          # 真能出 token
```

前两条过了但第三条报错，基本是网络/额度问题，不是配置问题。

## 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `401 Unauthorized` | key 错/过期，或把 `Bearer` 前缀写进了 `key` 字段 | 只放 key 本体 |
| `404 model not found` | `defaultModel` 写了目录里不存在的 id | `pi --list-models` 看真实 id |
| 代理报 `404 /v1` | `baseUrl` 少了或多了 `/v1` | OpenAI 兼容端点一般是 `https://host/v1` |
| `pi --list-models` 里没有 deepseek | 三处都没配好，或 provider 名拼错 | 先跑 `pi auth check --provider deepseek` |
| 能聊天但工具调用乱 | 代理改写了 reasoning 字段 | DeepSeek 需要保留 `reasoning_content`，别让网关剥掉 |

## 凭据优先级（从高到低）

1. 命令行 `--api-key`
2. `~/.pi/agent/auth.json`
3. `models.json` 里的 `apiKey`
4. 环境变量（`DEEPSEEK_API_KEY`）

排查"为什么用的是旧 key"时，按这个顺序往上找。
