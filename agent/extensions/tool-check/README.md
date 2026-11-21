# tool-check

会话启动时检查本机缺哪些 CTF / 跑分工具，**只提示，不安装**。

## 它做什么

1. `session_start` 时异步跑 `bin/tools_check.py check --json`（纯本地、无网络、stdlib）。
2. 有缺失 → footer 显示 `tools !N`，并提示前几个缺失项 + 安装命令；
   全都在 → 清掉 footer 标记。
3. 结果缓存 24 小时（`PI_TOOL_CHECK_TTL`），同一份缺失清单 24 小时只提示一次。

## 命令

```
/tools-check     立刻重查，缺失项最多列 12 个
```

## 开关

| 变量 | 作用 |
|---|---|
| `PI_TOOL_CHECK=off` | 关掉自动检查（`/tools-check` 仍可用） |
| `PI_TOOL_CHECK=debug` | 脚本缺失 / python 报错时打印原因 |
| `PI_TOOL_CHECK_TTL=3600000` | 缓存时长（毫秒），默认 24h |

## 缺失清单与安装

清单在 `bin/tools_check.py` 的 `CATALOG`，`id` 与 `scripts/setup-tools.py` 保持一致，
所以提示里给的 `python <repo>/scripts/setup-tools.py` 可以直接装（它会先打印计划再问）。
`gdb` / `patchelf` 这类 Windows 没有原生包的，算 `needs_alt`（提示用 WSL / VM / Docker），
不当成"缺失"反复提醒。

想换成自己的清单：`python bin/tools_check.py check --catalog my.json`，
JSON 是 `[{"id": "...", "probe": "可执行文件名", "group": "web", "platforms": ["windows"]}]`。

## 不做的事

- 不安装、不下载任何东西（装软件必须用户同意）。
- 不联网（版本检查是 `update-check` 的职责）。
- 不改配置，只写一个状态文件 `~/.pi/agent/.tools-check.json`。
