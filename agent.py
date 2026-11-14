#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
急腹症病历核查 · AI 智能体  agent.py
=========================================================
把 jifuzheng.py 的规则引擎封装成一个能对话、能动手的智能体。

它具备 7 个工具（skills）：
    check        核查整份病历，出 12 段报告
    gaps         只列最该补的（红线 / 强提醒 / 待补充）
    draft        已下线（不做鉴别推理）
    explain      解释某条规则为什么存在
    rules        列出全部规则 ID
    ignore       忽略某项并生成免责补金句
    batch        批量核查一个文件夹

三种用法：
    1) 网页版（推荐）      python agent.py            → 自动打开 http://127.0.0.1:8765
    2) 命令行对话          python agent.py --cli
    3) 一次性核查          python agent.py 病例.txt

可选大模型（不配置也能用，配置后自由问答更自然）：
    set DEEPSEEK_API_KEY=sk-xxxx        （或 OPENAI_API_KEY）
    set LLM_MODEL=deepseek-chat         （可省略）

依赖：同目录的 jifuzheng.py + Python 标准库，无需 pip 安装。
"""

import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request as urlrequest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("JZ_GLYPHS", "fancy")  # 网页用 UTF-8，可以显示 █ ✔ ▶

try:
    import jifuzheng as JZ
except Exception as exc:  # 找不到同目录的引擎
    print("找不到同目录的 jifuzheng.py，请把两个文件放在一起。\n" + str(exc))
    raise SystemExit(1)

VERSION = "1.0"
PORT = int(os.environ.get("AGENT_PORT", "8765"))

# =====================================================================
# 一、工具层：把规则引擎包成智能体可调用的技能
# =====================================================================


def tool_check(text):
    """核查整份病历，返回结构化结果。"""
    ctx = JZ.analyze(text)
    rules = ctx["rules"]
    return {
        "ctx": ctx,
        "report": JZ.report(ctx),
        "red": [r for r in rules if r["lv"] == "red"],
        "yellow": [r for r in rules if r["lv"] == "yellow"],
        "main": [JZ.KB[k]["name"] if k in JZ.KB else k.replace("more:", "") for k in ctx["main"]],
        "diff": [JZ.KB[k]["name"] if k in JZ.KB else k.replace("more:", "") for k in ctx["diff"]],
        "framework": JZ.framework(ctx),
        "more": ctx["more"],
        "baseline": ctx["baseline"],
        "redlist": ctx["red"],
    }


def tool_gaps(text, with_yellow=True):
    """只返回最该补的项：强提醒 + 红线缺项 + 查体底线 + 核心缺失 + 弱提示。"""
    if not (text or "").strip():
        return []
    r = tool_check(text)
    ctx = r["ctx"]
    out = []
    for x in r["red"]:
        out.append({"级": "强提醒", "内容": x["title"], "怎么补": x["fix"]})
    for x in r["redlist"]:
        if x["missing"]:
            out.append({"级": "红线", "内容": x["label"] + " 缺 " + "、".join(x["missing"][:4]),
                        "怎么补": "按缺项补齐检查并写明排除依据"})
    for x in r["baseline"]:
        if not x["ok"] and not x["exempt"]:
            out.append({"级": "查体底线", "内容": x["label"], "怎么补": "补记查体；确有困难写明原因"})
    for k in JZ.CORE_KEYS:
        res = ctx["res"][k]
        if k in ctx["main"] or JZ.mentions(ctx, k):
            for row in res["rows"]:
                if row["st"] == "missing" and row["pt"]["core"] and row["pt"]["hint"]:
                    out.append({"级": "要点", "内容": res["d"]["name"] + "：" + row["pt"]["label"],
                                "怎么补": row["pt"]["hint"]})
    if with_yellow:
        for x in r["yellow"]:
            if x.get("id") == "yellow-baseline":
                continue  # 查体底线上面已按项列出，避免重复
            out.append({"级": "弱提示", "内容": x["title"], "怎么补": x["fix"]})
    return out


def tool_causes(text):
    """只输出【零】病因 / 并发症分级预警这一栏。"""
    if not (text or "").strip():
        return "还没有载入病历。"
    ctx = JZ.analyze(text)
    blk = JZ.causes_block(ctx)
    if not blk:
        return ("本例未命中六大急腹症，没有需要预警的病因/并发症。\n"
                "（产品定位：不像急腹症就静默退出，不打扰医生）")
    return "\n".join(blk)


def tool_yellow(text):
    """只列黄色弱提示（病历质控项）。"""
    if not (text or "").strip():
        return "还没有载入病历。"
    r = tool_check(text)
    if not r["yellow"]:
        return "本例没有黄色弱提示（病历质控项都齐了）。"
    lines = ["【弱提示 / 病历质控】共 %d 条" % len(r["yellow"]), ""]
    for i, x in enumerate(r["yellow"], 1):
        lines.append("%d. %s" % (i, x["title"]))
        lines.append("   %s" % x["fix"])
    lines.append("")
    lines.append("这些不影响急诊决策，但属于病历质控扣分项；已写入的病历不会再提示。")
    return "\n".join(lines)


def tool_draft(text):
    """已下线：生成鉴别诊断等于替医生做诊断推理（越界）。保留占位以兼容旧调用。"""
    return ("【已下线】本系统只做病历质控（防弹衣），不做鉴别诊断推理（指南针）。\n"
            "请用 /gaps 看「还缺什么记录」，用 /causes 看该问什么、该记什么。")


RULE_DOC = {
    "red-hcg": "只有病历出现明确停经史，或诊断/鉴别已提到宫外孕时，才要求 hCG；单纯育龄女性腹痛不打扰。",
    "red-pediatric-hernia": "小儿腹痛漏查腹股沟→嵌顿疝→肠坏死，是经典医疗纠纷场景。",
    "red-ectopic-mgmt": "宫外孕破裂是育龄女性急腹症第一死因；确诊后不记录血流动力学与手术决策，恶化时无法追溯。",
    "red-sepsis-bundle": "脓毒症 1 小时集束化（乳酸、用药前血培养、1 小时内抗生素）每延迟 1 小时病死率上升。",
    "red-septic-shock": "感染性休克需 30 分钟内 30 mL/kg 晶体复苏，必要时去甲肾上腺素，MAP 目标≥65。",
    "red-strangulation": "肠梗阻出现持续剧痛/发热/WBC↑/酸中毒提示绞窄，延误即肠坏死。",
    "red-perforation-imaging": "膈下游离气体是穿孔确诊关键（阳性率 50%~75%，阴性不能排除）。",
    "red-redlist": "图四 13 项红线病因：涉及就必须留下关键检查记录，否则无法证明已排除。",
    "red-torsion-exam": "睾丸扭转以腹痛/阴囊痛就诊，6 小时是睾丸存活窗；提睾反射、Prehn 征、超声血流是决定急诊手术的关键。",
    "yellow-baseline": "图五通用底线：腹部四诊、双侧腹股沟、阴囊、妇科、心肺、直肠指检，缺项即遗漏。",
    "yellow-diff-count": "图二：鉴别写 3~4 个最需要排除的，多了分散重点、少了漏致命病。",
    "yellow-diff-basis": "图二：只罗列病名、不写支持点/不支持点，属不合格的鉴别诊断。",
}


def tool_explain(rule_id):
    rule_id = (rule_id or "").strip()
    if rule_id in RULE_DOC:
        return "【%s】%s" % (rule_id, RULE_DOC[rule_id])
    if rule_id in {r[0] for r in JZ.RED_LIST}:
        return "【%s】属于图四红线病因，涉及即须核对关键检查记录。" % rule_id
    return "没有找到规则 %s。可用 /rules 查看全部。" % rule_id


def tool_rules():
    rows = []
    for k, why in RULE_DOC.items():
        rows.append({"id": k, "级别": "红" if k.startswith("red") else "黄", "说明": why})
    return rows


REASONS = {
    "患者拒绝": "因患者拒绝，未行{item}。",
    "病情不允许": "因患者病情危重/不配合，未能行{item}。",
    "临床判断不必要": "经临床综合评估，本例无{item}指征，未行该项检查。",
    "条件限制": "因本院设备条件限制，未行{item}，已建议转诊或外院完善。",
    "外院已完成": "{item}已在外院完成，结果详见外院检查资料。",
}


def tool_ignore(rule_id, reason, item=None):
    tpl = REASONS.get(reason)
    if not tpl:
        return "理由只能是：%s" % "、".join(REASONS)
    return "已生成补金：%s" % tpl.format(item=item or rule_id)


def tool_batch(folder):
    files = sorted(Path(folder).glob("*.txt"))
    if not files:
        return "该文件夹没有 .txt 病历。"
    lines = ["批量核查：%s（%d 份）" % (folder, len(files)), ""]
    lines.append("%-28s %5s %5s %5s  %s" % ("文件", "强提醒", "红线", "底线", "初步诊断"))
    for f in files:
        try:
            r = tool_check(JZ.read_file(str(f)))
            lines.append("%-28s %5d %5d %5d  %s" % (
                f.name[:26], len(r["red"]), len([x for x in r["redlist"] if x["missing"]]),
                len([x for x in r["baseline"] if not x["ok"] and not x["exempt"]]),
                "、".join(r["main"]) or "未写"))
        except Exception as e:
            lines.append("%-28s 解析失败：%s" % (f.name[:26], e))
    return "\n".join(lines)

# =====================================================================
# 二、大模型（可选）：不配置就走本地规则回答
# =====================================================================


def llm_conf():
    if os.environ.get("DEEPSEEK_API_KEY"):
        return ("https://api.deepseek.com/v1/chat/completions", os.environ["DEEPSEEK_API_KEY"],
                os.environ.get("LLM_MODEL", "deepseek-chat"))
    if os.environ.get("OPENAI_API_KEY"):
        return (os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
                os.environ["OPENAI_API_KEY"], os.environ.get("LLM_MODEL", "gpt-4o-mini"))
    return None


def llm_chat(system, user, history=None):
    conf = llm_conf()
    if not conf:
        return None
    url, key, model = conf
    msgs = [{"role": "system", "content": system}]
    for h in history or []:
        msgs.append(h)
    msgs.append({"role": "user", "content": user})
    body = json.dumps({"model": model, "messages": msgs, "temperature": 0.2}).encode("utf-8")
    req = urlrequest.Request(url, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return "（大模型调用失败：%s；以下是本地规则结果）" % e

# =====================================================================
# 三、会话：记忆 + 工具路由
# =====================================================================


class Agent:
    def __init__(self):
        self.case = ""
        self.last = None
        self.ignores = []
        self.history = []

    def context_summary(self):
        if not self.last:
            return "（当前没有载入病历）"
        ctx = self.last["ctx"]
        return ("当前病历：%s，%s；初步诊断：%s；强提醒 %d 条；红线缺项 %d 条"
                % (ctx["sex"] or "性别未记录", (str(ctx["age"]) + "岁") if ctx["age"] else "年龄未记录",
                   "、".join(self.last["main"]) or "未写",
                   len(self.last["red"]), len([x for x in self.last["redlist"] if x["missing"]])))

    def load(self, text):
        self.case = text
        self.last = tool_check(text)
        return self.last

    def answer(self, q):
        """本地意图路由：不依赖大模型也能答。"""
        ql = q.lower()
        if not self.last:
            return "还没有载入病历。把病历粘进来，或点网页上的「核查」按钮。"
        if re.search(r"(鉴别草稿|draft|帮我写鉴别|生成鉴别)", q):
            return tool_draft(self.case)
        if re.search(r"(还缺|缺什么|补什么|gap|待补|下一步)", ql):
            gaps = tool_gaps(self.case)
            if not gaps:
                return "这份病历的关键项都齐了。"
            return "\n".join("· [%s] %s\n    怎么补：%s" % (g["级"], g["内容"], g["怎么补"]) for g in gaps[:12])
        if re.search(r"(红线|致死|危险)", q):
            rows = [x for x in self.last["redlist"]]
            if not rows:
                return "本例未见红线病因证据。"
            return "\n".join("%s %s%s" % ("✔" if not x["missing"] else "✘", x["label"],
                                          "" if not x["missing"] else " 缺 " + "、".join(x["missing"][:3]))
                             for x in rows)
        if re.search(r"(为什么|依据|解释)", q):
            m = re.search(r"(red-[\w-]+|yellow-[\w-]+)", q)
            return tool_explain(m.group(1)) if m else "请给出规则 ID，例如 /explain red-hcg（/rules 查看全部）。"
        if re.search(r"(严重|怎么样|评价|打分)", q):
            return ("初步诊断：%s\n强提醒 %d 条，弱提示 %d 条；查体底线缺 %d 项。\n%s"
                    % ("、".join(self.last["main"]) or "未写",
                       len(self.last["red"]), len(self.last["yellow"]),
                       len([x for x in self.last["baseline"] if not x["ok"] and not x["exempt"]]),
                       self.context_summary()))
        return None

    def ask(self, q):
        local = self.answer(q)
        if local is not None and not llm_conf():
            return local
        system = ("你是急腹症病历质控助手。基于下面这份病历的核查结果回答，"
                  "只讲结论和下一步怎么做，不要编造病历里没有的信息。\n\n" + self.context_summary())
        if self.last:
            system += "\n强提醒：" + "；".join(r["title"] for r in self.last["red"][:6])
            system += "\n检查缺失：" + "；".join(x["label"] for x in self.last["baseline"]
                                              if not x["ok"] and not x["exempt"])
        reply = llm_chat(system, q, self.history[-6:])
        self.history += [{"role": "user", "content": q}]
        if reply:
            self.history.append({"role": "assistant", "content": reply})
            return reply
        return local or "（未配置大模型，我只会做规则核查。可用命令：/check /gaps /causes /red /rules /explain）"

    def handle(self, line):
        """斜杠命令。返回 (类型, 内容)。"""
        s = line.strip()
        if not s:
            return "text", ""
        if not s.startswith("/"):
            p = Path(s.strip('"').strip("'"))
            if len(s) < 260 and p.is_file():
                return "report", self.load(JZ.read_file(str(p)))["report"]
            if len(s) > 80 or re.search(r"(主诉|现病史|体格检查|初步诊断)", s):
                return "report", self.load(s)["report"]
            return "text", self.ask(s)
        cmd, _, arg = s[1:].partition(" ")
        cmd = cmd.lower()
        if cmd in ("check", "核查"):
            if not self.case:
                return "text", "还没有病历。先粘病历，或用 /load <文件路径>。"
            return "report", self.load(self.case)["report"]
        if cmd == "load":
            p = Path(arg.strip().strip('"'))
            if not p.is_file():
                return "text", "找不到文件：%s" % p
            return "report", self.load(JZ.read_file(str(p)))["report"]
        if cmd in ("gaps", "缺"):
            gaps = tool_gaps(self.case)
            return "text", ("\n".join("· [%s] %s\n    怎么补：%s" % (g["级"], g["内容"], g["怎么补"]) for g in gaps)
                            if gaps else "关键项都齐了。")
        if cmd in ("draft", "鉴别草稿"):
            return "text", tool_draft(self.case)
        if cmd in ("yellow", "弱提示", "质控"):
            return "text", tool_yellow(self.case)
        if cmd in ("causes", "病因", "并发症", "前因后果"):
            return "text", tool_causes(self.case)
        if cmd in ("red", "红线"):
            return "text", self.answer("红线")
        if cmd == "rules":
            return "text", "\n".join("%-24s %s %s" % (r["id"], r["级别"], r["说明"]) for r in tool_rules())
        if cmd == "explain":
            return "text", tool_explain(arg)
        if cmd == "ignore":
            rid, _, reason = arg.partition(" ")
            self.ignores.append((rid, reason or "临床判断不必要"))
            return "text", tool_ignore(rid, reason or "临床判断不必要")
        if cmd == "batch":
            return "text", tool_batch(arg.strip() or ".")
        if cmd in ("save", "导出"):
            out = Path(arg.strip() or "核查报告.md")
            out.write_text(self.last["report"] if self.last else "", encoding="utf-8-sig")
            return "text", "已保存到 %s" % out
        if cmd in ("help", "?", "帮助"):
            return "text", HELP
        if cmd == "quit":
            return "quit", ""
        return "text", "未知命令 %s，/help 看用法。" % s

# =====================================================================
# 四、命令行
# =====================================================================

HELP = """命令：
  /load 路径      载入病历文件（也可以直接粘一段病历）
  /check          重新出报告
  /gaps           只列最该补的（含弱提示）
  /yellow         只列黄色弱提示（病历质控）
  /causes         只列病因 / 并发症分级预警
  /red            红线病因核对
  /rules          全部规则 ID
  /explain red-hcg  解释某条规则
  /ignore red-hcg 患者拒绝   生成免责补金句
  /batch 目录     批量核查
  /save 报告.md   导出
  直接提问        "这份病历还缺什么""帮我把鉴别写了\""""


