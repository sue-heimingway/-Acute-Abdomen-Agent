"""演示集评测：红框召回率与打扰度。

python evaluate.py

说明：只有 4 个演示病例，指标用于验证链路与展示口径，不代表临床性能；
正式评测需扩充到 50–100 例并由医生双盲标注“必须记录项”。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app import config  # noqa: E402
from app.agents import Coordinator  # noqa: E402
from app.llm import LLMClient  # noqa: E402


def main() -> None:
    agent = Coordinator(LLMClient(api_key=""))
    cases = config.load_json("cases.json")
    rows = []
    hit = total = 0
    for case in cases:
        report = agent.analyze(case["record"], case["patient"])
        found = {f["rule_id"] for f in report["findings"] if f["level"] == "red"}
        expected = set(case.get("expected_red", []))
        hit += len(found & expected)
        total += len(expected)
        rows.append(
            (
                case["id"],
                len(expected),
                len(found),
                len(found & expected),
                report["summary"]["alert_total"],
                report["summary"]["checked"],
            )
        )

    print(f"{'病例':<9}{'应报':>4}{'实报红':>6}{'命中':>4}{'总提示':>6}{'已核查':>6}")
    for r in rows:
        print(f"{r[0]:<9}{r[1]:>4}{r[2]:>6}{r[3]:>4}{r[4]:>6}{r[5]:>6}")
    recall = hit / total if total else 0.0
    burden = sum(r[4] for r in rows) / len(rows)
    print("-" * 42)
    print(f"红框召回率: {recall:.0%}（{hit}/{total}）")
    print(f"平均提示条数/例: {burden:.1f}（含弱提示与灰项）")
    print("注：演示集样本量小，仅用于链路验证。")


if __name__ == "__main__":
    main()
