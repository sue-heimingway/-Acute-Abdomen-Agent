"""命令行演示：跑通 4 个病例，打印核查报告与补记草稿。

python demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app import config  # noqa: E402
from app.agents import Coordinator  # noqa: E402

LEVEL_TAG = {"red": "[红]", "yellow": "[黄]", "grey": "[灰]"}


def main() -> None:
    cases = config.load_json("cases.json")
    agent = Coordinator()
    print(f"引擎: {agent.llm.name} | 规则版本: {agent.engine.version}\n" + "=" * 78)

    for case in cases:
        report = agent.analyze(case["record"], case["patient"])
        summary = report["summary"]
        print(f"\n【{case['title']}】 {case['id']}")
        print(f"  初步诊断: {'、'.join(report['structured']['primary_dx']) or '未识别'}")
        ddx = "、".join(c["disease"] for c in report["ddx"]["candidates"][:5])
        print(f"  鉴别诊断候选({report['ddx']['count']}): {ddx or '-'}")
        print(
            f"  结果: 红 {summary['red']} / 黄 {summary['yellow']} / 灰 {summary['grey']}，"
            f"已核查 {summary['checked']} 项，触发规则 {summary['rules_triggered']} 条"
        )
        for f in report["findings"]:
            print(f"  {LEVEL_TAG[f['level']]} {f['name']}（{f['status_label']}）→ 鉴别: {f['ddx']}")
            print(f"      问: {f['question']}")
            print(f"      依据: {f['source']}")

        reds = [f for f in report["findings"] if f["level"] == "red"]
        if reds:
            age = case["patient"].get("age") or 0
            reason = "患儿哭闹不配合" if age < 14 else "患者拒绝检查"
            doc = agent.draft_documentation(reds[0]["rule_id"], reason, case["patient"], reds[0].get("missing_parts"))
            print(f"  ↳ 忽略草稿（{reds[0]['name']}）: {doc}")
        print("-" * 78)

    print("\n演示结束。启动 Web 界面: python run.py")


if __name__ == "__main__":
    main()
