#!/usr/bin/env python3
"""A Linux VM the workbench brings with it — no VMware/VirtualBox/WSL/Docker needed.

    python scripts/portable-vm.py check          # 环境/磁盘/组件是否齐
    python scripts/portable-vm.py setup --yes    # 下载 QEMU + Ubuntu cloud image（约 1.5 GB）
    python scripts/portable-vm.py start          # 后台启动，等 SSH 就绪
    python scripts/portable-vm.py ssh            # ssh 进去（端口 2222）
    python scripts/portable-vm.py status
    python scripts/portable-vm.py stop

启动后就能给 setup-tools 用：

    python scripts/setup-tools.py --target ubuntu --ssh-host 127.0.0.1 --ssh-port 2222 --ssh-user pi

原理
----
* QEMU 用户态网络把 guest 的 22 转发到 127.0.0.1:2222。
* cloud-init 的 NoCloud 数据源支持从 SMBIOS 序列号读取
  `ds=nocloud;s=http://10.0.2.2:PORT/`，所以**不需要生成 seed.iso**：
  宿主机起一个只读小 HTTP 服务，guest 启动时自己来拉 user-data/meta-data。
* 账号 pi / 密码 pi（可用 --user/--password 改），并注入 ~/.ssh/id_*.pub。

注意
----
* QEMU 在 Windows 上若没有 WHPX（需要管理员开 Hyper-V 平台），是纯软件模拟，
  性能大约是原生的 1/5–1/10；跑 nmap/脚本没问题，编译和大型二进制分析会明显慢。
* 这台机器第一次跑要下载约 1.5 GB，请确认带宽和磁盘（需要 ≥8 GB 空闲）。
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import platform
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = Path(os.environ.get("PI_VM_DIR") or (Path.home() / ".pi" / "vm"))
IMAGE_NAME = "noble-server-cloudimg-amd64.img"
# 官方源在国内常常只有 0.4 MB/s；镜像站 8 MB/s（实测）
MIRRORS = [
    "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cloud-images/noble/current",
    "https://mirrors.ustc.edu.cn/ubuntu-cloud-images/noble/current",
    "https://cloud-images.ubuntu.com/noble/current",
]
IMAGE_URL = f"{MIRRORS[-1]}/{IMAGE_NAME}"      # 仅作展示
SUMS_URL = f"{MIRRORS[-1]}/SHA256SUMS"
DISK_MIN_GB = 8
SSH_PORT = 2222
HTTP_PORT = 8000
IS_WIN = sys.platform.startswith("win")


def say(msg: str) -> None:
    print(msg, flush=True)


def sh(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace")
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def find_qemu() -> str:
    for name in ("qemu-system-x86_64", "qemu-system-x86_64.exe"):
        p = shutil.which(name)
        if p:
            return p
    for base in (Path(r"C:\Program Files\qemu"), Path(r"C:\Program Files (x86)\qemu"),
                 Path.home() / "qemu", WORK / "qemu"):
        cand = base / ("qemu-system-x86_64.exe" if IS_WIN else "qemu-system-x86_64")
        if cand.exists():
            return str(cand)
    return ""


def best_accel(qemu: str) -> str:
    """whpx (Hyper-V) is ~5-10x faster than tcg; fall back to tcg."""
    if not IS_WIN:
        return ""
    rc, out = sh([qemu, "-accel", "help"], timeout=30)
    return "whpx" if "whpx" in out else "tcg"


def ssh_key() -> str:
    for name in ("id_ed25519.pub", "id_rsa.pub"):
        p = Path.home() / ".ssh" / name
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return ""


def free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(str(path if path.exists() else path.parent)).free / 1e9
    except OSError:
        return -1.0


# --------------------------------------------------------------------- cloud-init
def cloud_init_files(user: str, password: str) -> tuple[str, str]:
    key = ssh_key()
    ssh_block = f"    - {key}\n" if key else ""
    user_data = f"""#cloud-config
hostname: pi-vm
users:
  - name: {user}
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
    plain_text_passwd: {password}
    ssh_authorized_keys:
{ssh_block or "    []\n"}ssh_pwauth: true
package_update: false
runcmd:
  - [ systemctl, enable, --now, ssh ]
