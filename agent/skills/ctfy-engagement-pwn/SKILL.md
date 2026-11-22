---
name: ctfy-engagement-pwn
description: CTFY / tpctf 的 engagement（WireGuard VPN）题目：Windows 无管理员时用 wireproxy 建 SOCKS 出口、平台 503 限流/僵尸实例的应对、菜单式 pwn 与 reverse/字体题（checker+font pipeline 链式反推）、ASan 题的 heap 复用与栈 shadow 打法、多段 web/CVE 链（AsterOps/cve_stage）的“公开情报优先 + 确定性 harness”打法，以及游戏/交互类题的工程纪律（先算信息上界、先量传输成本、延迟注入回归、流水线 pulse、跑通即备份，见 §8）。当题目地址形如 xxx.internal、说明里出现 "VPN / .conf / ctfy-callback / engage"，或出现 cve_stage / AsterOps / 多段 gate token、或要做“实时操作 + 随机数”类靶场（游戏 bot）时使用。
---

# CTFY engagement 题 playbook

本次实战：`clean-room`（pwn/hard，ASan+UBSan+seccomp，菜单式记录管理器）、`Hint Me Maybe`（reverse/medium，TTF hinting VM + checker pipeline）、`cve_stage`/AsterOps（web/medium，4 段 gate：litellm wheel → PHP stale unserializer → Node brotli CVE → Go toolchain）。
参考实现：`clean-room` 在 `<workbench>/比赛/2`（`exploit.py` `ch.py` `socks.py` `up.sh` `WRITEUP.md`）；
`Hint Me Maybe` 在 `<workbench>/比赛/1`（`suleq.py` `fontinv2.py` `chain0.py`；wireproxy 配置 `wgproxy*.conf`）；
`cve_stage` 在 `<workbench>/比赛/3/cve_stage`（`WRITEUP.md`、`solve/`、`_ctfy/`）。

**工程纪律见 §8**（先算信息上界 → 先量传输 → 延迟注入回归 → 流水线 pulse → 跑通即备份）。
这节来自 `The Last 3 Minutes`（游戏 + 随机数题，4 小时里 3 小时的弯路）。

## 1. 建立隧道（Windows，无 admin）

```bash
# 平台：GET /api/v1/instances/<id>/vpn-config  （Bearer = CTFY_TOKEN）
#   经常 503，循环重试即可：8~10s 一次，一般 < 2 分钟能拿到
nohup ./bin/wireproxy.exe -c wgproxy.conf -i 127.0.0.1:25446 > wgproxy.log 2>&1 &
```

* `wgproxy.conf` = 平台 conf + 末尾 `[Socks5] BindAddress = 127.0.0.1:25346`
* **`DNS` 是否保留取决于连接方式**：保留隧道 DNS 时 wireproxy 可直接做域名 CONNECT；若把它删掉（`cve_stage` 这次为了减少变量删了），就必须自己解析——用 DNS-over-TCP 到隧道 DNS（`dnsq.py`，如 `172.17.87.3`），然后按 IP 连接（`curl --socks5`），或改用 `--socks5-hostname` 并确保代理能解析。删 DNS 后直接域名 CONNECT 会 `0x04 host unreachable`，不是隧道坏了。
* wireproxy 会**偶发停止监听**（进程还在、`connect` 被拒）。客户端要有看门狗：
  连接失败 → `taskkill //F //IM wireproxy.exe` → 重新拉起 → 重试，别先怀疑题目。
* 只承载 TCP。要解析/探测就用 **DNS-over-TCP** 到隧道 DNS（见 `dnsq.py`）。
* 域名解析：若 wireproxy 仍保留隧道 DNS，可 `curl --socks5-hostname` 交给代理解析；
  若已删 DNS，先用 `dnsq.py` 解析成 IP，再 `curl --socks5 http://<ip>:<port>`。
  `ncat --proxy 127.0.0.1:25346 --proxy-type socks5` 同理。
* 回连：wireproxy 用 `[TCPServerTunnel] ListenPort/Target` 把隧道侧端口转本机（SOCKS 不走 UDP/ICMP）。

