#!/usr/bin/env python3
"""build_manifest.py — 重新生成仓库里的 agent/.workbench.json（发布清单）。

清单记录配置层每个文件的 sha256（CRLF 归一化）和版本/commit，装在别的机器上的
`update-check` 用它判断「本地文件和 GitHub 发布内容是否一致」（content_diff）。

    python scripts/build_manifest.py            # 写入 agent/.workbench.json
    python scripts/build_manifest.py --check    # 只比较，不写；不一致 exit 1

发布流程会自动调用本脚本；手工发版时记得重新生成，否则别人会
看到假的 content_diff。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_update():
    spec = importlib.util.spec_from_file_location("wb_update", HERE / "update.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(repo: Path) -> dict:
    mod = load_update()
    sources = mod.source_files(repo)
    agent = repo / "agent"
    try:
        version = (agent / "VERSION").read_text(encoding="utf-8-sig").splitlines()[0].strip()
    except Exception:
        version = ""
    return {
        "schema": "workbench/1",
        "commit": mod.repo_commit(repo),
        "version": version,
        "repo": "<repo>",
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": {rel: mod.sha(p) for rel, p in sorted(sources.items())},
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--check", action="store_true", help="只比较，不写入")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    manifest_path = repo / "agent" / ".workbench.json"
    data = build(repo)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            print(f"没有可比较的清单：{manifest_path}", file=sys.stderr)
            return 1
        if old.get("files") == data["files"] and old.get("version") == data["version"]:
            print(f"OK: manifest up to date ({len(data['files'])} files, v{data['version']})")
            return 0
        missing = sorted(set(data["files"]) - set(old.get("files") or {}))
        extra = sorted(set(old.get("files") or {}) - set(data["files"]))
        changed = sorted(k for k in data["files"] if k in (old.get("files") or {})
                         and old["files"][k] != data["files"][k])
        print(f"STALE: v{old.get('version')} -> v{data['version']}, "
              f"+{len(missing)} -{len(extra)} ~{len(changed)}")
        for label, rows in (("new", missing), ("stale", extra), ("changed", changed)):
            for rel in rows[:10]:
                print(f"  {label:7} {rel}")
        return 1

    manifest_path.write_text(text, encoding="utf-8")
    print(f"manifest written: {manifest_path} ({len(data['files'])} files, v{data['version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
