# TSec 跑分工作台

把一次完整跑分（104 题）的经验固化成**可复用流程 + 工具**。
这里不保存任何题目明细或 flag，只保留"怎么打"。

## 目录

```
framework/          可直接 import 的工具箱
  api.py            平台 API 客户端（start/close/submit/hint，409 与 duplicate 归一处理）
  net.py            HTTP 原语（会话/cookie/原始报文/并发 map）
  recon.py          指纹识别 -> 打法族
  payloads.py       载荷库（按注入上下文分组）
  playbooks.py      指纹 -> 自动化尝试链
  race.py           竞态编排器（多路错峰，实战验证有效）
  governance.py     可执行纪律：SKIP / 软时间盒看门狗 / 双地址探活 / VPN 预检
  cli.py            命令行入口

docs/
  SOP.md            跑分标准作业流程（含时间盒、并行策略、放弃判据）
  PLAYBOOK.md       指纹 -> 打法对照表（含各过滤场景的 payload）
  PITFALLS.md       41 条踩坑复盘 + 待改进项

tsec.py             旧版单文件 CLI（保留兼容）
```

## 快速开始

```bash
export TSEC_TOKEN=<your-token>          # 必需
export TSEC_BASE_URL=https://...        # 可选
export TSEC_MAX_ACTIVE=3                # 可选：覆盖平台上限；默认 3，409 自动学习

# 0) 前置：VPN 健康检查必须过
python -m framework.cli preflight       # 必须 status=ok 且 TSEC_TOKEN 已设置；退出码 1 = 不通过

# 1) 看进度 / 下一题（自动跳过 SKIP）
python -m framework.cli status
python -m framework.cli next

# 1b) 坏题登记 / 查 SKIP
python -m framework.cli check-skip a-03
python -m framework.cli skip a-03 "/login GET/POST 恒 500，应用未初始化"
python -m framework.cli skips

# 2) 指纹识别（手工打之前先看一眼）
python -m framework.cli scan 10.0.185.216:80

# 3) 一键跑（自动 playbooks，命中即提交）
python -m framework.cli auto xben-005-24

# 4) 手工模式（start 后自动双地址探活）
python -m framework.cli run xben-010-24
python -m framework.cli run xben-010-24 --watch   # 分层时间盒：easy 5/10、medium 10/20、hard 30/60
python -m framework.cli run xben-010-24 --watch --tier credential  # 缺凭据型：5/5，找不到来源就关
python -m framework.cli extend xben-010-24 10m "new primitive: config read"  # 主控批一次延长
python -m framework.cli touch xben-010-24         # 有新原语：更新心跳
python -m framework.cli hold xben-010-24          # 保题：看门狗绝不关闭
python -m framework.cli submit xben-010-24 'flag{...}'
python -m framework.cli close xben-010-24

# 5) 兜底：关掉自己开过的所有容器
python -m framework.cli panic
```

## 测试

```bash
python -m unittest discover -s framework/tests -v    # 35 例：SKIP/分层看门狗/延长/探活/预检/容量/CLI
```

## 设计原则

1. **状态少、可解释**：每个 play 返回 `flag | None`，失败不抛异常，便于串行轮询。
2. **能力分级**：`playbooks` 只自动化高复用套路；重活（上传、反序列化、竞态）
   走 `docs/PLAYBOOK.md` 的人工流程，避免"自动化一半"带来的误判。
3. **并发是一等公民**：平台限制最大并发容器数（默认 3，409 自动学习；`TSEC_MAX_ACTIVE` 可覆盖），`race.py` 与 subagent 派发都围绕这条约束设计。
4. **失败要可归因**：环境类失败必须能区分 `getaddrinfo failed` / `Connection refused` /
   `403`，避免在不可达的数据上浪费时间。

## 本次跑分结果（作为基准）

- 通关 **103/104**，总分 **28000**
- 唯一未解：应用侧 DNS 解析不到依赖的 MySQL（编排未部署该服务），flag 数据不可达
- 有效提速手段排序：**并行流水线 > 套路回流 > 激进并发竞态 > 更好的 payload**