## 2. 平台侧（CTFY）

| 现象 | 含义 | 动作 |
|---|---|---|
| `503` + 203 字节 `platform_busy` | 线程池 shed | 按 `retry_after_s` 重试 |
| `503` + 20488 字节 HTML `unavailable` | 边缘/前端兜底页 | 同上，重试 |
| `code=000`（curl 无响应） | 请求**可能已被处理** | 不要当失败；重试（本题 8 次都算 correct） |
| `404 instance_not_found`（附件/SSH/verify） | 平台侧僵尸/实例记录丢失 | 让用户开新实例；旧实例详情 200 也可能是僵尸，别死磕 |
| `404 instance_not_found`（提交时） | 维护中写库不可见 | 看 status；拿 flag 优先，提交挂后台 |

先看 `https://status.ctfy.io/api/status.json` 的 `flag_submission` / `attachments` / `website_api_tsinghua`。
**附件先和静态 zip 比 sha256**（实例附件很可能就是静态模板）；接口挂了就直接找用户要 tgz/实例文件
（本题靠用户给包，5 分钟定位；之前黑盒摸了 40 分钟）。

**提交/验证**：`verify_answer` 是 oracle（true/false，不写审计）；`submit_answer` 可能 60s 超时但已写库，
查实例 `questions[].answered_correctly` 确认；拿到服务返回的 flag 后先 verify 再 submit，最后 `stop_instance`。

提交走 `platform_do.py`（唯一被 `platform-guard` 放行的口；裸 POST 会被拦）：

```bash
# 服务题（有 host:port）：按地址唯一匹配本队 ready 实例后自动提交
python ~/.pi/agent/bin/platform_do.py submit --target <host:port> --question flag1 --answer-file f.txt
# 只有实例/附件题：只输出计划，用户在自终端跑 platform_approve.py 生成 approval
python ~/.pi/agent/bin/platform_do.py submit --instance-id <uuid> --question flag1 --answer-file f.txt
python ~/.pi/agent/bin/platform_do.py submit --instance-id <uuid> --question flag1 --answer-file f.txt --approval approvals/<...>.json
# 先看计划不提交：加 --dry-run
```

平台侧等价语义：`{"instance_id":"<uuid>","question_id":"flagN","answer":"FLAG{...}"}`，
返回 `correct` / `solve_rank` / `challenge_fully_solved`。

- 竞赛实例若 `/api/v1/instances/<id>` 报 `platform_busy`/`instance_not_found`，改用
  `/api/v1/competitions/<competition_id>/instances/<id>`（200 可能是可用视图）。
- `POST /api/v1/instances/<id>/renew?competition_id=<cid>&ttl=7200` 可续期；长题先续期再死磕。
- `/verify-answer` 与正式提交是两回事：只 verify 不提交 = 没分（verify 不在 guard 名单里，可以继续 raw；提交一律走 `platform_do.py`）。
`solves_count` 是“题是否可解”的硬信号；别人有 solve 而我们模型跑不通时，优先怀疑自己的数据流模型。

## 3. 菜单式 pwn 服务

* 写 expect 型客户端：读 `> ` 菜单、按提示应答（`ch.py`）。
* 坑：定长 `read`/`fread` 后面的**残留换行会被当命令**，表现为莫名 `EOF`/`command sequence rejected`。
  用"不带换行的裸发送"（`!payload`）验证。
* 先摸清**隐藏状态机与预算**（同命令不能连用、命令配额、字节配额）——作者往往写在 README 里。
* 先黑盒 15~20 分钟理解协议是值得的，**超过就停下来找源码/附件**。

## 4. pwn/ASan 题的通用打法（本题具体套路）

1. **读路径常无校验、写路径才有**：先找"能回显任意地址"的口子，再用写限制反推能打哪里。
2. **ASan 默认 quarantine 256MB**：`free` 后想复用必须先把它顶出去。
   本题 16 次 `alloc(16MB)+free` 就把队列头的 0x100 chunk 回收了 → `delete`+`create` 得到 type confusion。
