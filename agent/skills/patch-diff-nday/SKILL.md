---
name: patch-diff-nday
description: 从厂商修复反推漏洞——拿补丁前后两个构建、对齐符号、函数级 diff（相似度 0.5–0.95 才是目标）、读新增守卫推断 bug 类（OOB/UAF/整数溢出/竞态/信息泄露/缺权限检查）、写只崩未打补丁版本的 PoC 并在打过补丁的版本上验证干净。给 CVE 无公开 PoC、Patch Tuesday、发行版 backport 缺口、闭源安全补丁、或 CTF 里给了源码/二进制 diff 的题。【中文触发】补丁差分/patch diff/N-day/给个 diff 找洞/CVE 复现。没有 diff/两版本时不要用（走 rev）。
metadata:
  tags: [ctf, patch-diff, n-day, reverse]
---

# 补丁差分恢复 N-day

> 蒸馏自 ARTEX `patch-diff`（源自 zhaoxuya520/reverse-skill，MIT）。

## 0. 边界

只在**自己的 lab、题目给的容器、或平台授权的靶场**里跑。PoC 只证明漏洞（崩溃/挂起/内存破坏证据），
不产出可交付武器；对外内容一律用 `{target}`/`{host}`/`{user}` 占位符。
**环境缺工具就报告并停**，禁止编造 diff 输出或崩溃证据。

## 1. 什么时候用

1. 已知 CVE 但没有公开 PoC（只有 fix）。
2. Patch Tuesday：按可利用性给本月补丁排序，再决定深挖哪个。
3. Backport 缺口：主线修了，发行版/LTS/OEM 还没跟上。
4. 闭源安全补丁：只有 advisory，要看真实改动。
5. **CTF 变体**：直接给了 `vuln.c` / `fixed.c`，或两个版本的二进制 —— 这时第 2/3 步可以跳过，直接进第 4 步。

## 2. 拿 before/after

- **Windows**：Microsoft Update Catalog 取「最后一个未打补丁」与「第一个已打补丁」两个 MSU；
  `expand.exe pkg.msu -F:* out\` 再解内层 `.cab`；取 `ntoskrnl.exe`、`win32kfull.sys`、目标驱动。
- **Linux**：`apt download linux-image-<v>`（两版）→ `dpkg-deb -x`；`vmlinuz` 用 extract-vmlinux 还原
  vmlinux；配套 dbgsym。
- **第三方**：厂商归档里的 N-1 与 N 安装包。
- 每个工件记 **sha256**，报告引用 hash 而不是文件名。

> **脱网/镜像降级（TSec 闭卷、无公网）**：以上取包路径全部不可用，**不要反复尝试联网**。
> 改成：① 只用题目已给的源码/二进制/两个版本；② 若只给一个版本，按第 5 节表猜 bug 类直接打边界值；
> ③ 本地无 ghidra/ghidriff 时用 `strings/nm/objdump` 先看符号与字符串差异。

## 3. 对齐符号

- Windows：`symchk /v module.dll /s SRV*c:\sym*https://msdl.microsoft.com/download/symbols`
- Linux：vmlinux + 对应 `System.map` 或 dbgsym

## 4. Diff

- 首选 `ghidriff old new -o out/`（Ghidra headless，pip 可装，出 markdown 报告）；
  BinDiff / Diaphora 仅在已有 IDA/Ghidra 导出时用。
- **过滤相似度 0.5–0.95**：完全一样的是噪声，完全不同的是新增/改名。读新增条件、循环边界、被删代码块
  （删除也是线索）。
- 按控制流/数据流判断，不看 token 级 diff —— 补丁常夹带无关清理和改名。

## 5. 补丁模式 → bug 类（最快的定位表）

| 新增守卫 | 可能的 bug 类 |
|---|---|
| `if (a + b < a)`、`__builtin_add_overflow` | 整数溢出 |
| `KeAcquireSpinLock` / `mutex_lock` | 竞态（TOCTOU、double free） |
| `if (idx >= MAX)`、`if (len > buf_size)` | 越界读/写 |
| `RtlZeroMemory`、`memset(struct,0,...)` | 未初始化内存信息泄露 |
| `InterlockedDecrement` + 引用计数检查 | UAF / 引用计数 bug |
| `ProbeForRead` / `ProbeForWrite` | 未校验用户态指针 |
| `SeAccessCheck`、能力检查 | 缺权限校验 |
| 收紧/删除某 IOCTL 码 | 攻击面收缩（去研究旧路径） |

## 6. 写 PoC 并双向验证

- 整数溢出：边界值（`INT_MAX-1`、`0xFFFFFFFF`）。
- 竞态：并发线程猛打开/关闭 + ioctl 路径。
- UAF：spray → free → 复用。
- OOB：刚好越过守卫的长度/索引。

**必须对两个构建跑同一个 harness**：未打补丁崩、打过补丁干净。只测未打补丁的**证明不了任何事**
（崩溃可能是环境问题）。

## 7. 四个警告

- **补丁可能只是收缩攻击面，不是修 bug**：原始缺陷可能从另一条路径仍可达，把 fix 当线索而不是定论。
- **缓解不是修复**：CFG/XFG 之类的插桩是加固，不是根因修复。
- **编译噪声**：同工具链版本比较，绝不跨大版本编译器 diff。
- **出门先匿名化**。

## 8. CTF 专用捷径

1. **给源码 diff**：先在 diff 里找"新增条件/新增校验"，反推触发输入；再回头读旧版确认缺陷；最后构造边界值。
   不要从整份源码重读一遍。
2. **给两个二进制**：`strings`/`nm` 先比符号与字符串差异（最快的信号），再上 ghidriff。
3. **给 advisory + 一个版本**：先按第 5 节表猜 bug 类，直接打对应边界值，往往不用真做 diff。
4. **判断"哪个更危险"**：看新增守卫落在攻击者可直达的路径上（ioctl/解析器/协议处理）> 内部工具函数。
5. 一旦确认机制，**回到 `ctf-state-search`** 记台账行，并立即检查该机制能否拼到更大的链上。

## 9. 完成自检

- [ ] 每一步都是真跑过，不是读过
- [ ] diff、推断的 bug 类、PoC 工件都在工作目录里且带 hash
- [ ] 打过补丁的版本**真的测过**并记录了干净结果
- [ ] 结论标了 proof basis（executed-live / executed-lab / static）
