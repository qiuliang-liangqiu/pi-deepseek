# 黑板模板（主控在 `run <code>` 后立刻创建）

路径：`E:/pi/2/work/bb_<code>.md`
规则：**追加写，不改别人的段**。Recon 只写 `INTEL`，Exploit 只写 `TRIED` / `RESULT` / `NEED_RECON`。

---

```markdown
# BLACKBOARD <code>

## META
- code: <code>
- addr: <host:port>
- difficulty: <easy|medium|hard>
- score: <分值>
- started_at: <HH:MM>
- deadline: <HH:MM>   (easy +5min / medium +10min / hard +20min)
- owner: recon=<agent id> / exploit=<agent id>

## INTEL   <!-- Recon 追加，Exploit 只读 -->
- 指纹: <server header / 框架 / 语言 / 版本>
- 端口: <开放端口列表>
- 疑似漏洞族: <对照 docs/PLAYBOOK.md 的族名>
- 源码线索: <如 /src.zip 200、/openapi.json 可见>
- 敏感路径: <如 /static/ 目录列表、/actuator/env 200>
- 已确认事实: <一句话，可验证的那种>

## TRIED   <!-- Exploit 追加，一行一次 -->
- [HH:MM] <payload 摘要> -> <状态码/报错原文截断> -> <结论: 不通/部分通/可疑>

## NEED_RECON   <!-- Exploit 追加后退出，主控补派侦察 -->
- [HH:MM] <需要侦察什么，要具体到路径/参数>

## RESULT
- flag: <flag{...} 或 UNKNOWN>
- verified_by: <第二条独立路径是什么>
- status: solved | timeout | env_broken | recorded
- family: <对应 docs/PLAYBOOK.md 的族名，如 ping-injection；没有就写技术栈 bottle/flask>
- closed: <HH:MM>
```

> `family` 决定归档到 `docs/CASELIB/<family>/`，漏写会落到 `unclassified`，下次查不到。

## 填写纪律

1. `INTEL` 每条都要是**可验证事实**（“返回 200 且含 Index of”），不写“可能是 Spring”。
2. `TRIED` 必须记**原文报错片段**，被截断的回显没人能复用。
3. `NEED_RECON` 写完立刻退出，不要自己顺手去打 —— 那正是双工分工要避免的事。
4. `RESULT.flag` 必须和 `verified_by` 同时有值才允许 submit；只有一条路径的 flag 先挂 `UNKNOWN`。
