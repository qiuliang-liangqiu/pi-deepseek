# update-check

自动检测 [pi-agent workbench](https://github.com/qiuliang-liangqiu/pi-deepseek-v1) 的新版本。

- **版本源**：仓库里的 `agent/VERSION`；安装/更新后同一个文件在 `~/.pi/agent/VERSION`。
- **内容源**：仓库里的 `agent/.workbench.json`（每个受管文件的 sha256，随 release 一起提交）。
- **检测**：`session_start` 时异步跑 `bin/workbench_update.py check --json`，
  结果缓存 6 小时（`PI_UPDATE_TTL`），断网时降级、不阻塞会话。
- **提示**：
  - 远端版本更高：footer `wb ↑ 1.0.2`，提示有新版本；
  - 版本相同但文件内容不同：footer `wb ≠ <n>`，提示“本地 Agent 与 GitHub 内容不同”，`/wb-update` 仍可看计划（会给本地文件留 `.local-<时间戳>`）；
  - 内容一致且版本不旧：`/wb-check` 显示已是最新，不弹。
  同一 key 24 小时内只提示一次。
- **升级**：`/wb-update` 先 `apply --dry-run` 打印文件计划，确认后 `apply --yes`
  （备份到 `~/.pi/agent/backups/`，本地改过的文件保留 `.local-<时间戳>`）。
  升级完执行 `/reload`。

命令：

| 命令 | 作用 |
|---|---|
| `/wb-check` | 忽略缓存，强制联网检查一次 |
| `/wb-update` | 查看计划 + 确认后升级 |

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PI_UPDATE_CHECK` | `on` | `off` 关闭自动检查（命令仍可用） |
| `PI_UPDATE_REPO` | `qiuliang-liangqiu/pi-deepseek-v1` | `owner/name` |
| `PI_UPDATE_BRANCH` | `main` | 分支 |
| `PI_UPDATE_URL` | — | 直接指定 `VERSION` 的 URL（镜像/测试） |
| `PI_UPDATE_MANIFEST_URL` | — | 直接指定 `.workbench.json` 的 URL（本地联调/镜像；优先级最高） |
| `PI_UPDATE_TTL` | `21600` | 检查缓存秒数 |
| `PI_WORKBENCH_REPO` | — | 本地 checkout 路径，`apply` 优先用它 |

安全：`check` 只读一个版本字符串（`^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$`），
不会执行远端内容；`apply` 运行的 `scripts/update.py` 来自本仓库，且必须经过确认框。

本机存在 `.update-hold` 时（配置源头机器）：不提示跟随仓库，`/wb-update` 拒绝覆盖。
