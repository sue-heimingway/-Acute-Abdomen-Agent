# -*- coding: utf-8 -*-
"""评审版启动器：自动选空闲端口 → 打印地址 → 打开浏览器 → 启动服务。

由「①请双击我启动.bat」调用；也可直接：python start_here.py
"""
from __future__ import annotations

import socket
import sys
import threading
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

LINE = "=" * 64


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
    url = "http://127.0.0.1:%d/" % port
    try:
        (BASE / "_本次访问地址.txt").write_text(url + "\n", encoding="utf-8")
    except Exception:
        pass

    print(LINE)
    print("  急腹症体征核查 Agent  ·  评审试用版")
    print(LINE)
    print()
    print("  请在浏览器打开：  %s" % url)
    print()
    print("  这个窗口不要关，它就是服务本身；关掉窗口 = 停止服务。")
    print(LINE)
    print()

    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    from app.server import serve

    serve("127.0.0.1", port)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已停止")
    except OSError as e:
        print()
        print("启动失败：端口被占用或权限不足 —— %s" % e)
        input("按回车关闭窗口…")
    except Exception as e:  # 兜底，避免窗口一闪而过
        print()
        print("启动失败：%s: %s" % (type(e).__name__, e))
        print("请把本窗口截图发给团队联系人。")
        input("按回车关闭窗口…")