3. **shadow 可写 = 自己毒化栈**：`shadow = (addr>>3) + 0x7fff8000`，一次写 8 字节 shadow 覆盖 64 字节栈；
   然后走"整段 poisoned 才允许写"的通道（服务会先 `__asan_unpoison_memory_region`）。
4. **canary 位置不能凭感觉**：`-fstack-protector-all` + `and rsp,-32` 让 canary 偏移随栈对齐变化。
   有 debug_info 就自己反汇编（`pyelftools` + `capstone`，Windows 上没有 objdump/gdb）算偏移；
   或者干脆**只覆盖返回地址本身**（8 字节），完全不碰 canary。
5. 泄漏链优先级：`.rodata` 里的静态指针（`label`）→ PIE base → `environ@got` → `&environ` → libc base → `*environ` → 栈。
6. seccomp 只放什么，就把 ROP 写成什么：`openat(O_RDONLY)` / `read(fd≥3, ≤0x1000)` / `write(1|2)`。
7. **把预算计数写进 exploit**（echo/write/ops 实时打印），本题收尾 `344/472`、`305/440`、`61/78`，
   半路就知道还能不能塞下 ROP。

## 5. 收尾

* flag 到手就立刻落盘（`run2.log`），别只留在终端里；顺手 grep 一次确认格式。
* 用完关隧道：`taskkill //F //IM wireproxy.exe`；后台重试循环记得杀掉。

## 6. reverse/字体题：checker + font 的 pipeline（Hint Me Maybe / AUTHORED-37DA5EFD）

本次实战：`Hint Me Maybe`（TPCTF 2026，reverse/medium）；附件 `challenge.ttf` + `checker`；平台 flag 是 dynamic。

### 6.1 先比 hash，别假设 per-instance
* 静态 zip 与 `GET /instances/{id}/attachments/{name}` 的 sha256 可能完全一致；先 `sha256sum` 再决定是否找“patch 过的实例附件”。
* 实例详情 200 但 attachments 404/503、SSH 404、verify 404 → 平台侧僵尸实例；直接让用户开新实例，不要死磕。

### 6.2 checker 的输入/返回语义
* 先 `strings checker` 看 usage：本题 `usage: %s <24 hex characters>`；候选是 24 hex，不是 `FLAG{...}`。
* 服务（15001）对错误候选回 `no`；对正确 24-hex 直接回 `FLAG{...}`（它同时是 hint 和 flag dispenser）。
* 不要用 `FLAG{}` 形式去试服务；先按 checker 的 usage 发裸 24-hex。

### 6.3 checker 的 pipeline 数据流（本题题眼）
* checker 不是“每个中间字形都用同一组 24 个坐标”；它把当前字形的 16 个 SCFS 点 y/64 写回 coords，
  **下一个字形用这 16 个输出作为新的 variation 轴**（+8 个 0）。
* 链：`A→B→...→H→I`（block0，窗口 axes0..15）和 `J→K→...→Q→R`（block1，窗口 axes8..23）；
  最终字形 point 66 的 y 必须 = 640。
* 只读调用序列不够，必须追“谁把什么写回哪里、下一个环节读什么”。

### 6.4 链式反推
* 普通中间字形 = 4 轮 SPN：`out = K_r ^ XOR_{i∈M_r}(S_r(state_i)^S_r(0))`；对每个 keybase 用 VM 求 S/K/M，可逆。
* 特殊字形 A/J 有前缀：`state[perm[i]] = input[i] ^ B2/B3[i]`（B2=1584..1599，B3=1600..1615）；反推时先 `Finv` 再 `prefix_inv`。
* 最终 I/R 先用 basis 差分求 `G(main_out) → check_state` 的置换+XOR（I: `check[P[i]]=y[i]^c[i]`；R 同法单独求），
  再反推 `check_state → main_out → glyph input`。
* 两段在 axes 8..15 的重叠必须一致；本题拼出 `c0cc6d622d42faba82e87c92`，服务返回 flag。

