#!/usr/bin/env python3
"""wuage-home parse — 一句话记账 → 结构化 JSON（OpenCode Go 网关直调，契约与 wuage-ledger 技能卡一致）。

CLI:  python3.11 scripts/parse.py "今天买西瓜花了10块"
Web:  from parse import parse_text
凭据链: 环境变量 OPENCODE_GO_API_KEY → $DSH_HOME/.credentials.yaml（默认 ~/.dsh）
配置: WUAGE_PARSE_MODEL（默认 deepseek-v4-flash）、WUAGE_PARSE_API（默认 opencode.ai/zen/go）
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

CANONICAL_CATEGORIES = ["餐饮", "交通", "居住", "购物", "娱乐",
                        "育儿教育", "医疗健康", "人情往来", "其他"]


def system_prompt():
    today = datetime.date.today()
    yest = (today - datetime.timedelta(days=1)).isoformat()
    before = (today - datetime.timedelta(days=2)).isoformat()
    cats = "、".join(CANONICAL_CATEGORIES)
    return (
        "今天是 {td}。你是 wuage-home 家庭账本记账解析器，把一句话消费记录解析为 JSON。\n"
        "规则：\n"
        "- amount_cents：金额整数（单位：分）。口语都认：10块→1000，23块5→2350，十块→1000。\n"
        "- date：YYYY-MM-DD 完整日期。默认今天 {td}；昨天={yest}，前天={before}，\"上周X\"以此类推准确换算。\n"
        "- kind：默认 expense；明确说收入/转账才用 income/transfer。\n"
        "- category：必须从这9个选：{cats}。分不准选\"其他\"。\n"
        "- tags：恒为 []。\n"
        "- note：用户原话，原样保留，不改写。\n"
        "- source：manual。\n"
        "输出必须包含且仅包含：amount_cents, date, kind, category, tags, note, source。\n"
        "只输出一个 JSON 对象，不要任何解释、Markdown 或多余文字。"
    ).format(td=today.isoformat(), yest=yest, before=before, cats=cats)


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
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _extract_json(content):
    if not content or not isinstance(content, str):
        raise ValueError("模型未返回内容")
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"```\s*$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("内容中没有 JSON：" + content[:120])
    return json.loads(s[a:b + 1])


def _normalize(obj, text):
    if not isinstance(obj, dict):
        raise ValueError("解析结果不是对象")
    out = {
        "amount_cents": int(obj["amount_cents"]),
        "date": obj.get("date") or datetime.date.today().isoformat(),
        "kind": obj.get("kind") or "expense",
        "category": obj.get("category"),
        "tags": obj.get("tags") or [],
        "note": text,
        "source": obj.get("source") or "manual",
    }
    if out["category"] not in CANONICAL_CATEGORIES:
        out["category"] = "其他"
    if out["kind"] not in ("expense", "income", "transfer"):
        out["kind"] = "expense"
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", out["date"]):
        out["date"] = datetime.date.today().isoformat()
    return out


def parse_text(text, model=None):
    key = api_key()
    if not key:
        raise RuntimeError("OPENCODE_GO_API_KEY 未配置（环境变量或 $DSH_HOME/.credentials.yaml）")
    payload = {
        "model": model or MODEL,
        "messages": [
            {"role": "system", "content": system_prompt()},
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
            return _normalize(_extract_json(content), str(text))
        except urllib.error.HTTPError as e:
            raise RuntimeError("解析服务 {}: {}".format(e.code, e.read().decode("utf-8")[:200]))
        except Exception as ex:  # noqa: BLE001 — 解析失败重试一次
            last = ex
    raise RuntimeError("解析失败（重试后仍失败）: {}".format(last))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3.11 scripts/parse.py 今天买西瓜花了10块")
        sys.exit(2)
    print(json.dumps(parse_text(" ".join(sys.argv[1:])), ensure_ascii=False))
