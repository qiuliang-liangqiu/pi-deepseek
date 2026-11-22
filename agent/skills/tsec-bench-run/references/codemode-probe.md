# codemode 探活模板（并发 + 限速 + 只回差异）

用途：triage / recon 阶段一次探多个路径或端口，**只把差异行交给模型**。
适用工具：`codemode`（本机已启用——`ctf2` 是 `codemode` 暴露，会自动激活它）。

## 为什么不用逐个 `bash`

- 逐个 `curl` 每个路径一轮工具调用 → 20 个路径 ≈ 20 轮，triage 直接吃满；
- 原始响应进上下文 → 挤爆窗口且全是噪声；
- 速率靠"自觉"，脚本里反而更好守。

## 约束（先读）

- **沙箱没有 `setTimeout`**（QuickJS，无 timer）。限速用"分批 + 批间 `sleep`"实现。
- 默认目标：**并发 ≤6、批间隔 0.3s ≈ ≤20 req/s**；平台/题目给了更严的就按更严的。
- `tools.bash` 返回 `{output, exit_code, wall_time_seconds, truncated}`，不是字符串。
- 脚本里**只有 `text()`/`return` 的内容**进上下文；大输出先写文件再用 `grep`/`read`。

## 模板：路径探活

```js
// @options: {"max_output_tokens": 1200, "timeout_ms": 120000}
const base = "http://TARGET:PORT";
const paths = ["/", "/login", "/admin", "/api", "/swagger", "/.git/config", "/actuator/health"];
const CONC = 6, GAP_MS = 300;

const one = (p) => tools.bash({
  command: `curl -s -o /dev/null -w '%{http_code} %{size_download}' -m 5 '${base}${p}'`,
  timeout: 10,
}).then(r => ({ p, out: String(r.output || "").trim(), code: r.exit_code }));

const rows = [];
for (let i = 0; i < paths.length; i += CONC) {
  const batch = paths.slice(i, i + CONC);
  const rs = await Promise.allSettled(batch.map(one));
  for (const r of rs) {
    if (r.status !== "fulfilled") { rows.push(batch[rs.indexOf(r)] + " -> ERR"); continue; }
    const { p, out, code } = r.value;
    if (code !== 0) { rows.push(`${p} -> curl ${code}`); continue; }
    const [status, size] = out.split(/\s+/);
    if (status !== "404") rows.push(`${p} -> ${status} ${size}B`);
  }
  if (i + CONC < paths.length) await tools.bash({ command: "sleep 0.3", timeout: 5 });
}
return rows.join("\n") || "no non-404 hits";
```

判读：同一路径集合里 **`404` 是基线**；`200/301/302/401/403` 与"404 但 size 明显不同"才是信号。
若站点是 catch-all（所有路径同 size），把判据改成"size 与基线不同的行"。

## 模板：批量取 banner（只留指纹行）

```js
// @options: {"max_output_tokens": 1500, "timeout_ms": 120000}
const targets = ["http://A", "http://B", "http://C"];
const rs = await Promise.allSettled(targets.map(u => tools.bash({
  command: `curl -s -D- -o /dev/null -m 6 '${u}' | grep -iE '^(HTTP/|Server:|X-Powered-By:|Location:)'`,
  timeout: 12,
})));
return rs.map((r, i) => targets[i] + "\n" +
  (r.status === "fulfilled" ? String(r.value.output || "").trim() : "ERR")).join("\n---\n");
```

## 模板：大产物在沙箱外解析、只回候选

```js
// @options: {"max_output_tokens": 2000}
// 1) 让 bash 把解析结果写成 JSON 行（不在上下文里打印原始 XML/JSON）
await tools.bash({ command: "python - <<'PY'\nimport json,xml.etree.ElementTree as ET\n# nmap XML -> 只吐 open 端口\nfor h in ET.parse('nmap.xml').getroot().iter('host'):\n    ip=[a.get('addr') for a in h.findall('address')][0]\n    for p in h.iter('port'):\n        if p.find('state').get('state')=='open':\n            print(json.dumps({'ip':ip,'port':p.get('portid'),'svc':(p.find('service').get('name') if p.find('service') is not None else '')}))\nPY", timeout: 30 });
// 2) 只读关键行
const r = await tools.bash({ command: "cat ports.jsonl | head -40", timeout: 10 });
return String(r.output || "");
```

## 常见坑

- 忘了 `-m 5` → 一条挂起的连接拖住整批（`Promise.allSettled` 会等它）。
- `curl -o /dev/null` 与 `-s` 必须同时给，否则大小/噪声不可控。
- 目标给了更严的限速（比如 5 req/s）→ 把 `CONC` 降到 2、`sleep` 到 1s，别硬顶。
- 脚本失败也会保留已产生的部分输出；失败的调用**不会**回滚，别在同一个脚本里做破坏性写操作。