### 6.5 收尾
* 拿到服务返回的 flag 后，先 `verify_answer`（true）再 `submit_answer`；MCP submit 可能 60s 超时但已写库 → 查 `answered_correctly`。
* 确认 `my_solved` / `fully_solved` 后 `stop_instance` 释放名额；后台重试脚本和 wireproxy 一并清理。
* 工具链：`capstone` + `pyelftools` 读 ELF（Windows 没 objdump/readelf）、`fontTools` 读 TTF、`freetype-py` 实测 point、`z3` 解约束；
  每一层模型都用真实 VM/FreeType 输出交叉验证。

## 7. 多段 web / CVE 链（AsterOps / cve_stage 实战）

本次实战：TPCTF 2026 `cve_stage`（web/medium，4 段 gate：litellm wheel → PHP stale unserializer → Node brotli CVE → Go toolchain）。
参考实现：`<workbench>/比赛/3/cve_stage`（`WRITEUP.md`、`solve/stage1_wheel.py`、`solve/solve.py`、`solve/stage3_4.py`、`_ctfy/` 原始请求与触发流）。

### 7.1 先拆 gate 链路，别从第一段盲打

* `docker-compose.yml` 里每个服务挂载的 `next-token`/`flag` volume 就是通关图：
  `litellm <- gate_php`、`php-runtime <- gate_node`、`node <- gate_go`、`go <- answer_flag4`。
  README 的 “each later panel unlocks with the secret from the previous stage” 要当成硬约束。
* 每段先确认：入口路径（nginx `location ^~ /xxx/`）、认证方式（`X-Stage-Token` + cookie）、
  flag/token 文件路径、服务用户/权限。把四段写成一张表再动手。
* 本地没有 Docker 不代表不能复现：先找已有 Linux VM/远程机（`ssh vm`），把 compose 丢过去；
  本机只负责 HTTP 客户端和 VPN。Windows 上 `wireproxy` + Python/curl 足够。

### 7.2 公开情报优先（本次最大教训）

遇到“某产品 + 某版本 + 自定义 harness”的题，**先查公开 CVE/修复 commit/官方测试/公开 PoC**，再决定是否手推：

1. `NVD API`：`https://services.nvd.nist.gov/rest/json/cves/2.0?cveId=CVE-...`
   （本次 `CVE-2026-111497` 直接查到“invalid block switch 被当作 NEEDS_MORE_INPUT”）。
2. `cveawg.mitre.org/api/cve/<id>` 看 references / 修复版本。
3. GitHub 修复 commit 的 diff：`api.github.com/repos/<org>/<repo>/commits/<sha>`；
   它直接告诉你哪条分支/哪个函数是 bug。
4. 官方 regression test 的输入字节（本次 Node `test-zlib-invalid-input.js` 里的 22 字节 PoC）。
5. 搜公开 PoC 仓库：`api.github.com/search/repositories?q=CVE-...`、
   `search/issues?q=CVE-...`；命名常是 `<product>2shell` / `brotli2crash` / `CVE-xxxx-POC`。
6. 公开 PoC 的“生成器”通常比手写位流快一个数量级；本次直接复用
   `dimasma0305/CVE-2026-111497-POC` 的 `generate_stream()`（23,068,690 字节，SHA256 `b7a6508e...`）。

**时间盒**：纯理论/手推 20–30 分钟没有可验证进展，立刻切到“查公开资料/找 PoC/找官方测试”；
不要因为“白盒”就觉得必须从源码推。

### 7.3 本次 Stage 3 的题眼（可当 brotli/CVE 模板）

* 触发不是 literal/command block switch，而是 **distance block switch**：
  `NBLTYPESD=1`，但 command 循环把 `block_length[2]` 逐条减到 0；
  每条 command 的字典 transform 输出 0 字节，`meta_block_remaining_len` 保持正数（16,777,216）。
* 这样 invalid switch 发生在 **safe path**，proactive `WriteRingBuffer` 的
  `meta_block_remaining_len < 0` 检查会通过；恢复分支才能逐字节 append。
  如果走 literal switch（16M literal insert），rem 会先变负，直接返回 -9，永远进不了 append。
