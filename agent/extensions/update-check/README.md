# update-check

自动检测 [pi-agent workbench](https://github.com/qiuliang-liangqiu/pi-deepseek-v1) 的新版本。

- **版本源**：仓库里的 `agent/VERSION`；安装/更新后同一个文件在 `~/.pi/agent/VERSION`。
- **检测**：`session_start` 时异步跑 `bin/workbench_update.py check --json`，
  依次尝试 `raw.githubusercontent.com` → GitHub API（`contents`，raw accept）→ jsDelivr，
  结果缓存 6 小时（`PI_UPDATE_TTL`），断网时降级、不阻塞会话。
- **提示**：有新版本时 footer 显示 `wb ↑ 1.0.1`，并提示 `/wb-update`；同一版本 24 小时内只提示一次。
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
| `PI_UPDATE_TTL` | `21600` | 检查缓存秒数 |
| `PI_WORKBENCH_REPO` | — | 本地 checkout 路径，`apply` 优先用它 |

安全：`check` 只读一个版本字符串（`^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$`），
不会执行远端内容；`apply` 运行的 `scripts/update.py` 来自本仓库，且必须经过确认框。
`~/.pi/agent/.update-hold` 存在时 `apply` 会拒绝（配置源头机器防覆盖）。
