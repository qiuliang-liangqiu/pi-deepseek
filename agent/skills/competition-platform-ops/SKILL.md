---
name: competition-platform-ops
description: CTFY / CTF2 / DataCon 比赛平台的写操作流程：先确认账号/赛道/实例，再按“服务题按 host:port 绑定、实例/附件题先询问”的规则提交；平台写操作走 platform_do.py + platform_approve.py，audit 对账。Load before any platform submit/start/close action.
---

# Competition platform ops (write actions)

适用：CTFY（ctfy.tsinghua.edu.cn）、CTF2（dasctf）、DataCon 等比赛平台。
目标：**只提交从本队授权实例实际解题取得的 flag；不按题目名、不替别人、不跨赛道。**

## 0. 先回答三个问题（不答完不写）

1. **这是谁的账号/哪个赛道？** 是你自己的队，还是帮别人做？
2. **允许提交吗？** “做题”默认只做本地分析；提交是单独授权。
3. **规则怎么说？** 先读赛事规则手册（如 CCF-TPCTF v2.7）：
   - 智能体自主性：正式任务期间不得人工注入分析/代码/答案；
   - 人机协同成果不得传入智能体赛道；
   - 禁止队外代解、共享账号；
   - 禁止提交验证环境/其他队伍/其他赛道的 flag。

## 1. 服务题：按地址绑定，匹配通过自动交

适用：有 `host:port`（或 TLS SNI）的题目。

```bash
python ~/.pi/agent/bin/platform_do.py submit \
  --target HOST:PORT [--sni SNI] \
  --answer-file flag.txt
```

脚本行为：

- 配置来源：CTFY 读 `~/.config/ctfy/config.toml`（或 `CTFY_PLATFORM_URL`/`CTFY_TOKEN`）；
  CTF2 读 `~/.pi/agent/mcp.json` 的 `mcpServers.ctf2`（URL 推 base，`Authorization: Bearer` 取 token）。

- 查平台 `/instances`，要求**唯一**匹配：
  - `status == ready`；
  - **本队**（必须传 `--team <team_id>` 或设置 `CTFY_TEAM_ID`/`CTF2_TEAM_ID`；
    未配置则视为无法验证，fail closed，不提交）；
  - `services` 里的 `host:port` 完全一致（给 SNI 时 `tls_sni` 也要一致）；
  - `challenge_id` 对得上（可选 `--challenge`）。
- 匹配通过 → 自动 `POST /submissions`，带该 `instance_id`；
- 0 个/多个/非本队/未 ready → **不提交**，退出码 2/3/4，报告原因；
- **永远不按题目名匹配。**

## 2. 只有实例和附件的题：先询问

适用：没有端口，只有实例和附件（如 BurnHeart）。

```bash
# 1) 只拿计划，不提交（退出码 10，status=ask）
python ~/.pi/agent/bin/platform_do.py submit \
  --instance-id <uuid> --answer-file flag.txt

# 2) 用户在自己的终端批准（TTY 必须；agent 不能用管道/here-string 伪造）
python ~/.pi/agent/bin/platform_approve.py \
  --instance-id <uuid> --challenge <challenge-id> \
  [--competition <competition-id>] --answer-file flag.txt
# -> 打印 approval 路径

# 3) 带 approval 提交
python ~/.pi/agent/bin/platform_do.py submit \
  --instance-id <uuid> --answer-file flag.txt \
  --approval ~/.pi/agent/approvals/<nonce>.json
```

没有 approval 时**不要**提交；`platform_do.py` 不会发 POST。

## 3. 禁止项

- 不提交静态测试/占位 flag（如 `TPCTF{TEST_PLATFORM_FLAG}`）；脚本会拒绝 `TEST_PLATFORM/PLACEHOLDER/EXAMPLE`。
- 不提交其他队伍、其他赛道、验证环境的 flag。
- 不用 raw `curl`/`requests`/MCP `submit` 工具/codemode 绕过 `platform-guard`；它只拦 **flag 提交**（`/submissions`、`/qa/submissions`）。
- 上传镜像/出题（`/authored-challenges`、`/authoring/*`）、实例启停、评测（`/instances/*/check`、`/eval/*`）不是 flag 提交，guard 不拦；按平台规则操作。
- 不修改 `extensions/platform-guard`、`bin/platform_do.py`、`platform_approve.py`、`approvals/`。
- 不把 `CTFY_TOKEN`/`BENCHMARK_TOKEN` 打进命令输出或日志。

## 4. 审计与对账

```bash
python ~/.pi/agent/bin/platform_audit.py              # 最近尝试
python ~/.pi/agent/bin/platform_audit.py --instances  # 当前实例与 services
python ~/.pi/agent/bin/platform_audit.py --verify     # 提交记录 vs 当前实例
```

审计文件：`~/.pi/agent/platform-audit.jsonl`（只存 answer hash，不存明文/token）。

## 5. 误提交后的止损

1. 立刻停手，不再调用平台写接口；
2. **不要删/改日志、不要换 token、不要重新提交**；
3. `platform_audit.py` 导出时间线（submission id、instance id、challenge、时间）；
4. 主动联系组委会（如 `thuctf@tsinghua.edu.cn`），说明是 AI 误操作/非本人正常提交，请求撤销；
5. 保留 VPS/本机日志，配合核验。

## 6. 边界

`platform-guard` 是 best-effort 门，不是沙箱；agent 与本机同权限。真正的硬边界是
“用户明确授权 + 不替别人提交 + 先读规则”。代码负责把正常路径收紧、留痕，
不负责替人做规则解释。
