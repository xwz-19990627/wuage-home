#!/usr/bin/env python3
"""wuage-home parse — 一句话记账 → 结构化 JSON（OpenCode Go 网关，契约与 v2 数据层一致）。

CLI:  python3.11 scripts/parse.py "今天买菜花了86元"
Web:  from parse import parse_text
候选：categories / members 由调用方（web.py 从 DB 读）传入，模型只能从中选。
凭据链：OPENCODE_GO_API_KEY（环境变量 → $DSH_HOME/.credentials.yaml）
"""

import datetime
import json
import os
import re
import sys
import urllib.request
import urllib.error

API_URL = os.environ.get("WUAGE_PARSE_API", "https://opencode.ai/zen/go/v1/chat/completions")
MODEL = os.environ.get("WUAGE_PARSE_MODEL", "deepseek-v4-flash")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

FALLBACK_CATEGORIES = ["餐饮", "交通", "医疗", "购物", "教育", "娱乐", "居住", "通讯", "人情", "其他"]


def build_system_prompt(categories=None, members=None):
    today = datetime.date.today()
    yest = (today - datetime.timedelta(days=1)).isoformat()
    before = (today - datetime.timedelta(days=2)).isoformat()
    cats = "、".join(categories) if categories else "、".join(FALLBACK_CATEGORIES)
    lines = [
        "今天是 {td}。你是 wuage-home 家庭账本记账解析器，把一句话消费记录解析为 JSON。",
        "规则：",
        "- amount_cents：金额整数（单位：分）。口语都认：10块→1000，23块5→2350，十块→1000。",
        "- date：YYYY-MM-DD 完整日期。默认今天 {td}；昨天={yest}，前天={before}，\"上周X\"以此类推准确换算。",
        "- kind：默认 expense；明确说收入/转账才用 income/transfer。",
        "- category：必须从候选分类选：{cats}。分不准选\"其他\"。",
        "- member：家庭成员名，从候选成员选（若给出）；没提谁花的就默认\"本人\"。",
        "- merchant：商户/商家（如医院、菜市场），没提到就 null。",
        "- ai_confidence：0~1 的自评置信度（数字）。",
        "- tags：恒为 []。",
        "- note：用户原话，原样保留，不改写。",
        "输出必须包含且仅包含：amount_cents, date, kind, category, member, merchant, ai_confidence, tags, note。",
        "只输出一个 JSON 对象，不要任何解释、Markdown 或多余文字。",
    ]
    body = "\n".join(lines).format(td=today.isoformat(), yest=yest, before=before, cats=cats)
    if members:
        body += "\n成员候选（从这些里选，没提就本人）：" + "、".join(members)
    return body


def api_key():
    k = os.environ.get("OPENCODE_GO_API_KEY")
    if k:
        return k
    home = os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh")
    try:
        with open(os.path.join(home, ".credentials.yaml")) as f:
            for line in f:
                line = line.strip()
                if line.startswith("OPENCODE_GO_API_KEY:"):
                    return line.split(":", 1)[1].strip().strip(chr(34)).strip(chr(39))
    except OSError:
        pass
    return None


def _strip_fence(s):
    F = chr(96)
    if s.startswith(F * 3):
        s = s[3:]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
        if s.endswith(F * 3):
            s = s[:-3].rstrip()
    return s


def _extract_json(content):
    if not content or not isinstance(content, str):
        raise ValueError("模型未返回内容")
    s = _strip_fence(content.strip())
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("内容中没有 JSON：" + content[:120])
    return json.loads(s[a:b + 1])


def _normalize(obj, text, categories, members):
    if not isinstance(obj, dict):
        raise ValueError("解析结果不是对象")
    category = obj.get("category")
    if category not in (categories or FALLBACK_CATEGORIES):
        category = "其他"
    member = obj.get("member") or "本人"
    if members and member not in members:
        member = "本人"
    try:
        confidence = float(obj.get("ai_confidence") or 0.8)
        if not (0 <= confidence <= 1):
            confidence = 0.8
    except (TypeError, ValueError):
        confidence = 0.8
    out = {
        "amount_cents": int(obj["amount_cents"]),
        "date": obj.get("date") or datetime.date.today().isoformat(),
        "kind": obj.get("kind") or "expense",
        "category": category,
        "member": member,
        "merchant": obj.get("merchant"),
        "ai_confidence": confidence,
        "tags": obj.get("tags") or [],
        "note": text,
    }
    if out["kind"] not in ("expense", "income", "transfer"):
        out["kind"] = "expense"
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", out["date"]):
        out["date"] = datetime.date.today().isoformat()
    return out


def parse_text(text, categories=None, members=None, model=None):
    key = api_key()
    if not key:
        raise RuntimeError("OPENCODE_GO_API_KEY 未配置（环境变量或 $DSH_HOME/.credentials.yaml）")
    payload = {
        "model": model or MODEL,
        "messages": [
            {"role": "system", "content": build_system_prompt(categories, members)},
            {"role": "user", "content": str(text)},
        ],
        "max_tokens": 512,
    }
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                API_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + key,
                    "User-Agent": USER_AGENT,
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return _normalize(_extract_json(content), str(text), categories, members)
        except urllib.error.HTTPError as e:
            raise RuntimeError("解析服务 {}: {}".format(e.code, e.read().decode("utf-8")[:200]))
        except Exception as ex:
            last = ex
    raise RuntimeError("解析失败（重试后仍失败）: {}".format(last))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3.11 scripts/parse.py 今天买菜花了86元")
        sys.exit(2)
    print(json.dumps(parse_text(" ".join(sys.argv[1:])), ensure_ascii=False))