* 挑战 patch 把 `buffer_length`/`ctf_cookie` 放在 `buffer.u8[8]` 后；`-O2` 下
  `buffer.u8[8]` 的越界写是 UB，`buffer_length++` 的整块 store 会覆盖刚写进去的字节，
  实际是 **顺序递增**。所以不需要 steering：只要 append 到 24，让 index 16..23 的
  输入字节等于 `NODERCE!` 即可。这个结论必须靠本地 harness 打日志确认，不能靠 C 抽象机推。
* `chunk1 = trigger + 3 个尾字节`（invalid switch 时留 1 个 unread 字节，tail copy 后
  `buffer_length=1`），`chunk2 = 15*'B' + 'NODERCE!'`；body 23,068,716 字节，
  gzip 后 44,858 字节，`Content-Encoding: gzip` 上传 2.4 秒。
* 远端 decode 返回 `controlFlowTriggered:true` + `proofNonce`；再 `GET /node/proof`
  带 `X-Proof-Nonce` 拿 flag 和下一段 token。

### 7.4 自定义 harness 的调试纪律

* 先复刻**完全相同的编译参数**（本次 `-O2 -fPIC -DBROTLI_CTF_DETERMINISTIC_HOOK=1`，
  C 源 + C++ addon），在本地 VM 编一个 standalone harness；不要只读源码。
* 把成功条件做成可观测 oracle：append 的 `idx/bitpos/state/cookie`、invalid switch、
  `meta_block_remaining_len`、`pos`、`ringbuffer` 是否为空、是否走到 proactive push。
* **热路径不要放 fprintf**：16M 次 command 的 per-command 日志会把 1.4s 拖成 150s；
  先计数器/条件打印，性能测试前把热路径日志删掉再重编。
* 传输限制先看常量：`MAX_DECODED_BODY`、`MAX_GZIP_BODY`、`content-length`、nginx
  `client_max_body_size`；能 gzip 就 gzip，能分块就分块，先算大小再发。

### 7.5 多段题的提交纪律

* 每段拿到 flag 立刻：`verify-answer` 确认 → `platform_do.py submit --target <host:port> --question flagN --answer-file f`
  正式提交 → 记录 `answered_correctly`；不要攒到最后（实例可能到期/被回收）。
* 只有实例、没有端口的题：`--instance-id` 只出计划，需用户在自终端 `platform_approve.py` 生成 approval 再 `--approval`（一次性）。
* 提交后看 `challenge_fully_solved`；四段全部 `answered_correctly=true` 才算完。
* 快到期先 `renew`；隧道 `wireproxy` 偶发 SOCKS 假死（TCP 可连但不回 greeting）→
  杀进程换端口重拉，别先怀疑题。

### 7.6 本次可复用的两个“小工具”

* `dnsq.py`：DNS-over-TCP 到隧道 DNS，绕过 wireproxy 删 DNS 后的域名解析问题。
* `solve/stage3_4.py`：`generate_stream()` + gzip 上传 + proof 读取 + go build，
  是把“公开 PoC → 远端 flag”串起来的最小脚本，换实例只需改 base/token。

## 8. 工程纪律（The Last 3 Minutes 复盘，游戏/交互/随机数类题通用）

本次实战：`The Last 3 Minutes`（crypto/web，浏览器平台跳跃 + 64 bit 保险箱密码）。
参考实现：`<workbench>/比赛/5`（`bot4.py` 延迟容错 runner、`probe_fight9.py` 单杀零伤验证、
`REPRO.md` 复现记录、`RETRO.md` 完整复盘）。

### 8.1 先做三件事，再写一行利用

1. **信息预算**：任何“从随机输出推目标值”的题，先写 20 行算
   `每次抽取的可观测量 → bit/次 → 每局/总预算` 与 `目标状态长度` 的对比。
   本次：`ACTIONS.get(getrandbits(6),'idle')` 把 61/64 折叠成 idle → 0.347 bit/次，
   180 s 只有 1249 bit，而 MT19937 状态要 19937 bit → 一开始就该砍掉这条线。