"""
    meta_data = f"instance-id: pi-vm-001\nlocal-hostname: pi-vm\n"
    return user_data, meta_data


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # 安静
        pass


def start_http(directory: Path, port: int) -> subprocess.Popen:
    """Detached server: it must outlive this command (cloud-init fetches later)."""
    argv = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
            "--directory", str(directory)]
    log = (WORK / "http.log").open("ab")
    proc = subprocess.Popen(argv, stdout=log, stderr=log, start_new_session=True)
    (WORK / "http.pid").write_text(str(proc.pid), encoding="utf-8")
    return proc


def wait_http(port: int, timeout: float = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/user-data", timeout=3) as r:
                return r.status == 200
        except Exception:
            time.sleep(0.5)
    return False


# --------------------------------------------------------------------- setup
def download(url: str, dest: Path, expect_sha: str = "") -> bool:
    say(f"== 下载 {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, tmp.open("wb") as fh:
            total = int(r.headers.get("content-length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r   {done/1e6:7.1f}/{total/1e6:.1f} MB ({pct}%)", end="", flush=True)
        print()
    except Exception as exc:
        print(f"\n   下载失败：{exc}")
        tmp.unlink(missing_ok=True)
        return False
    if expect_sha:
        h = hashlib.sha256()
        with tmp.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != expect_sha:
            print(f"   ! sha256 不匹配：{h.hexdigest()[:16]}… != {expect_sha[:16]}…")
            tmp.unlink(missing_ok=True)
            return False
        say("   sha256 ok")
    tmp.replace(dest)
    return True


def sha_for_image() -> str:
    for base in MIRRORS:
        try:
            with urllib.request.urlopen(f"{base}/SHA256SUMS", timeout=30) as r:
                for line in r.read().decode().splitlines():
                    if line.strip().endswith(IMAGE_NAME):
                        return line.split()[0]
        except Exception:
            continue
    return ""


def download_any(dest: Path, sha: str) -> bool:
    for base in MIRRORS:
        if download(f"{base}/{IMAGE_NAME}", dest, sha):
            return True
        print("   换下一个镜像")
    return False


def cmd_check(_args) -> int:
    ok = True
    qemu = find_qemu()
    say(f"{'OK ' if qemu else '-- '} qemu        {qemu or '未安装（setup 会装）'}")
    img = WORK / IMAGE_NAME
    say(f"{'OK ' if img.exists() else '-- '} 镜像        {img if img.exists() else '未下载（约 600 MB）'}")
    free = free_gb(WORK)
    say(f"{'OK ' if free < 0 or free >= DISK_MIN_GB else '-- '} 磁盘        {free:.1f} GB 可用（需要 {DISK_MIN_GB} GB）")
    ok &= bool(qemu) and img.exists() and (free < 0 or free >= DISK_MIN_GB)
    return 0 if ok else 1


def install_qemu(dry: bool) -> bool:
    if find_qemu():
        say("== qemu 已存在")
        return True
    if shutil.which("winget"):
        argv = ["winget", "install", "-e", "--id", "SoftwareFreedomConservancy.QEMU",
                "--accept-package-agreements", "--accept-source-agreements"]
        say("== 装 QEMU：" + " ".join(argv))
        say("   （winget 安装 QEMU 通常需要一次 UAC 确认）")
        if dry:
            return True
        rc, _ = sh(cmd=argv, timeout=3600)
        return rc == 0 and bool(find_qemu())
    say("   ! 没有 winget：请手动安装 QEMU（https://qemu.weilnetz.de/w64/）")
    return False


def cmd_setup(args) -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    free = free_gb(WORK)
    if free >= 0 and free < DISK_MIN_GB:
        say(f"磁盘只剩 {free:.1f} GB，至少需要 {DISK_MIN_GB} GB")
        return 1
    if not install_qemu(args.dry_run):
        return 1
    img = WORK / IMAGE_NAME
    if not img.exists():
        if args.dry_run:
            say(f"[dry-run] 会下载 {IMAGE_URL} -> {img}")
        else:
            sha = sha_for_image()
            say(f"== 期望 sha256：{sha[:16]}…" if sha else "== 没拿到 SHA256SUMS，跳过校验")
            if not download_any(img, sha):
                return 1
    else:
        say(f"== 镜像已存在：{img}")
    say(f"\n完成。下一步： python scripts/portable-vm.py start")
    return 0


# --------------------------------------------------------------------- run
def cmd_start(args) -> int:
    qemu = find_qemu()
    img = WORK / IMAGE_NAME
    if not qemu:
        say("qemu 未安装：先跑 setup")
        return 1
    if not img.exists():
        say("镜像未下载：先跑 setup")
        return 1

    WORK.mkdir(parents=True, exist_ok=True)
    user_data, meta_data = cloud_init_files(args.user, args.password)
    seed = WORK / "seed"
    seed.mkdir(exist_ok=True)
    (seed / "user-data").write_text(user_data, encoding="utf-8")
    (seed / "meta-data").write_text(meta_data, encoding="utf-8")
    # cloud-init 会反复取 vendor-data；404 会触发退避重试，拖慢启动
    (seed / "vendor-data").write_text("", encoding="utf-8")

    httpd = start_http(seed, args.http_port)
    if not wait_http(args.http_port):
        say("! seed HTTP 没起来，看 " + str(WORK / "http.log"))
        return 1
    say(f"== seed HTTP 就绪: http://127.0.0.1:{args.http_port}/ （guest 通过 10.0.2.2 访问）")

    disk = WORK / "disk.qcow2"
    if not disk.exists():
        # 用覆盖层，保留原始镜像可复用
        rc, out = sh([qemu.replace("qemu-system-x86_64", "qemu-img"), "create", "-f", "qcow2",
                      "-F", "qcow2", "-b", str(img), str(disk)], timeout=120)
        say("== 创建覆盖盘" + ("" if rc == 0 else f"（失败：{out.strip()[:120]}）"))
        if rc != 0:
            return 1

    accel = args.accel or best_accel(qemu)
    if accel:
        say(f"== 加速器：{accel}" + ("（WHVP 硬件加速）" if accel == "whpx" else "（软件模拟，慢 5-10 倍）"))
    argv = [
        qemu, *([f"-accel", accel] if accel else []),
        "-m", str(args.mem), "-smp", str(args.cpus),
        "-drive", f"file={disk},if=virtio,format=qcow2",
        "-netdev", f"user,id=n0,hostfwd=tcp:127.0.0.1:{args.ssh_port}-:22",
        "-device", "virtio-net-pci,netdev=n0",
        "-smbios", f"type=1,serial=ds=nocloud;s=http://10.0.2.2:{args.http_port}/",
        "-display", "none",
        "-serial", f"file:{WORK / 'console.log'}",
    ]
    say("== QEMU：" + " ".join(argv))
    if args.dry_run:
        say("[dry-run] 不启动")
        httpd.terminate()
        return 0

    log = (WORK / "qemu.log").open("ab")
    proc = subprocess.Popen(argv, stdout=log, stderr=log)
    (WORK / "pid").write_text(str(proc.pid), encoding="utf-8")
    say(f"== 已启动 pid={proc.pid}，等 SSH（最多 {args.wait}s）")

    deadline = time.time() + args.wait
    ready = False
    while time.time() < deadline:
        # slirp 会立刻接受 TCP，所以必须做真正的 SSH 握手才叫就绪
        rc, _ = sh(["ssh", "-p", str(args.ssh_port), "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=5",
                    f"{args.user}@127.0.0.1", "true"], timeout=25)
        if rc == 0:
            ready = True
            break
        time.sleep(3)

    if ready:
        say(f"\n== SSH 就绪： ssh -p {args.ssh_port} {args.user}@127.0.0.1")
        say(f"   或： python scripts/setup-tools.py --target ubuntu "
            f"--ssh-host 127.0.0.1 --ssh-port {args.ssh_port} --ssh-user {args.user}")
        (WORK / "vm.env").write_text(
            f"PI_VM_HOST=127.0.0.1\nPI_VM_USER={args.user}\nPI_VM_PORT={args.ssh_port}\n",
            encoding="utf-8")
        return 0
    say("超时：看 " + str(WORK / "console.log") + " / qemu.log")
    return 1


def cmd_stop(_args) -> int:
    httpf = WORK / "http.pid"
    if httpf.exists():
        try:
            os.kill(int(httpf.read_text().strip() or 0), signal.SIGTERM)
            say("已停止 seed HTTP")
        except (OSError, ValueError):
            pass
        httpf.unlink(missing_ok=True)
    pidf = WORK / "pid"
    if not pidf.exists():
        say("没有 pid 文件（可能没在跑）")
        return 0
    pid = int(pidf.read_text().strip() or 0)
    try:
        os.kill(pid, signal.SIGTERM)
        say(f"已停止 pid={pid}")
    except OSError as exc:
        say(f"停止失败：{exc}")
    pidf.unlink(missing_ok=True)
    return 0


def cmd_status(_args) -> int:
    pidf = WORK / "pid"
    if not pidf.exists():
        say("未运行")
        return 1
    pid = int(pidf.read_text().strip() or 0)
    alive = False
    try:
        os.kill(pid, 0)
        alive = True
    except OSError:
        alive = False
    try:
        with socket.create_connection(("127.0.0.1", SSH_PORT), timeout=2):
            ssh_ok = True
    except OSError:
        ssh_ok = False
    say(f"pid={pid} {'running' if alive else 'dead'} | ssh {'ok' if ssh_ok else 'not ready'}")
    return 0 if alive else 1


def cmd_ssh(args) -> int:
    user = args.user
    port = args.ssh_port
    return subprocess.call(["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=accept-new",
                            f"{user}@127.0.0.1"] + (args.rest or []))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("check", "setup", "start", "stop", "status", "ssh"):
        p = sub.add_parser(name)
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--user", default="pi")
        p.add_argument("--password", default="pi")
        p.add_argument("--mem", type=int, default=2048)
        p.add_argument("--cpus", type=int, default=2)
        p.add_argument("--ssh-port", type=int, default=SSH_PORT)
        p.add_argument("--http-port", type=int, default=HTTP_PORT)
        p.add_argument("--wait", type=int, default=300)
        p.add_argument("--accel", default="", choices=["", "whpx", "tcg", "kvm"])
        p.add_argument("--yes", "-y", action="store_true")
        if name == "ssh":
            p.add_argument("rest", nargs="*")

    args = ap.parse_args()
    return {
        "check": cmd_check,
        "setup": cmd_setup,
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "ssh": cmd_ssh,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
