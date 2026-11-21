#!/usr/bin/env python3
"""workbench_update.py — 检测 / 安装 pi-agent workbench 的新版本。

    python bin/workbench_update.py check              # 人读
    python bin/workbench_update.py check --json       # 给扩展 / 脚本
    python bin/workbench_update.py check --force      # 忽略 6 小时缓存
    python bin/workbench_update.py apply --dry-run    # 只看会改哪些文件
    python bin/workbench_update.py apply              # 询问后更新
    python bin/workbench_update.py apply --yes        # 不问（扩展确认后调用）
    python bin/workbench_update.py status             # 只看本地 + 缓存，不联网

版本契约
--------
仓库里的 `agent/VERSION`（semver）是唯一版本源；安装 / 更新后同一个文件落在
`~/.pi/agent/VERSION`。远端默认取

    https://raw.githubusercontent.com/<repo>/<branch>/agent/VERSION

失败时回退到 jsDelivr：

    https://cdn.jsdelivr.net/gh/<repo>@<branch>/agent/VERSION

环境变量
--------
PI_UPDATE_CHECK=off      关闭自动提示（扩展读这个）
PI_UPDATE_REPO           owner/name，默认 qiuliang-liangqiu/pi-deepseek-v1
PI_UPDATE_BRANCH         默认 main
PI_UPDATE_URL            直接指定 VERSION 的 URL（自建镜像 / 测试用）
PI_UPDATE_TTL            检查缓存秒数，默认 21600（6 小时）
PI_AGENT_DIR             安装目录，默认 ~/.pi/agent
PI_WORKBENCH_REPO        workbench 仓库 checkout 路径（apply 时优先用）

退出码
------
check: 0 = 检查完成（含 up_to_date / offline；网络失败不是错误码，看 status 字段）
      2 = 参数错误（argparse）
apply: 0 = 成功（或已是最新）；1 = 更新失败；2 = 找不到更新器 / 下载失败

安全说明
--------
`check` 只读一个版本字符串（严格校验 `^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$`），
不会执行远端内容。`apply` 会下载并运行本仓库的 `scripts/update.py`，所以永远
先打印计划、默认要用户确认；`--yes` 只在用户已经确认后使用。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

DEFAULT_REPO = "qiuliang-liangqiu/pi-deepseek-v1"
DEFAULT_BRANCH = "main"
VERSION_REL = "agent/VERSION"
MANIFEST_REL = "agent/.workbench.json"
CACHE_NAME = ".update-check.json"
DEFAULT_TTL = 21600
ERROR_TTL = 1800
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")
SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def agent_dir() -> Path:
    return Path(os.environ.get("PI_AGENT_DIR") or (Path.home() / ".pi" / "agent")).expanduser()


def now() -> int:
    return int(time.time())


def read_text(path: Path) -> str:
    # utf-8-sig: PowerShell 5.1 的 Set-Content -Encoding UTF8 会写 BOM，
    # 带着 BOM 的版本串会被 VERSION_RE 拒掉，看起来像“本机没有版本”。
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace").strip()
    except Exception:
        return ""


def local_version(agent: Path) -> str | None:
    text = read_text(agent / "VERSION")
    v = text.splitlines()[0].strip() if text else ""
    if v and VERSION_RE.match(v):
        return v
    try:
        data = json.loads((agent / ".workbench.json").read_text(encoding="utf-8-sig"))
        v = str(data.get("version") or "").strip()
        if v and VERSION_RE.match(v):
            return v
    except Exception:
        pass
    return None


def source_key() -> str:
    """缓存键：只看“哪个仓库/分支/自定义 URL”，不看这次走了哪个镜像。"""
    override = os.environ.get("PI_UPDATE_URL", "").strip()
    if override:
        return f"url:{override}"
    repo = os.environ.get("PI_UPDATE_REPO", "").strip() or DEFAULT_REPO
    branch = os.environ.get("PI_UPDATE_BRANCH", "").strip() or DEFAULT_BRANCH
    return f"gh:{repo}@{branch}"


def version_sources() -> list[str]:
    """按可靠性排序：GitHub API（权威，60/h/IP）→ raw（快但可能缓存旧值）→ jsDelivr。"""
    override = os.environ.get("PI_UPDATE_URL", "").strip()
    if override:
        return [override]
    repo = os.environ.get("PI_UPDATE_REPO", "").strip() or DEFAULT_REPO
    branch = os.environ.get("PI_UPDATE_BRANCH", "").strip() or DEFAULT_BRANCH
    return [
        f"https://api.github.com/repos/{repo}/contents/{VERSION_REL}?ref={branch}",
        f"https://raw.githubusercontent.com/{repo}/{branch}/{VERSION_REL}",
        f"https://cdn.jsdelivr.net/gh/{repo}@{branch}/{VERSION_REL}",
    ]


def fetch_once(url: str, timeout: float) -> str:
    headers = {"User-Agent": "pi-workbench-update/1", "Cache-Control": "no-cache"}
    if "api.github.com" in url:
        # 直接拿文件内容，而不是 base64 JSON
        headers["Accept"] = "application/vnd.github.raw"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(8192).decode("utf-8-sig", "replace")


def parse_payload(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        # 少数情况下 API 忽略 raw accept，返回 base64 JSON
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and isinstance(obj.get("content"), str):
                import base64
                text = base64.b64decode(obj["content"]).decode("utf-8-sig", "replace").strip()
        except Exception:
            return ""
    v = text.splitlines()[0].strip() if text else ""
    return v if VERSION_RE.match(v) else ""


def fetch_version(timeout: float = 4.0, attempts: int = 2) -> tuple[str | None, str, str]:
    """返回 (version, url, error)。只接受合法版本串，其他内容当数据丢弃。"""
    last_err = ""
    for url in version_sources():
        for i in range(max(1, attempts)):
            try:
                v = parse_payload(fetch_once(url, timeout))
                if v:
                    return v, url, ""
                last_err = f"invalid payload from {url}"
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    last_err = f"HTTP 404：远端没有 {VERSION_REL}（分支/仓库名对不对？）"
                else:
                    last_err = f"HTTP {e.code}: {e.reason} ({url})"
                break  # 4xx/5xx 重试意义不大，换下一个源
            except Exception as e:  # noqa: BLE001 - 网络失败换源
                last_err = f"{type(e).__name__}: {e} ({url})"
                if i + 1 < attempts:
                    time.sleep(1.0)
    return None, "", last_err


def manifest_sources() -> list[str]:
    override = os.environ.get("PI_UPDATE_MANIFEST_URL", "").strip()
    if override:
        return [override]
    version_override = os.environ.get("PI_UPDATE_URL", "").strip()
    if version_override and "VERSION" in version_override:
        return [version_override.replace("VERSION", ".workbench.json")]
    repo = os.environ.get("PI_UPDATE_REPO", "").strip() or DEFAULT_REPO
    branch = os.environ.get("PI_UPDATE_BRANCH", "").strip() or DEFAULT_BRANCH
    return [
        f"https://api.github.com/repos/{repo}/contents/{MANIFEST_REL}?ref={branch}",
        f"https://raw.githubusercontent.com/{repo}/{branch}/{MANIFEST_REL}",
        f"https://cdn.jsdelivr.net/gh/{repo}@{branch}/{MANIFEST_REL}",
    ]


def fetch_text(url: str, timeout: float, limit: int = 2 << 20) -> str:
    headers = {"User-Agent": "pi-workbench-update/1", "Cache-Control": "no-cache"}
    if "api.github.com" in url:
        headers["Accept"] = "application/vnd.github.raw"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"payload too large (> {limit} bytes)")
    return raw.decode("utf-8-sig", "replace")


def parse_manifest(raw: str) -> dict | None:
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return None
    files = {}
    for rel, digest in data["files"].items():
        if not isinstance(rel, str) or not isinstance(digest, str):
            continue
        parts = PurePosixPath(rel).parts
        if not parts or rel.startswith(("/", "\\")) or ".." in parts:
            continue
        if len(digest) < 16:
            continue
        files[rel] = digest.lower()
    if not files:
        return None
    data["files"] = files
    return data


def fetch_manifest(timeout: float = 6.0) -> tuple[dict | None, str, str]:
    last_err = ""
    for url in manifest_sources():
        try:
            manifest = parse_manifest(fetch_text(url, timeout))
            if manifest:
                return manifest, url, ""
            last_err = f"invalid manifest from {url}"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                last_err = f"HTTP 404：远端没有 {MANIFEST_REL}（先推送一次 manifest）"
            else:
                last_err = f"HTTP {e.code}: {e.reason} ({url})"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e} ({url})"
    return None, "", last_err


def sha_file(path: Path) -> str:
    # Line endings are not content: hash CRLF-normalised bytes so a Windows
    # checkout, a Linux clone and a copied install all agree (otherwise every
    # other machine would report content_diff for the whole tree).
    data = path.read_bytes().replace(bytes((13, 10)), bytes((10,)))
    return hashlib.sha256(data).hexdigest()

def content_diff(agent: Path, manifest: dict) -> tuple[list[str], list[str]]:
    changed: list[str] = []
    missing: list[str] = []
    for rel, expected in (manifest.get("files") or {}).items():
        p = agent / Path(*PurePosixPath(rel).parts)
        if not p.is_file():
            missing.append(rel)
            continue
        try:
            actual = sha_file(p)
        except OSError:
            missing.append(rel)
            continue
        if actual.lower() != str(expected).lower():
            changed.append(rel)
    return sorted(changed), sorted(missing)


def load_cache(agent: Path) -> dict:
    try:
        return json.loads((agent / CACHE_NAME).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def save_cache(agent: Path, data: dict) -> None:
    try:
        agent.mkdir(parents=True, exist_ok=True)
        (agent / CACHE_NAME).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        pass


def normalize(v: str) -> str:
    """1 / 1.0 / v1.0 -> 1.0.0；其他形式原样返回。"""
    m = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", v.strip())
    if not m:
        return v.strip()
    a, b, c = (x or "0" for x in m.groups())
    return f"{int(a)}.{int(b)}.{int(c)}"


def version_key(v: str):
    m = SEMVER_RE.match(v.strip())
    if m:
        nums = tuple(int(x) for x in m.group(1, 2, 3))
        pre = m.group(4)
        return (0, nums, pre)
    parts = re.split(r"[.\-_+]", v.strip().lstrip("vV"))
    return (1, tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts if p), None)


def compare(a: str, b: str) -> int:
    """a > b => 1, a < b => -1, equal => 0。"""
    a, b = normalize(a), normalize(b)
    ka, kb = version_key(a), version_key(b)
    if ka[0] == 0 and kb[0] == 0:
        if ka[1] != kb[1]:
            return 1 if ka[1] > kb[1] else -1
        pa, pb = ka[2], kb[2]
        if pa == pb:
            return 0
        if pa is None:
            return 1
        if pb is None:
            return -1
        return 1 if pa > pb else -1
    if ka == kb:
        return 0
    return 1 if str(ka) > str(kb) else -1


def check(agent: Path, force: bool, timeout: float) -> dict:
    local = local_version(agent)
    try:
        ttl = int(os.environ.get("PI_UPDATE_TTL", "") or DEFAULT_TTL)
    except ValueError:
        ttl = DEFAULT_TTL
    cache = load_cache(agent)
    t = now()
    key = source_key()
    same_source = (cache.get("source") == key) if cache.get("source") else (
        bool(cache.get("url")) and cache.get("url") == version_sources()[0]
    )
    # 缓存只在同一来源下有效：换了 PI_UPDATE_URL / 仓库分支时不能拿旧结果比较
    remote = (cache.get("remote") or None) if same_source else None
    url = (cache.get("url") or "") if same_source else ""
    cached = False
    stale = False
    error = ""
    fresh = bool(remote) and same_source and (t - int(cache.get("checked_at") or 0)) < ttl
    backoff = bool(remote) and same_source and (t - int(cache.get("failed_at") or 0)) < ERROR_TTL
    if remote and not force and (fresh or backoff):
        cached = True
        stale = backoff and not fresh
    else:
        got, got_url, error = fetch_version(timeout)
        if got:
            remote, url = got, got_url
            cache.update({"remote": got, "url": got_url, "source": key,
                          "checked_at": t, "failed_at": 0})
            save_cache(agent, cache)
        elif remote:
            stale = True
            cache["failed_at"] = t
            save_cache(agent, cache)
        else:
            cache["failed_at"] = t
            save_cache(agent, cache)

    # Content manifest: compare local agent files with the published hash list.
    manifest = None
    manifest_url = ""
    manifest_error = ""
    manifest_cached = False
    manifest_key = os.environ.get("PI_UPDATE_MANIFEST_URL", "").strip() or key
    same_manifest = cache.get("manifest_source") == manifest_key
    cached_manifest = cache.get("manifest") if same_manifest and isinstance(cache.get("manifest"), dict) else None
    manifest_at = int(cache.get("manifest_at") or 0)
    if cached_manifest and not force and (t - manifest_at) < ttl:
        manifest = cached_manifest
        manifest_url = cache.get("manifest_url") or ""
        manifest_cached = True
    else:
        got_m, got_m_url, manifest_error = fetch_manifest(timeout)
        if got_m:
            manifest = got_m
            manifest_url = got_m_url
            cache.update({"manifest": got_m, "manifest_url": got_m_url,
                          "manifest_at": t, "manifest_source": manifest_key})
            save_cache(agent, cache)
        elif cached_manifest:
            manifest = cached_manifest
            manifest_url = cache.get("manifest_url") or ""
            manifest_cached = True

    changed: list[str] = []
    missing: list[str] = []
    if manifest:
        changed, missing = content_diff(agent, manifest)
    content_count = len(changed) + len(missing)

    # A manifest may carry a newer VERSION than the cached VERSION file.
    manifest_version = str(manifest.get("version") or "").strip() if manifest else ""
    if manifest_version and (not remote or compare(manifest_version, remote) > 0):
        remote = manifest_version

    vcmp = compare(remote, local) if remote and local else None
    info = {
        "agent": str(agent),
        "local": local,
        "remote": remote,
        "url": url,
        "cached": cached,
        "stale": stale,
        "error": error,
        "checked_at": int(cache.get("checked_at") or t),
        "manifest_url": manifest_url,
        "manifest_cached": manifest_cached,
        "manifest_error": manifest_error,
        "manifest_version": manifest_version,
        "manifest_commit": str(manifest.get("commit") or "") if manifest else "",
        "content_changed": changed[:12],
        "content_missing": missing[:12],
        "content_count": content_count,
    }
    if not remote:
        info["status"] = "offline"
    elif not local:
        info["status"] = "unknown_local"
    elif vcmp is not None and vcmp > 0:
        info["status"] = "update_available"
    elif vcmp is not None and vcmp < 0:
        info["status"] = "up_to_date"
        info["local_ahead"] = True
    elif manifest is not None and content_count > 0:
        info["status"] = "content_diff"
    else:
        info["status"] = "up_to_date"
    return info


def print_human(info: dict) -> None:
    status = info["status"]
    local = info.get("local") or "(未知)"
    remote = info.get("remote") or "(取不到)"
    if status == "update_available":
        suffix = "（缓存）" if info.get("cached") else ""
        print(f"有新版本：{local} -> {remote}{suffix}")
        if info.get("content_count"):
            print(f"  另有 {info['content_count']} 个文件与远端内容不同")
        print("升级：python ~/.pi/agent/bin/workbench_update.py apply")
    elif status == "content_diff":
        print(f"与 GitHub 内容不同：本机 {local}，远端 {remote}，{info.get('content_count')} 个文件")
        for rel in (info.get("content_changed") or [])[:8]:
            print(f"  ~ {rel}")
        for rel in (info.get("content_missing") or [])[:8]:
            print(f"  - {rel} (缺失)")
        print("先看计划：python ~/.pi/agent/bin/workbench_update.py apply --dry-run")
        print("确认后再 apply；update.py 会给本地版本留 .local-<stamp> 副本。")
    elif status == "up_to_date":
        if info.get("local_ahead"):
            print(f"本机版本 {local} 比远端 {remote} 新（本地未 push？）")
        else:
            print(f"已是最新：{local}（内容一致）")
        if info.get("manifest_error"):
            print(f"  （内容清单不可用：{info['manifest_error']}）")
    elif status == "unknown_local":
        print(f"本机没有版本文件（读不到 VERSION / manifest.version），远端为 {remote}")
        print("先跑一次安装器或 scripts/update.py 打基线，之后就能比较版本。")
    else:
        err = info.get("error") or ""
        if "404" in err:
            print(f"远端仓库还没有 {VERSION_REL}（push 一次就能比较版本）")
        else:
            print("取不到远端版本（离线 / 网络受限）" + (f"：{err}" if err else ""))
        if info.get("local"):
            print(f"本机版本：{info['local']}")


# --------------------------------------------------------------------------- apply


def find_repo(agent: Path, override: str | None) -> Path | None:
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    env = os.environ.get("PI_WORKBENCH_REPO", "").strip()
    if env:
        candidates.append(Path(env))
    try:
        manifest = json.loads((agent / ".workbench.json").read_text(encoding="utf-8"))
        if manifest.get("repo"):
            candidates.append(Path(str(manifest["repo"])))
    except Exception:
        pass
    wb = os.environ.get("PI_WORKBENCH", "").strip()
    if wb:
        candidates.append(Path(wb).parent)
    candidates += [Path.home() / "pi-agent", Path.home() / "workbench" / "pi-agent"]

    for c in candidates:
        try:
            c = c.expanduser().resolve()
        except Exception:
            continue
        if (c / "scripts" / "update.py").is_file() and (c / "agent").is_dir():
            return c
    return None


def fetch_file(url: str, dest: Path, timeout: float = 60.0, attempts: int = 3) -> None:
    """带重试的下载（GitHub codeload 在部分网络下会偶发 SSL EOF）。"""
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pi-workbench-update/1"})
            with urllib.request.urlopen(req, timeout=timeout) as r, dest.open("wb") as fh:
                shutil.copyfileobj(r, fh)
            return
        except Exception as e:  # noqa: BLE001 - 重试耗尽后再抛
            last = e
            if i + 1 < attempts:
                time.sleep(1.5 * (i + 1))
    assert last is not None
    raise last


def download_repo(timeout: float = 60.0) -> tuple[Path, Path]:
    """下载 main 分支 zip 到临时目录，返回 (repo_root, tmp_dir)。调用方负责删 tmp_dir。"""
    repo = os.environ.get("PI_UPDATE_REPO", "").strip() or DEFAULT_REPO
    branch = os.environ.get("PI_UPDATE_BRANCH", "").strip() or DEFAULT_BRANCH
    urls = [
        f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}",
        f"https://github.com/{repo}/archive/refs/heads/{branch}.zip",
    ]
    tmp = Path(tempfile.mkdtemp(prefix="pi-workbench-"))
    try:
        zip_path = tmp / "repo.zip"
        errors: list[str] = []
        for url in urls:
            print(f"== 下载 {url}", flush=True)
            try:
                fetch_file(url, zip_path, timeout=timeout)
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url}: {type(e).__name__}: {e}")
        else:
            raise RuntimeError("; ".join(errors))
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp / "x")
        dirs = [p for p in (tmp / "x").iterdir() if p.is_dir()]
        if not dirs:
            raise RuntimeError("zip 解压后没有目录")
        root = dirs[0]
        if not (root / "scripts" / "update.py").is_file():
            raise RuntimeError("下载包缺少 scripts/update.py")
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return root, tmp


def apply(args: argparse.Namespace) -> int:
    agent = agent_dir()
    hold = agent / ".update-hold"
    if hold.exists() and not args.ignore_hold:
        print(f"本机有 {hold.name}，跳过更新：{hold}", file=sys.stderr)
        reason = read_text(hold)
        if reason:
            print(reason, file=sys.stderr)
        print("确认要更新就删掉这个文件，或加 --ignore-hold。", file=sys.stderr)
        return 1
    repo = find_repo(agent, args.repo)
    tmp_dir: Path | None = None
    if repo is None:
        try:
            repo, tmp_dir = download_repo()
        except Exception as e:  # noqa: BLE001
            print(f"找不到本地 checkout，下载也失败：{type(e).__name__}: {e}", file=sys.stderr)
            return 2
        print(f"== 使用临时 checkout {repo}", flush=True)
    else:
        print(f"== workbench repo: {repo}", flush=True)
        if not args.dry_run and not args.no_pull and (repo / ".git").exists():
            try:
                p = subprocess.run(
                    ["git", "-C", str(repo), "pull", "--ff-only", "--quiet"],
                    capture_output=True, timeout=60,
                )
                line = (p.stdout or b"").decode("utf-8", "replace").strip().splitlines()
                print("== git pull: " + (line[0] if line else "ok"), flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"== ! git pull 失败，用当前 checkout：{e}", flush=True)

    repo_ver = ""
    try:
        repo_ver = (repo / "agent" / "VERSION").read_text(encoding="utf-8-sig").splitlines()[0].strip()
    except Exception:
        pass
    local_ver = local_version(agent)
    if repo_ver and local_ver and not args.allow_downgrade and compare(repo_ver, local_ver) < 0:
        print(
            f"拒绝降级：仓库版本 {repo_ver} < 本机 {local_ver}。"
            "如果确实要回滚，加 --allow-downgrade。",
            file=sys.stderr,
        )
        return 1

    cmd = [sys.executable, str(repo / "scripts" / "update.py"),
           "--agent-dir", str(agent), "--repo", str(repo)]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.yes:
        cmd.append("--yes")
    if args.keep_local:
        cmd.append("--keep-local")
    if args.prune:
        cmd.append("--prune")
    print("== " + " ".join(cmd), flush=True)
    try:
        rc = subprocess.run(cmd).returncode
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    if rc == 0 and not args.dry_run:
        v = local_version(agent)
        print(f"\n本机版本：{v or '(未知)'}", flush=True)
        print("重启 pi 或执行 /reload 让扩展生效。", flush=True)
    return rc


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="检查是否有新版本")
    p_check.add_argument("--json", action="store_true")
    p_check.add_argument("--force", action="store_true", help="忽略缓存，强制联网")
    p_check.add_argument("--timeout", type=float, default=6.0)
    p_check.add_argument("--agent-dir", default="")

    p_status = sub.add_parser("status", help="只看本地版本和缓存（不联网）")
    p_status.add_argument("--agent-dir", default="")

    p_apply = sub.add_parser("apply", help="下载并安装新版本")
    p_apply.add_argument("--agent-dir", default="", help="安装目录（默认 ~/.pi/agent）")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--yes", "-y", action="store_true")
    p_apply.add_argument("--keep-local", action="store_true")
    p_apply.add_argument("--prune", action="store_true")
    p_apply.add_argument("--no-pull", action="store_true")
    p_apply.add_argument("--allow-downgrade", action="store_true", help="允许仓库版本低于本机时回滚")
    p_apply.add_argument("--ignore-hold", action="store_true", help="忽略 .update-hold 保护标记")
    p_apply.add_argument("--repo", default="")

    args = ap.parse_args()
    if getattr(args, "agent_dir", ""):
        os.environ["PI_AGENT_DIR"] = args.agent_dir

    if args.cmd == "apply":
        return apply(args)

    agent = agent_dir()
    if args.cmd == "status":
        cache = load_cache(agent)
        info = {
            "agent": str(agent),
            "local": local_version(agent),
            "remote": cache.get("remote"),
            "url": cache.get("url"),
            "checked_at": cache.get("checked_at"),
            "status": "cached",
        }
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    info = check(agent, force=bool(args.force), timeout=float(args.timeout))
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print_human(info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
