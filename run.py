"""启动入口：python run.py --port 8000"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.server import serve  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="急性腹痛体征核查 Agent")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
