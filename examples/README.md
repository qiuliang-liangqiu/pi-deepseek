# examples

这些文件是**模板**，不要直接把真实 token 提交到仓库。

## `settings.example.json` → `~/.pi/agent/settings.json`

选择默认 provider / model / thinking 等级。按自己用的模型改。

## `auth.json` → `~/.pi/agent/auth.json`

pi 的 key 文件是**按 provider 分组**的，格式：

```json
{
  "deepseek": { "type": "api_key", "key": "sk-..." }
}
```

`deepseek` 换成你实际用的 provider（`openai`、`anthropic`、`openrouter` 等），字段名保持一致即可。
这个文件**不要**提交（`.gitignore` 已忽略）。

## `mcp.json.example` → `~/.pi/agent/mcp.json`

两个远程 MCP 的例子：

- `ctf2`：HTTP MCP，`Authorization: Bearer <token>`。
- `ctfy`：`uvx ctfy mcp`，token 走环境变量 `CTFY_TOKEN`。

换成自己的地址/token；也可以删掉不用的条目。MCP 工具会在新会话或 `/reload` 后出现。

## `models-store.example.json`

自定义 provider/model 的注册表（价格、上下文窗口、thinking 映射）。只在需要覆盖内置模型时使用，
放到 `~/.pi/agent/models-store.json`；不需要就忽略。
