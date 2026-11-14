# -*- coding: utf-8 -*-
"""公网发布器：本机启动服务 + Cloudflare 临时隧道 + 应用级密码。

用法（由「3_公网发布.bat」调用，也可：python publish_public.py）
  1. 自动挑一个空闲端口启动本服务
  2. 自动生成 6 位访问密码（或读环境变量 APP_PASSWORD）
  3. 调用同目录的 cloudflared.exe 建立 https 隧道
  4. 打印公网地址 + 用户名 + 密码，并写入 _公网地址.txt

安全说明：临时隧道本身没有访问控制，所以必须靠应用自带的 HTTP Basic 认证
（APP_USER / APP_PASSWORD），否则等于把病历系统裸奔到公网。
"""
from __future__ import annotations

import os
import random
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
LINE = "=" * 68
CF_PATTERN = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")


def free_port(start: int = 8000, tries: int = 30) -> int:
    for p in range(start, start + tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    return start


def wait_port(port: int, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.6)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except OSError:
            s.close()
            time.sleep(0.2)
    return False


def find_cloudflared() -> str | None:
    cand = [BASE / "cloudflared.exe", BASE / "cloudflared"]
    for c in cand:
        if c.exists():
            return str(c)
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d) / "cloudflared.exe"
        if p.exists():
            return str(p)
    return None


def main() -> int:
    port = free_port()
    user = os.environ.get("APP_USER") or "judge"
    pwd = os.environ.get("APP_PASSWORD") or ("jfz" + str(random.randint(1000, 9999)))
    os.environ["APP_USER"] = user
    os.environ["APP_PASSWORD"] = pwd

    exe = find_cloudflared()
    if not exe:
        print(LINE)
        print("  没有找到 cloudflared.exe")
        print(LINE)
        print()
        print("  请下载后放到本文件夹（与本文件同一目录）：")
        print("    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe")
        print("  下载后重命名为：  cloudflared.exe")
        print()
        print("  只需要一次。放好后重新双击 3_公网发布.bat。")
        print()
        input("按回车关闭…")
        return 1

    print(LINE)
    print("  正在启动本地服务（端口 %d）…" % port)
    print(LINE)

    from app.server import serve  # 读取刚才设置的 APP_USER / APP_PASSWORD

    t = threading.Thread(target=lambda: serve("127.0.0.1", port), daemon=True)
    t.start()
    if not wait_port(port):
        print("本地服务启动失败：端口 %d 无响应" % port)
        input("按回车关闭…")
        return 1

    print("  本地已就绪，正在建立公网隧道（约 5~15 秒）…")
    proc = subprocess.Popen(
        [exe, "tunnel", "--url", "http://127.0.0.1:%d" % port, "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    url = None
    deadline = time.time() + 60
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        m = CF_PATTERN.search(line)
        if m:
            url = m.group(0)
            break

    if not url:
        print()
        print("隧道建立失败（60 秒内没拿到地址）。常见原因：")
        print("  1. 公司/校园网屏蔽了外网 7844 端口 → 换手机热点再试")
        print("  2. cloudflared.exe 被杀毒软件拦 → 放行后重试")
        print("  3. 代理软件冲突 → 临时关闭代理")
        print()
        print("如需长期可用，建议部署到服务器（见 deploy/ 目录）。")
        proc.terminate()
        input("按回车关闭…")
        return 1

    (BASE / "_公网地址.txt").write_text(
        "公网地址：%s\n用户名：%s\n密码：%s\n" % (url, user, pwd), encoding="utf-8"
    )

    print()
    print(LINE)
    print("  公网访问地址（发给评委的就是这个）")
    print()
    print("      %s" % url)
    print()
    print("  用户名： %s" % user)
    print("  密码：   %s" % pwd)
    print(LINE)
    print()
    print("  已写入 _公网地址.txt，可直接截图发群。")
    print("  关闭本窗口 = 链接立即失效；评审结束后请务必关掉。")
    print()

    try:
        for line in proc.stdout:  # 继续排空输出，避免管道阻塞
            pass
    except KeyboardInterrupt:
        pass
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        print("\n公网隧道已关闭。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已停止")
    except Exception as e:  # 兜底，避免窗口一闪而过
        print("\n发布失败：%s: %s" % (type(e).__name__, e))
        input("按回车关闭窗口…")
