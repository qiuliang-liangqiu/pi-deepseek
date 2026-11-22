# PWN 模式召回卡（60 秒内从表象想到手法）

目标：看到题目的**第一个异常信号**时，立刻知道试哪类原语、先做哪个实验。
来源：强网杯第十届 17 道 PWN 的共性 + 本机复现踩坑。只记“触发条件”，不记题号。

## 0. 开局固定 60 秒

```bash
file ./bin; checksec --file=./bin
readelf -hW ./bin | grep Type; readelf -lW ./bin | grep -E 'GNU_RELRO|LOAD'
strings -a -t x ./bin | grep -iE 'flag|/bin/sh|%s|%n|system|popen|/tmp|\.jrn|token|secret'
ldd ./bin; ls -l libc* ld* 2>/dev/null
```

然后**跑一次**：记录输入语法（行/长度前缀/TLV/菜单）、输出、是否 fork、是否有超时。
再决定主类型：栈 / 堆 / 类型混淆 / 逻辑+表 / 盲 oracle / 沙箱 / 内核 / 设备。

## 1. 信号 → 手法（按优先级）

| 你看到的信号 | 第一反应 | 第一个实验 |
|---|---|---|
| 同一个变量既做 `malloc` 又做 `memcpy/read` | 长度截断/脱钩 | 试边界：0,0x7f,0x80,0xff,0x7fff,0x8000,0xffff,0x10000,0xffffffff；看 `int8/16/32`、`sh`、`min/cap` |
| 长度字段带“协商/上限” | 负数/下溢当无符号 | 令协商值=0，算 `x-常量`；看是否变巨大 |
| off-by-one 只改 size/低半字节 | 块头半字节写 | 只放大 size、保留 PREV_INUSE；配合 unsorted/largebin |
| 有 `add/edit/free/show` 菜单 | 堆 UAF/overlap | 先 free 再 show/edit；看 tcache key/count、chunk 复用 |
| 删除/关闭时只清一个表 | 悬空引用 | 查所有表/缓存/回调/引用计数：谁还认旧句柄 |
| 缓存以 `(index, ptr)` 为键 | 键少一维 → 缓存不失效 | 让同一对象换槽号/换容器再操作，看旧缓存是否还命中 |
| 多线程 + 共享表 + 延迟队列 | 竞态/双重 free | 灌队列、乱序事件、drop/pickup 交错；先测“数量差” |
| `tag/union/elements kind/vtable` | 类型混淆 | 找“检查 tag 的分支”和“用字段的分支”是否同一份 tag；release 下 ASSERT 是否被裁 |
| 自研 VM / 字节码 / `.abc` | 无验证器 | 改立即数（slot/reg/len），先越界读 8 字节 |
| JIT / JS 引擎 | 类型反馈 + 代码指针 | 找 `double↔string`/Smi↔object 的错配；先 `addrOf`/`fakeobj` |
| 沙箱（V8/QEMU/VM） | 先箱内 RW，再逃逸 | 改 backing store 拿箱内任意读写；找 PIE/宿主指针 |
| 没有输出/只回 OK/连接断 | 1-bit oracle | 候选：连接关闭(EPIPE)、`error` 尾巴、错误码、时间、MAC/状态校验、长度回显 |
| 内核 ioctl / 自定义设备 | 对象生命周期 | 建/删/查顺序；UAF 后用同尺寸对象占位；看 cred/token |
| MMIO + timer + DMA | 设备 UAF | reset 不 del timer；DMA 描述符写寄存器；释放→复用→覆盖同一次回调 |
| MIPS/ARM/静态 | 架构细节 | MIPS：gp/t9、delay slot、unaligned `ld/sd`=SIGBUS；ARM：Thumb 对齐 |
| Full RELRO + glibc≥2.34 | 无 hook | FSOP：`_IO_wfile_jumps` + `setcontext`；或改返回地址/栈迁移 |
| seccomp 只给 read/write/exit | ORW | 不要找 system；open/read/write 或 sendfile |
| 字符串里有 `%s/%n` 且可控 | 格式化字符串 | 先 `%p` 定位参数，再 `%n` 写 GOT/返回地址 |

## 2. 四阶原语梯子（卡住时问“差哪一阶”）

1. **泄漏**：栈/堆/PIE/libc。
   - 优先找“把指针当数据回显”的路径：悬空 chunk 的 fd、`description_addr`、错误消息、长度回显。
   - 没有回显 → 找 1-bit oracle（见第 1 节最后几行）。
2. **任意读**：通常来自 `data 指针 + size` 结构（ArrayBuffer/ByteArray/string/desc）。
   - 先做“改指针读 8 字节”，再批量。
3. **任意写**：tcache/unsorted poison、GOT、BSS 槽指针、表项指针。
   - 写之前先确认目标可写（RELRO 范围、GNU_RELRO 之后的段）。
4. **控制流**：返回地址、函数指针、vtable、回调、`setcontext`/`longjmp`。
   - seccomp 下直接 ORW；有 `system` 且能控参数就 system。

## 3. 每类手法的“第一个实验”模板

**长度/截断**
```
for n in [0,1,0x7f,0x80,0xff,0x100,0x7fff,0x8000,0xffff,0x10000,0xffffffff]:
    send(len=n, payload=pattern) ; 记录回包长度/崩溃
```
**UAF/缓存**
```
create A; use A (建立缓存); delete A; use A  -> 是否仍返回旧内容
create B (同尺寸) 占位; use A -> 读到 B 的字段?
```
**类型混淆**
```
找 addrof: 把对象写进“被当成整数”的槽，String/toString 读回
找 fakeobj: 把“整数”写进“被当成对象”的槽，访问字段
```
**盲 oracle**
```
对同一输入重复 3 次，确认判定稳定；再测“猜对/猜错”的差异是否可区分
二分/逐字节；失败重试换 nonce/画布
```
**设备/内核**
```
建对象 → 触发 free → 用同尺寸对象占位 → 旧回调是否还指向旧块
```

## 4. 本机实测的坑（看到就绕）

- **长度谎言溢出不要用固定 filler**：`0x11` 会写坏后面 fastbin 块的 `size`，free 时
  `malloc_consolidate` 顺着 `0x1111...` 走崩。做法：从 `/proc/mem` 复制溢出区原始字节，
  只改目标块头。
- **改小 top size 会破坏相邻 fastbin 的合并判定**：先 malloc 吃掉相邻 fastbin 块，再 free。
- **libc epilogue 入口要看清**：`0x2831c` 会 `ld ra,0xf8(sp)`，`0x28324` 不会；
  差两条 `ld` 就是每 `+0x100` 死循环。
- **arena 先分清**：`P=A&~0xFFFFFF` 这类公式只对 main arena；thread arena 的 unsorted
  指向 arena 自己，要按 heap_info/malloc_state 布局重标定。
- **QEMU/user 下地址**：libc/栈可能固定，PIE/堆随机；用 `/proc/maps` + 栈扫描现场标定，
  不要硬编码。
- **WP 的偏移只对同构建**：换 libc/编译器/arena 必须重算；WP 里 `<端口>` 这类红字说明
  PoC 不能直接抄，先跑出第一个 oracle。

## 5. 一句话决策

- 有回显 → 先泄漏，再任意读写。
- 无回显 → 先造 1-bit oracle，别急着 ROP。
- 有表/缓存/回调 → 先问生命周期，再问类型。
- 有长度 → 先问“分配到拷贝之间有没有窄化/取整/下溢”。
- 有沙箱 → 先箱内 RW，再找 PIE/宿主的跳板。
- 有 seccomp → 直接 ORW，不要浪费时间找 system。
