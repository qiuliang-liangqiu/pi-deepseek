# eval

两套评测，离线可跑。

## `mirror/` — 行为快照

把 2026-11-12 的 pi 1.5.2 + deepseek/deepseek-flash + thinking=max 打成可复现包：
6 段系统提示、6 个工具 schema、8 条探针/19 个检查、18 个评分器测试、61 条参考轨迹。

```bash
node eval/mirror/score.mjs verify
node eval/mirror/score.mjs materialize
node eval/mirror/score.mjs score eval/mirror/reference/trace.jsonl
node --test eval/mirror/tests/score.test.mjs
```

注意：本仓库对快照里的本机绝对路径做了脱敏，并**重新计算了 `manifest.json` 的 sha256**，
所以 `verify` 对当前文件仍然 PASS。它不是原始会话的逐字节副本。

## `route/` — skill 路由评测

14 条「题目描述 → 应加载哪个 `ctf-*` skill」用例（`cases.json`），每条跑一次 `pi -p`。

```bash
python eval/route/route_eval.py --dry-run          # 离线看用例
python eval/route/route_eval.py --tier smoke       # 需要已配置 pi + 模型 key
python eval/route/route_eval.py --id web-jwt-upload
```

结果写到 `eval/route/out/`（已 gitignore）。`results/` 里是 2026-11-16 的三次参考运行。