2. **传输三问**：keep-alive 吗？单请求耗时分布？并发上限？
   本次直到复现阶段才发现 Werkzeug dev server 每个响应 `Connection: close`，
   即**每个 API 请求都要新建 TCP(+SOCKS)**，600 个控制请求 ≈ 66 s 起。
3. **判据先写**：本次“成功”是“能通关”还是“能拿到 flag”？写下来再动手。

### 8.2 可直接复用的数字与坑（本次实测）

* **每请求建连成本**：隧道内 100–130 ms（抖动到 3 s），本地 127.0.0.1 约 1–5 ms；
  8 条连接并发后有效 ~140 ms/请求，16 条反而退化。
* **wall-clock 追平的服务端**（`advance()` 按 `time.monotonic` 一次补 N 个 20 ms 步）：
  1 个 RTT = 5–6 步 = 34 px 的“盲飞”；本地只有 0.3 px。
* **输入锁 `input_until = sim + 800 ms`**：一次 >800 ms 的停顿会把 30 px 微调变成 228 px 整段滑走。
* **流水线 pulse**：方向 + 停止两条请求**同时发**（间隔 `gap`），位移 = speed×gap，与 RTT 无关。
  比“发方向 → 等响应 → sleep(hold) → 发停”稳一个量级，残余误差 ±3–6 px。
* **冷却语义**：本题 `403` 也会写入 320 ms 冷却（打空很贵），`429` 不写（可放心重试）。
  先读服务端代码确认，再决定“盲打”还是“看到再打”。
* **命中窗口**：`|dy|<75` / `forward∈[-10,115]` 这类判定会把命中窗口压到跳跃最后 ~100 ms，
  而对方 windup 是 640–700 ms → 不要靠反应，要用结构性手段（骗空 volley 换 1400 ms 免疫）。
* **SSE 就是时钟 + 传感器**：这类服务常有 `/events`（50 Hz 推进 + 200 ms tick + windup/throw 事件），
  能同时省请求数和降状态陈旧度；本次写了却只用来抓样本，是明显的浪费。

### 8.3 流程纪律（每条都有代价）

* **跑通即备份**：`cp run.py run_ok_<HHMM>.py`；`.pyc` 不能当备份（会被后续源码覆盖）。
  本次唯一能通关的版本被后续编辑覆盖，赛后无法复现。
* **每轮清环境**：残留 python 进程（listen/relay/http.server）会把本地从 1 ms 拖到 5–20 ms、
  偶发 3 s，把“本地基准”直接毁掉；本地验收前先 `tasklist` 清场。
* **延迟注入回归**：本地起“固定 150 ms + 偶发 2 s 停顿”的 TCP 代理，bot 过了才算逻辑对；
  0 RTT 的 3/3 是假信号（本次为此多花 2 h）。
* **无效实验先自检**：搜索器先喂“已知真值”（如本地强制走 fallback seed 再搜回）；
  网格精度必须 ≥ 真值精度（本次用 ms 网格搜 ns 精度的 `random_seed_time_pid`，数学上永不命中）。
* **矛盾先证伪自己**：别人解出而我方“证明不可解”时，先怀疑部署/镜像/权限
  （换实例、rebuild、比 image digest、查 authoring/registry scope），而不是继续加固证明。
  本次 1/139 首杀 13 分钟完成，显然不在 App 内。
* **结束前 15 分钟强制写 RESULT**（做到哪、结论、证据路径），别赛后考古 1.9 MB 日志。
* **有结论就写回 skill**：pool / pulse / 冷却语义 / 信息预算这些是可复用的，别留在临时脚本里。

### 8.4 本次的技术结论（避免重复投入）

* 本地确实通关过 3 次（`/safe` 返回 200 = 位置校验 + 四门校验都过），但密码是
  每局新的 `random.Random().getrandbits(64)`（urandom 播种），游戏内可观测 ≤ 9080 bit，
  **信息论上不可解**；`/debug/<gid>/password`、`/answers/flag`、`/flag.txt` 线上全 404。
* 因此这类题一旦算出“预算 < 需要”，正确动作是：① 确认部署=附件；② 查平台 scope/镜像；
  ③ 把“别人怎么解”当 P0，而不是继续做 bot。

