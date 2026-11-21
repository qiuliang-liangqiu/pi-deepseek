# 双工 Prompt 使用说明（ACTION 协议 → 本地真实命令）

白皮书附录 A 的 `ACTION ...` 是**训练期标签语言**，本机没有对应解释器。
下表是逐条落地映射，`prompts/RECON_SUBAGENT.md` 与 `EXPLOIT_SUBAGENT.md` 已按此改写，可直接当 subagent 的 task 文本用。

| 文档里的协议 | 本地真实做法 |
|---|---|
| `ACTION shell <cmd>` | 直接 `bash` 工具（`cd E:/pi/2` 后执行） |
| `ACTION http GET <url>` | `curl -sS -A '<浏览器UA>' <url>`，别用默认 UA（会吃 403 client blocked） |
| `ACTION nuclei <url> -t <tpl>` | **不可用**：本机无 nuclei / 无模板库。改 `python -c "from framework import playbooks; ..."` + `docs/PLAYBOOK.md` 手工 payload |
| `ACTION cve <关键词>` | **不可用**：无 FTS5 CVE 库。改 `grep -n 关键词 E:/pi/2/docs/PLAYBOOK.md` + 读 `framework/payloads.py` |
| `ACTION intel <摘要>` | 追加写黑板文件 `E:/pi/2/work/bb_<code>.md` 的 `## INTEL` 段 |
| `ACTION need_recon <需求>` | 追加写黑板 `## NEED_RECON` 段，然后**退出**，由主控补派侦察 |
| `ACTION submit <flag>` | `python -m framework.cli submit <code> <flag>` |
| （文档没写）拿地址 | `python -m framework.cli run <code>` |
| （文档没写）释放 | `python -m framework.cli close <code>` |
| （文档没写）指纹 | `python -m framework.cli scan <host:port>` |

## 环境事实（改 prompt 前先确认，别假设）

- 有：`python 3.14` + `requests 2.34`、`curl 8.21`、`nmap`
- 没有：`nuclei`、`ffuf`、`gobuster`、`docker`、`/opt/wordlists`、`/root/nuclei-templates`、`/app/cve.db`、`security_wiki`
- 所以“14.6w PoC / 18.9w CVE / 176MB 字典”这套在**这台机器上不存在**，prompt 里必须写成“用 playbooks + PLAYBOOK.md”，否则 agent 会去调不存在的命令然后空转。

## 为什么不用 `python -m framework.cli auto`

`cmd_auto` 逻辑是 `start → playbooks → submit → close`，**无论成功失败都 close**。
双工模式下 Recon 跑完就 close 会把容器从 Exploit 手里收走。所以 Recon 只用**不带 close 的等价调用**：

```bash
cd E:/pi/2 && python -c "
from framework import playbooks, recon
t = playbooks.Target('HOST', PORT)
print('fp:', recon.fingerprint('HOST', PORT).get('families'))
print('flag:', playbooks.auto(t, log=lambda *a: None))
"
```

## 黑板的必要性

两个 subagent context 隔离，直接传话只能靠主控转述，成本高且会失真。
黑板是**唯一共享状态**：Recon 只写 `INTEL`，Exploit 只写 `TRIED`/`RESULT`，追加不改历史（避免互相覆盖）。
模板见 `BLACKBOARD.template.md`，主控在 `run` 之后立即创建。

## 黑板的归宿：自动进制库

`cli close` 会把 `work/bb_<code>.md` 沉淀为 `docs/CASELIB/<family>/<code>.md` 并删掉黑板：

| 输入 | 去向 |
|---|---|
| 解出（`RESULT.status: solved`） | `CASELIB/<family>/` |
| 未解（`timeout` / `env_broken`） | `CASELIB/unsolved/` 或同名目录（按 status 分桶） |
| 无记录 | 静默跳过，不阻断 close |

笔记里的 **flag 已脱敏**（原值只在 `_raw/`），因为库是要被反复查阅、甚至分享的。
**别再手写第二份复盘**：黑板写全 → close 自动成型；两份记录必然发散。