def run_cli():
    ag = Agent()
    print("急腹症病历智能体 v%s（输入 /help 看命令，/quit 退出）" % VERSION)
    if llm_conf():
        print("已连接大模型：%s" % llm_conf()[2])
    else:
        print("未配置大模型：走本地规则（设置 DEEPSEEK_API_KEY 可开启自由问答）")
    print("-" * 60)
    while True:
        try:
            line = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0
        kind, out = ag.handle(line)
        if kind == "quit":
            print("再见。")
            return 0
        if out:
            print(out)

# =====================================================================
# 五、网页版
# =====================================================================

PAGE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>急腹症病历核查 · 智能体</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#262b36;--fg:#e6e9ef;--dim:#9aa4b2;
--red:#ff5d5d;--yel:#e8b339;--grn:#3fbf7f;--blue:#4c8dff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center}
header b{font-size:16px}header span{color:var(--dim);font-size:12px}
main{display:grid;grid-template-columns:1fr 1fr;gap:0;height:calc(100vh - 56px)}
.col{padding:16px;overflow:auto}.col:first-child{border-right:1px solid var(--line)}
textarea{width:100%;height:46vh;background:#0b0d12;color:var(--fg);border:1px solid var(--line);
border-radius:10px;padding:12px;font:13px/1.7 ui-monospace,Consolas,monospace;resize:vertical}
button{background:var(--blue);color:#fff;border:0;border-radius:8px;padding:9px 18px;font-size:14px;cursor:pointer}
button.ghost{background:#20242e;color:var(--fg);border:1px solid var(--line)}
button:disabled{opacity:.5;cursor:not-allowed}
.row{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap;align-items:center}
pre{white-space:pre-wrap;word-break:break-word;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:14px;font:12.5px/1.65 ui-monospace,Consolas,monospace;margin:0}
.chat{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
.msg{padding:8px 12px;border-radius:8px;margin:6px 0;max-width:92%}
.me{background:#1d2430;margin-left:auto}.ai{background:#141922;border:1px solid var(--line)}
input[type=text]{flex:1;min-width:200px;background:#0b0d12;color:var(--fg);border:1px solid var(--line);
border-radius:8px;padding:9px 12px}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:12px}
.p-red{background:#3a1d1d;color:var(--red)}.p-yel{background:#3a311d;color:var(--yel)}
.p-grn{background:#17331f;color:var(--grn)}
.tabs{display:flex;gap:6px;margin-bottom:10px}
.tabs button{padding:6px 12px;font-size:13px;background:#20242e}
.tabs button.on{background:var(--blue)}
</style></head><body>
<header><b>急腹症病历核查 · 智能体</b>
<span>引擎 {{V}} ｜ 把病历粘到左边 → 点「核查」；右侧可直接对话</span></header>
<main>
 <div class="col">
  <textarea id="case" placeholder="把病历整段粘在这里：主诉 / 现病史 / 体格检查 / 辅助检查 / 初步诊断 / 鉴别诊断"></textarea>
  <div class="row">
   <button id="go">核查</button>
   <button class="ghost" id="loaddemo">载入示例</button>
   <span id="status" style="color:var(--dim)"></span>
  </div>
  <div class="row"><span id="pills"></span></div>
 </div>
 <div class="col">
  <div class="tabs">
   <button class="on" data-t="report">完整报告</button>
   <button data-t="gaps">还缺什么</button>
   <button data-t="causes">病因/并发症</button>
   <button data-t="yellow">弱提示</button>
  </div>
  <pre id="out">等待核查…</pre>
  <div class="chat">
   <div id="msgs"></div>
   <div class="row">
    <input type="text" id="q" placeholder="问一句，例如：这份病历还缺什么？">
    <button id="send">发送</button>
   </div>
  </div>
 </div>
</main>
<script>
const $=s=>document.querySelector(s);
let CASE="";
async function post(url,obj){
  const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(obj)});
  if(!r.ok) throw new Error(await r.text());
  return await r.json();
}
function pills(d){
  const p=[];
  if(d.acute===false) p.push(`<span class="pill p-yel">未命中急腹症（产品默认静默退出）</span>`);
  p.push(`<span class="pill p-grn">${d.main||"未写诊断"}</span>`);
  d.red.forEach(t=>p.push(`<span class="pill p-red">● ${t}</span>`));
  if(d.gaps) p.push(`<span class="pill p-yel">待补 ${d.gaps} 项</span>`);
  if(d.yellow) p.push(`<span class="pill p-yel">弱提示 ${d.yellow}</span>`);
  $("#pills").innerHTML=p.join(" ");
}
$("#loaddemo").onclick=async()=>{
  const r=await post("/api/demo",{}); $("#case").value=r.text; $("#status").textContent="已载入示例";
};
$("#go").onclick=async()=>{
  CASE=$("#case").value.trim();
  if(!CASE){$("#status").textContent="先粘病历";return;}
  $("#go").disabled=true;$("#status").textContent="核查中…";
  try{
    const d=await post("/api/check",{text:CASE});
    $("#out").textContent=d.report; pills(d);
    $("#status").textContent="完成";
  }catch(e){$("#out").textContent="出错："+e.message;}
  $("#go").disabled=false;
};
document.querySelectorAll(".tabs button").forEach(b=>{
  b.onclick=async()=>{
    document.querySelectorAll(".tabs button").forEach(x=>x.classList.remove("on"));
    b.classList.add("on");
    if(!CASE){$("#out").textContent="请先核查一份病历";return;}
    const d=await post("/api/tool",{text:CASE,tool:b.dataset.t});
    $("#out").textContent=d.text;
  };
});
async function ask(){
  const q=$("#q").value.trim(); if(!q) return;
  $("#msgs").insertAdjacentHTML("beforeend",`<div class="msg me">${q}</div>`);
  $("#q").value="";
  const d=await post("/api/chat",{text:CASE,q:q});
  $("#msgs").insertAdjacentHTML("beforeend",`<div class="msg ai">${d.text.replace(/</g,"&lt;")}</div>`);
  $("#msgs").scrollIntoView(false);
}
$("#send").onclick=ask;
$("#q").addEventListener("keydown",e=>{if(e.key==="Enter")ask();});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    agent = Agent()

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.replace("{{V}}", "v" + JZ.VERSION), "text/html; charset=utf-8")
        elif self.path == "/api/version":
            self._send(200, json.dumps({"engine": JZ.VERSION, "agent": VERSION}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            self._send(400, json.dumps({"error": "bad json"}))
            return
        try:
            if self.path == "/api/check":
                text = body.get("text", "")
                r = self.agent.load(text)
                gaps = tool_gaps(text)
                self._send(200, json.dumps({
                    "report": r["report"], "main": "、".join(r["main"]),
                    "red": [x["title"] for x in r["red"]][:5], "gaps": len(gaps),
                    "yellow": len(r["yellow"]), "acute": JZ.is_acute(r["ctx"]),
                }, ensure_ascii=False))
            elif self.path == "/api/tool":
                text, tool = body.get("text", ""), body.get("tool", "report")
                if not self.agent.last or self.agent.case != text:
                    self.agent.load(text)
                if tool == "gaps":
                    gaps = tool_gaps(text)
                    out = "\n".join("· [%s] %s\n    怎么补：%s" % (g["级"], g["内容"], g["怎么补"]) for g in gaps)
                    out = out or "关键项都齐了。"
                elif tool == "yellow":
                    out = tool_yellow(text)
                elif tool == "causes":
                    out = tool_causes(text)
                elif tool == "draft":
                    out = tool_draft(text)
                else:
                    out = self.agent.last["report"]
                self._send(200, json.dumps({"text": out}, ensure_ascii=False))
            elif self.path == "/api/chat":
                text, q = body.get("text", ""), body.get("q", "")
                if text and (not self.agent.last or self.agent.case != text):
                    self.agent.load(text)
                self._send(200, json.dumps({"text": self.agent.ask(q)}, ensure_ascii=False))
            elif self.path == "/api/demo":
                self._send(200, json.dumps({"text": JZ.DEMOS["3"]}, ensure_ascii=False))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))


def pick_port(start):
    """端口被旧进程占用时自动往后找，避免"新进程绑不上、浏览器还指着旧服务"。"""
    import socket
    for p in range(start, start + 20):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
            continue
    return start


def run_web(open_browser=True):
    if os.environ.get("AGENT_NO_BROWSER"):
        open_browser = False
    port = pick_port(PORT)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    pidfile = HERE / "agent.pid"
    portfile = HERE / "agent.port"
    for f, v in ((pidfile, str(os.getpid())), (portfile, str(port))):
        try:
            f.write_text(v, encoding="utf-8")
        except Exception:
            pass
    url = "http://127.0.0.1:%d/" % port
    print("智能体已启动：%s" % url)
    print("引擎版本：jifuzheng.py v%s   智能体：v%s" % (JZ.VERSION, VERSION))
    if port != PORT:
        print("（端口 %d 被占用，已自动改用 %d）" % (PORT, port))
    print("大模型：%s" % (("已连接 " + llm_conf()[2]) if llm_conf() else "未配置（本地规则模式）"))
    print("按 Ctrl+C 停止，或双击【关闭智能体.vbs】")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        for f in (pidfile, portfile):
            try:
                f.unlink()
            except Exception:
                pass
    return 0

# =====================================================================
# 六、入口
# =====================================================================


def selftest():
    """一条命令检查所有工具能不能跑通（页面报"出错"时先跑这个）。"""
    case = JZ.DEMOS.get("1", "")
    checks = [
        ("引擎报告", lambda: JZ.report(JZ.analyze(case))),
        ("/check", lambda: tool_check(case)),
        ("/gaps", lambda: tool_gaps(case)),
        ("/yellow", lambda: tool_yellow(case)),
        ("/causes", lambda: tool_causes(case)),
        ("/batch", lambda: tool_batch("测试病例")),
        ("问答", lambda: Agent().ask("还缺什么")),
        ("规则表", lambda: tool_rules()),
    ]
    bad = 0
    print("智能体自检（引擎 v%s / 智能体 v%s）" % (JZ.VERSION, VERSION))
    for name, fn in checks:
        try:
            out = fn()
            print("  OK   %-8s 输出 %d 字" % (name, len(str(out))))
        except Exception as e:
            bad += 1
            print("  FAIL %-8s %s: %s" % (name, type(e).__name__, e))
    print("全部通过 ✔" if not bad else "有 %d 项失败，把上面 FAIL 那几行发给管理员" % bad)
    return 0 if not bad else 1


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args and args[0] in ("-v", "--version"):
        print("agent.py v%s   引擎 jifuzheng.py v%s" % (VERSION, JZ.VERSION))
        return 0
    if args and args[0] in ("--selftest", "--test"):
        return selftest()
    if args and args[0] == "--cli":
        return run_cli()
    if args and args[0] in ("--web", "--serve"):
        return run_web()
    if args and args[0] in ("--rules",):
        print("\n".join("%-24s %s %s" % (r["id"], r["级别"], r["说明"]) for r in tool_rules()))
        return 0
    if args and Path(args[0]).is_file():
        r = tool_check(JZ.read_file(args[0]))
        print(r["report"])
        return 0
    return run_web()


if __name__ == "__main__":
    raise SystemExit(main())
