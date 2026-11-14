# -*- coding: utf-8 -*-
"""本地带密码启动：不依赖 cloudflared，配合任意内网穿透工具使用。

流程：
  1. 自动挑空闲端口启动服务，并强制开启用户名/密码
  2. 打印「端口 + 账号 + 密码」，窗口保持不关
  3. 你自己用现成的穿透工具把 127.0.0.1:端口 映射到公网：
        - cpolar    : cpolar http 8000
        - natapp    : natapp -authtoken=xxxx
        - 花生壳     : 图形界面里配置内网主机 127.0.0.1、端口 8000
        - cloudflared: cloudflared tunnel --url http://127.0.0.1:8000
  4. 穿透工具给出的 https 地址 + 这里的账号密码，就是发给评委的内容
"""
from __future__ import annotations

import os
import random
import socket
import sys
import time
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
LINE = "=" * 68


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


def main() -> int:
    port = free_port()
    user = os.environ.get("APP_USER") or "judge"
    pwd = os.environ.get("APP_PASSWORD") or ("jfz" + str(random.randint(1000, 9999)))
    os.environ["APP_USER"] = user
    os.environ["APP_PASSWORD"] = pwd

    print(LINE)
    print("  启动本地服务（已开启访问密码）")
    print(LINE)
    print()
    print("  本机地址：  http://127.0.0.1:%d/" % port)
    print("  穿透端口：  %d          ← 下面工具的端口就填这个" % port)
    print("  用户名：    %s" % user)
    print("  密码：      %s" % pwd)
    print()
    print(LINE)
    print("  下一步：用穿透工具把这个端口映射到公网")
    print(LINE)
    print()
    print("  已装 cpolar（推荐，国内可用）： 在另一个窗口执行")
    print("      cpolar http %d" % port)
    print("  它会给出一个 https://xxxx.cpolar.top 地址，")
    print("  把那个地址 + 上面的用户名密码一起发给评委。")
    print()
    print("  已装 natapp：")
    print("      natapp -authtoken=你的token   （先在 natapp.cn 建一条 %d 端口隧道）" % port)
    print()
    print("  已装 花生壳：图形界面 → 内网穿透 → 内网主机 127.0.0.1、端口 %d" % port)
    print()
    print("  已装 cloudflared：")
    print("      cloudflared tunnel --url http://127.0.0.1:%d" % port)
    print()
    print(LINE)
    print("  本窗口不要关 = 服务不停；关掉即停止。")
    print(LINE)
    print()

    try:
        (BASE / "_本机端口与密码.txt").write_text(
            "端口：%d\n用户名：%s\n密码：%s\n" % (port, user, pwd), encoding="utf-8")
    except Exception:
        pass

    if not os.environ.get("NO_BROWSER"):
        time.sleep(1.0)
        try:
            webbrowser.open("http://127.0.0.1:%d/" % port)
        except Exception:
            pass

    from app.server import serve

    serve("127.0.0.1", port)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已停止")
    except Exception as e:
        print("\n启动失败：%s: %s" % (type(e).__name__, e))
        input("按回车关闭窗口…")
