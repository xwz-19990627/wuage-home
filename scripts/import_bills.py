#!/usr/bin/env python3
"""wuage-home 账单导入器 — 微信支付 / 支付宝 账单 → 账本。

流程：
  1) 识别文件（微信 xlsx / 支付宝 csv，文件名与表头双重判断）
  2) 解析 + 归一化（时间/金额/收支 → 记录）
  3) 分类映射：支付宝直接映射其交易分类；微信按 商户/商品 关键词推断；
     未命中 → "其他"（预览时列清单）
  4) 去重：external_id = wx:/ali: + 交易单号（transactions.external_id 唯一索引）
  5) preview 预览 → 确认后 import 入库（source=import, channel=import）

特殊处理：
  - 支付宝"不计收支" / 微信"中性"（理财、充值、还款）→ kind=transfer，不进收支统计
  - 支付宝"交易关闭" / 金额为 0 → 跳过
  - 退款记录 → kind=income 冲减（微信退款行本身是收入行，照导）
  - 全部记在"本人"名下（备注保留原始对方/商品，便于事后改成员）

用法：
  python3 scripts/import_bills.py list
  python3 scripts/import_bills.py preview <文件>
  python3 scripts/import_bills.py import <文件> [--yes] [--member 蛙哥] [--json]
"""

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger as L  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = Path(os.environ.get("WUAGE_UPLOAD_DIR", str(REPO_ROOT / "data" / "files")))

PLATFORM_NAME = {"wx": "微信", "ali": "支付宝"}
PLATFORM_PREFIX = {"wx": "wx:", "ali": "ali:"}

# ── 支付宝：交易分类 → 种子分类 ──────────────────────────────────────────────
ALI_CAT_MAP = {
    "餐饮美食": "餐饮",
    "交通出行": "交通",
    "爱车养车": "交通",
    "医疗健康": "医疗",
    "日用百货": "购物",
    "服饰装扮": "服饰",
    "数码电器": "购物",
    "美容美发": "美妆",
    "宠物": "宠物",
    "家居家装": "购物",
    "文化休闲": "娱乐",
    "酒店旅游": "娱乐",
    "充值缴费": "其他",   # 按商品关键词细分（话费→通讯/点券→娱乐/水电→居住）
    "生活服务": "其他",   # 按商品关键词细分（药品→医疗/外卖→餐饮/打车→交通）
    "商业服务": "其他",
    "公共服务": "保险",   # 养老医疗社保缴费
    "保险": "保险",
    "收入": "其他",
}
# 支付宝分类 → 视为转账（不进收支统计）；投资理财单列"理财"分类
ALI_TRANSFER_CATS = {"信用借还"}
# 笼统分类按 商品说明/对方 关键词细分（先命中先得）
ALI_KEYWORD_CATS = {
    "充值缴费": [("通讯", ["话费", "移动", "联通", "电信", "宽带"]),
                ("娱乐", ["王者", "点券", "游戏"]),
                ("居住", ["水电", "燃气", "物业", "房租"])],
    "生活服务": [("医疗", ["药品", "药房", "诊所", "医院"]),
                ("餐饮", ["汉堡", "外卖", "美团", "餐", "饭", "餐厅"]),
                ("交通", ["打车", "高德", "滴滴", "出租车"])],
}

# ── 微信：按 交易对方+商品+备注 关键词推断 ──────────────────────────────────
WX_KEYWORD_RULES = [
    ("交通", ["充电", "高速", "加油", "停车", "通行宝", "ETC", "地铁", "公交",
              "滴滴", "打车", "出租车", "客运", "加油站", "轮胎", "汽修", "洗车",
              "充电站", "先充后付", "汽车", "充电桩", "拖车", "城泊", "充换电",
              "哈啰", "车业", "骑行"]),
    ("餐饮", ["餐饮", "饭店", "面馆", "炸鸡", "烧烤", "火锅", "咖啡", "奶茶",
              "小吃", "外卖", "餐厅", "食堂", "汉堡", "饺子", "拉面", "酸菜鱼",
              "龙虾", "快餐", "茶饮", "烘焙", "包子", "馒头", "锅贴", "粥铺",
              "熟食", "卤", "甜品", "蛋糕", "面", "米粉", "烧烤店", "快餐店",
              "餐饮店", "轻食", "凉皮", "鲜包", "和牛", "饭", "茗", "小食",
              "食府", "食记", "食客", "披萨", "寿司", "烤肉", "麻辣烫", "米线",
              "馄饨", "烧饼", "煎饼", "寿司", "鸭脖", "炸串", "汤包", "鱼粉",
              "肉夹馍", "兰州拉面", "肯德基", "麦当劳", "必胜客", "生煎", "西塔",
              "汤面", "拌面", "炒饭", "盖浇饭", "煲仔饭", "麻辣香锅", "冒菜",
              "卡旺卡", "把子肉", "凉皮", "煎饼", "大排档", "烤串", "茶楼",
              "地锅鸡", "鸡锁骨", "星厨", "仟吉", "堂食"]),
    ("医疗", ["医院", "诊所", "药店", "药房", "体检", "门诊", "卫生室", "药品", "医保"]),
    ("购物", ["超市", "便利店", "商行", "百货", "水果", "生鲜", "菜市场", "菜市",
              "商城", "商场", "天猫", "淘宝", "京东", "拼多多", "苏宁", "名创",
              "优品", "永辉", "华联", "大润发", "盒马", "文具", "书店", "数码",
              "电器", "山姆", "罗森", "雪糕", "冰淇淋", "零食",
              "商贸", "批发", "商店", "会员店", "优选", "严选", "良品", "零售",
              "老婆大人", "自贩机", "小卖部", "购物", "理发", "美发", "洗衣",
              "便利店", "超市", "便利购", "砂之船", "奥特莱斯", "果", "美容",
              "烟酒", "售货机", "电商", "尚街", "商厦", "微店"]),
    ("服饰", ["服饰", "服装", "女装", "男装", "连衣裙", "毛衣", "卫衣", "短袖",
              "衬衫", "牛仔", "羽绒服", "外套", "开衫", "半身裙", "裙", "裤",
              "帽子", "围巾", "袜子", "鞋", "箱包", "行李箱", "包包", "汉服",
              "lolita", "洛丽塔", "制服", "背心", "吊带", "内衣", "文胸", "睡衣",
              "风衣", "大衣", "洞洞鞋", "雪地靴", "板鞋", "运动鞋", "凉鞋",
              "拖鞋", "发箍", "发夹", "腰带", "耳环", "珠宝", "首饰", "饰品"]),
    ("美妆", ["美妆", "口红", "唇釉", "护肤", "面霜", "精华", "防晒", "粉底",
              "遮瑕", "眼线", "睫毛", "美甲", "甲油", "染发", "洗发", "护发",
              "发膜", "香水", "洗面奶", "面膜", "水乳", "彩妆", "素颜霜", "眼霜",
              "身体乳", "散粉", "高光", "修容", "眉笔", "睫毛膏", "卸妆", "沐浴露"]),
    ("宠物", ["宠物", "狗狗", "猫咪", "泰迪", "猫粮", "狗粮", "尿垫", "牵引绳",
              "驱虫", "狗窝", "猫砂", "宠物用品", "宠物推车", "猫条", "猫犬",
              "狗床"]),
    ("教育", ["教育", "培训", "课程", "学费", "图书", "书店"]),
    ("娱乐", ["电影", "影院", "KTV", "ktv", "游戏", "网吧", "景区", "门票",
              "游乐", "旅游", "旅行", "游乐场", "文化园", "天游", "棋牌", "洗浴",
              "植物园", "王者", "点券", "天柱山", "景区"]),
    ("居住", ["房租", "物业", "水电", "燃气", "供暖", "维修", "家居", "房东"]),
    ("通讯", ["话费", "流量", "移动", "联通", "电信", "宽带"]),
    ("人情", ["红包", "转账"]),
]
WX_TRANSFER_TYPES = {"零钱充值", "零钱提现", "零钱通", "理财通"}

# 家庭内部往来：微信转账/扫码给对方（家庭成员昵称）→ 记 transfer，不算家庭支出。
# 2026-08-23 确认：小猪是个大美女🎀 = 蛙哥（转给她的钱是家庭内转移）。
WX_FAMILY_TRANSFERS = {
    "小猪是个大美女": {"member": "本人", "note": "[转蛙哥-家庭往来]"},
    "臭猪猪": {"member": "蛙哥", "note": "[转残雪-家庭往来]"},
    "德玛西亚": {"member": "蛙哥", "note": "[转残雪-家庭往来]"},
}
# 支付宝家庭互转（本人↔蛙哥 转账红包）→ transfer
ALI_FAMILY_TRANSFERS = {"五阿哥": "蛙哥"}


# ── 基础工具 ─────────────────────────────────────────────────────────────────
def yuan_to_cents(s):
    """金额(元) → 分（四舍五入到分，容忍千分位/空格）。"""
    t = str(s).strip().replace(",", "").replace(" ", "")
    if not t:
        raise ValueError("empty amount")
    d = Decimal(t)
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))


def ts_to_date(ts):
    s = str(ts).strip()
    return s[:10] if len(s) >= 10 else s


def clean(v):
    return str(v or "").strip()


def detect_platform(path):
    name = Path(path).name
    if "微信" in name:
        return "wx"
    if "支付宝" in name:
        return "ali"
    return None


# ── 解析：支付宝 csv ─────────────────────────────────────────────────────────
def parse_alipay_rows(path):
    raw = Path(path).read_bytes()
    text = None
    for enc in ("gb18030", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("无法解码支付宝账单（尝试 gb18030/utf-8 均失败）")
    lines = text.splitlines()
    # 表头行：以"交易时间"开头
    hi = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("交易时间"):
            hi = i
            break
    if hi is None:
        raise ValueError("支付宝账单：未找到表头行（交易时间,…）")
    header = [x.strip() for x in lines[hi].rstrip(",").split(",")]
    reader = csv.reader(io.StringIO(chr(10).join(lines[hi + 1:])))
    out = []
    for row in reader:
        if not row or not "".join(row).strip():
            continue
        row = [x.strip() for x in row]
        row += [""] * (len(header) - len(row))
        out.append(dict(zip(header, row[:len(header)])))
    return out


# ── 解析：微信 xlsx ──────────────────────────────────────────────────────────
def parse_wechat_rows(path):
    try:
        import openpyxl
    except ImportError:
        raise ValueError("解析微信 xlsx 需要 openpyxl：pip3 install openpyxl")
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    # 表头行：含"交易时间"与"金额(元)"
    hi = None
    for i, r in enumerate(rows):
        cells = [clean(x) for x in r]
        if "交易时间" in cells and "金额(元)" in cells:
            hi = i
            break
    if hi is None:
        raise ValueError("微信账单：未找到表头行（交易时间/金额(元)）")
    header = [clean(x) for x in rows[hi]]
    out = []
    for r in rows[hi + 1:]:
        cells = [clean(x) for x in r]
        cells += [""] * (len(header) - len(cells))
        out.append(dict(zip(header, cells[:len(header)])))
    return out


# ── 归一化 + 分类 ────────────────────────────────────────────────────────────
def _wx_category(row):
    """微信：关键词推断分类；命中返回分类名，否则 None。"""
    hay = " ".join([row.get("交易对方", ""), row.get("商品", ""),
                    row.get("备注", ""), row.get("交易类型", "")])
    for cat, words in WX_KEYWORD_RULES:
        for w in words:
            if w in hay:
                return cat
    return None


# 机构/行业特征词：交易对方含任意词则不算"个人收款"
PERSONAL_EXCLUDE = ("公司", "集团", "平台", "城市服务", "实业", "部", "厂", "所",
                    "馆", "中心", "管理", "科技", "商行", "超市", "商贸", "服务",
                    "售货", "充电", "电商", "车站", "分局", "分公司", "物业",
                    "公园", "广场", "大厦", "银行", "医院", "药房", "学校",
                    "幼儿园", "商户", "自助", "烟酒", "店", "消费")


def _is_personal(name):
    """启发：对方名不含机构/行业特征词 → 视为个人收款（归人情）。"""
    if not name:
        return False
    if "收款" in name:
        return False
    return not any(w in name for w in PERSONAL_EXCLUDE)


def normalize_wx(row):
    """微信行 → 归一化记录 dict；不可导入的返回 None + reason。"""
    io_v = clean(row.get("收/支"))
    amount = row.get("金额(元)")
    try:
        cents = yuan_to_cents(amount)
    except ValueError:
        return None, "金额异常: {!r}".format(amount)
    if cents <= 0:
        return None, "金额<=0"
    ext_id = PLATFORM_PREFIX["wx"] + clean(row.get("交易单号"))
    tx_type = clean(row.get("交易类型"))
    status = clean(row.get("当前状态"))
    tsv = clean(row.get("交易时间"))
    merchant = clean(row.get("交易对方"))
    product = clean(row.get("商品"))
    remark_parts = []
    if product and product not in ("/", "无"):
        remark_parts.append(product)
    if clean(row.get("备注")) and clean(row.get("备注")) not in ("/",):
        remark_parts.append("备注:" + clean(row.get("备注")))
    if tx_type and tx_type not in ("其他",):
        remark_parts.append("[" + tx_type + "]")
    remark = " ".join(remark_parts)

    # 中性交易（收/支=/）：零钱充值/理财通/还款 → 不导入（资金腾挪）
    if io_v == "/":
        return None, "中性/零钱腾挪"
    if tx_type in WX_TRANSFER_TYPES or "零钱" in tx_type:
        return None, "零钱/理财腾挪"

    if io_v == "收入":
        kind = "income"
    elif io_v == "支出":
        kind = "expense"
    else:
        kind = "transfer"

    # 退款行：类型/状态含"退款"且收入 → 冲减（按原商户关键词归回原分类）
    if (kind == "income" and ("退款" in tx_type or "退款" in status)) or tx_type.endswith("-退款"):
        cat = _wx_category(row)
        return ({"platform": "wx", "external_id": ext_id, "date": ts_to_date(tsv),
                 "time": tsv, "amount_cents": cents, "kind": "income",
                 "category": cat, "merchant": merchant, "product": product,
                 "remark": ("退款: " + remark) if remark else "退款", "status": status}, None)

    # 家庭内部往来：转给蛙哥等家庭成员 → 不导入（2026-08-25 规则：家庭互转是噪音）
    if "亲属卡" not in tx_type:
        for nick in WX_FAMILY_TRANSFERS:
            if nick in merchant or nick in product:
                return None, "家庭内部往来"

    # 红包 → 人情
    if "红包" in tx_type:
        cat = "人情"
    elif "亲属卡" in tx_type:
        cat = _wx_category(row)
        rec_member = "蛙哥"   # 亲属卡消费人=蛙哥（2026-08-23 确认）
        if not remark:
            remark = "亲属卡交易"
    elif tx_type == "二维码收款" or "二维码收款" in tx_type:
        cat = None
    else:
        cat = _wx_category(row)
    # 2026-08-23 用户拍板：个人收款/转账一律算人情（不识别为商户的）
    if cat is None and kind == "expense" and _is_personal(merchant):
        cat = "人情"
    return ({"platform": "wx", "external_id": ext_id, "date": ts_to_date(tsv),
             "time": tsv, "amount_cents": cents, "kind": kind, "category": cat,
             "member": locals().get("rec_member"), "merchant": merchant, "product": product,
             "remark": remark, "status": status}, None)


def normalize_ali(row):
    io_v = clean(row.get("收/支"))
    status = clean(row.get("交易状态"))
    if status == "交易关闭":
        return None, "交易关闭"
    amount = row.get("金额")
    try:
        cents = yuan_to_cents(amount)
    except ValueError:
        return None, "金额异常: {!r}".format(amount)
    if cents <= 0:
        return None, "金额<=0"
    ext_id = PLATFORM_PREFIX["ali"] + clean(row.get("交易订单号"))
    cat_v = clean(row.get("交易分类"))
    tsv = clean(row.get("交易时间"))
    merchant = clean(row.get("交易对方"))
    product = clean(row.get("商品说明"))
    remark_parts = []
    if cat_v:
        remark_parts.append("[" + cat_v + "]")
    if product and product not in ("/",):
        remark_parts.append(product)
    bz = clean(row.get("备注"))
    if bz and bz != "/":
        remark_parts.append("备注:" + bz)
    remark = " ".join(remark_parts)

    base = {"platform": "ali", "external_id": ext_id, "date": ts_to_date(tsv),
            "time": tsv, "amount_cents": cents, "merchant": merchant,
            "product": product, "remark": remark, "status": status}

    if io_v == "不计收支":
        if cat_v in ("投资理财", "信用借还"):
            return None, "理财/信用腾挪"
        if cat_v in ("公共服务", "保险"):
            return None, "社保同步行"   # 同社保缴费的同步记录（已按实际支出记账）
        return None, "不计收支"

    # 投资理财：买入/卖出/转入/转出都是资产腾挪 → 不导入；只有收益发放/分红=true 被动收入
    if cat_v == "投资理财":
        if io_v == "收入" and not ("卖出" in product or "转出" in product or "赎回" in product):
            return (dict(base, kind="income", category="理财"), None)
        return None, "理财腾挪"

    # 信用借还：还款/借出 → 不导入（资产腾挪）
    if cat_v in ALI_TRANSFER_CATS:
        return None, "信用借还/腾挪"

    if cat_v == "退款":
        return (dict(base, kind="income", category=None,
                     remark=("退款: " + remark) if remark else "退款"), None)

    if cat_v == "转账红包":
        for nick in ALI_FAMILY_TRANSFERS:
            if nick in merchant:
                return None, "家庭内部往来"
        if io_v == "收入":
            return (dict(base, kind="income", category="人情"), None)
        return (dict(base, kind="expense", category="人情"), None)

    # 笼统分类按商品/对方关键词细分（话费→通讯、药品→医疗、点券→娱乐…）
    if cat_v in ALI_KEYWORD_CATS:
        hay = " ".join([product, merchant, remark])
        cat = None
        for c, words in ALI_KEYWORD_CATS[cat_v]:
            if any(w in hay for w in words):
                cat = c
                break
        if cat is None:
            cat = "其他"
        kind = "income" if io_v == "收入" else ("expense" if io_v == "支出" else "transfer")
        return (dict(base, kind=kind, category=cat), None)

    if io_v == "收入":
        kind = "income"
    elif io_v == "支出":
        kind = "expense"
    else:
        kind = "transfer"

    # 亲友代付（亲情卡）：2026-08-23 确认五阿哥=蛙哥 → 记蛙哥名下，消费归其他
    cat = ALI_CAT_MAP.get(cat_v, "其他")
    if cat_v == "亲友代付":
        cat = "其他"
        base["member"] = "蛙哥"
    return (dict(base, kind=kind, category=cat), None)


def parse_bill(path):
    """返回 (platform, rows) 或抛错。"""
    platform = detect_platform(path)
    if platform == "wx" or (platform is None and Path(path).suffix.lower() == ".xlsx"):
        return "wx", parse_wechat_rows(path)
    if platform == "ali" or (platform is None and Path(path).suffix.lower() in (".csv", ".txt")):
        return "ali", parse_alipay_rows(path)
    raise ValueError("无法识别账单类型（文件名需含 微信/支付宝）")


def cancel_refunds(records):
    """同商户+同金额 的 消费(expense) 与 退款收入(income+退款备注) 正负抵消，两笔都移除。
    部分退款（金额不同）保留为收入冲减。"""
    exp = {}   # (merchant, amount_cents) -> [rec]
    ref = {}   # 同上，但仅退款收入
    for r in records:
        key = (r.get("merchant") or "", r["amount_cents"])
        if r["kind"] == "expense":
            exp.setdefault(key, []).append(r)
        elif r["kind"] == "income" and "退款" in (r.get("remark") or ""):
            ref.setdefault(key, []).append(r)
    killed = set()
    for key, exps in exp.items():
        refs = ref.get(key, [])
        n = min(len(exps), len(refs))
        for r in exps[:n]:
            killed.add(id(r))
        for r in refs[:n]:
            killed.add(id(r))
    return [r for r in records if id(r) not in killed]


def normalize_all(path):
    platform, rows = parse_bill(path)
    records, skipped = [], []
    for row in rows:
        if platform == "wx":
            rec, reason = normalize_wx(row)
        else:
            rec, reason = normalize_ali(row)
        if rec is None:
            skipped.append({"reason": reason, "time": clean(row.get("交易时间")),
                            "amount": clean(row.get("金额") or row.get("金额(元)"))})
        else:
            records.append(rec)
    return cancel_refunds(records), skipped


# ── 预览 / 导入 ──────────────────────────────────────────────────────────────
def build_preview(records, skipped, conn):
    L.ensure_seed(conn)
    total = len(records)
    by_kind_cat = Counter()
    by_cat_amount = {}
    unmatched = []
    dup_ids = set()
    ids = [r["external_id"] for r in records if r["external_id"]]
    existing = L.existing_external_ids(conn, ids)
    for r in records:
        if r["external_id"] in existing:
            dup_ids.add(r["external_id"])
            continue
        key = (r["kind"], r["category"] or "其他")
        by_kind_cat[key] += 1
        by_cat_amount[key] = by_cat_amount.get(key, 0) + r["amount_cents"]
        if r["category"] is None:
            unmatched.append(r)
    # 真正需要人工分类的是支出；收入/转账归「其他」用备注说明即可
    unmatched_exp = [r for r in unmatched if r["kind"] == "expense"]
    unmatched_rest = len(unmatched) - len(unmatched_exp)
    skip_counter = Counter(s["reason"] for s in skipped)
    return {
        "total": total,
        "dup": len(dup_ids),
        "skipped": len(skipped),
        "skip_reasons": dict(skip_counter),
        "by_kind_cat": [{"kind": k, "category": c, "count": n,
                         "amount_yuan": round(by_cat_amount.get((k, c), 0) / 100, 2)}
                        for (k, c), n in sorted(by_kind_cat.items(), key=lambda kv: -kv[1])],
        "unmatched_count": len(unmatched),
        "unmatched_expense_count": len(unmatched_exp),
        "unmatched_rest": unmatched_rest,
        "unmatched_expense": [{"date": r["date"], "amount": round(r["amount_cents"] / 100, 2),
                               "merchant": r.get("merchant", ""), "product": r.get("product", "")[:40]}
                              for r in unmatched_exp[:40]],
    }


def do_import(records, conn, member="本人"):
    fam_id = L.ensure_seed(conn)["family_id"]
    ids = [r["external_id"] for r in records if r["external_id"]]
    existing = L.existing_external_ids(conn, ids)
    imported = skipped_dup = 0
    for r in records:
        if r["external_id"] in existing:
            skipped_dup += 1
            continue
        rec_member = (r.get("member") or member or "本人").strip()
        tags = ["导入"] if rec_member == "本人" else ["导入", rec_member]
        # 大额特殊项自动打标：单笔>=3000 且非 房租/保险/社保 类（2026-08-25 规则）
        if r["amount_cents"] >= 300000 and (r["category"] or "其他") not in ("居住", "保险"):
            tags.append("大额")
        L.add_record(conn, {
            "date": r["date"], "amount_cents": r["amount_cents"], "kind": r["kind"],
            "category": r["category"] or "其他", "merchant": r.get("merchant"),
            "remark": r.get("remark", ""), "member": rec_member,
            "source": "import", "channel": "import", "external_id": r["external_id"],
            "tags": tags,
        })
        imported += 1
    return {"imported": imported, "dup": skipped_dup}


# ── CLI ──────────────────────────────────────────────────────────────────────
def cmd_list(args):
    files = sorted(UPLOAD_DIR.glob("*")) if UPLOAD_DIR.exists() else []
    bills = [p for p in files if p.is_file() and detect_platform(p.name) or
             (p.is_file() and p.suffix.lower() in (".xlsx", ".csv"))]
    print("上传目录：{}".format(UPLOAD_DIR))
    if not bills:
        print("（无可识别的账单文件）")
        return
    for p in bills:
        plat = detect_platform(p.name) or "未知"
        print("  [{}] {:<60} {:>8} KB".format(plat, p.name, p.stat().st_size // 1024))


def cmd_preview(args):
    path = Path(args.file)
    records, skipped = normalize_all(str(path))
    conn = L.connect()
    try:
        prev = build_preview(records, skipped, conn)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(prev, ensure_ascii=False, indent=2))
        return
    print("文件：{}".format(path.name))
    print("解析：共 {} 行 → 可导入 {} 笔，跳过 {} 行，与库重复 {} 笔".format(
        prev["total"] + prev["skipped"] + prev["dup"], prev["total"] - prev["dup"],
        prev["skipped"], prev["dup"]))
    print("跳过原因：{}".format(prev["skip_reasons"] or "无"))
    print(chr(10) + "待导入分类分布（笔数 / 金额元）：")
    for x in prev["by_kind_cat"]:
        print("  {:<8} {:<6} {:>4} 笔  {:>10,.2f} 元".format(
            x["kind"], x["category"], x["count"], x["amount_yuan"]))
    if prev["unmatched_expense_count"]:
        print(chr(10) + "⚠ 待人工分类的支出 {} 笔（将归入「其他」，导入后可批量改）：".format(prev["unmatched_expense_count"]))
        for u in prev["unmatched_expense"][:25]:
            print("   {} {:.2f}元 {} | {}".format(u["date"][5:], u["amount"], u["merchant"], u["product"]))
        if prev["unmatched_expense_count"] > 25:
            print("   …… 共 {} 笔支出，其余略".format(prev["unmatched_expense_count"]))
    if prev.get("unmatched_rest"):
        print("另有 {} 笔收入/转账归「其他」（理财收益、退款、代付等，备注里有原始信息）。".format(prev["unmatched_rest"]))
    print(chr(10) + "说明：中性/理财类转转账不计收支；退款按收入冲减；全部记在「本人」名下。")


def cmd_import(args):
    path = Path(args.file)
    records, skipped = normalize_all(str(path))
    conn = L.connect()
    try:
        prev = build_preview(records, skipped, conn)
        if not args.yes:
            print("预览模式（未入库）。确认请加 --yes 再执行：")
            cmd_preview(args)
            return
        result = do_import(records, conn, member=args.member)
    finally:
        conn.close()
    if args.json:
        print(json.dumps({"skipped": prev["skipped"], "skip_reasons": prev["skip_reasons"],
                          **result}, ensure_ascii=False))
        return
    print("导入完成：新增 {} 笔，跳过重复 {} 笔，解析跳过 {} 行（{}）".format(
        result["imported"], result["dup"], prev["skipped"], prev["skip_reasons"] or "无"))
    print("提示：未匹配分类的记录可在面板里按「其他」批量改分类；有需要可指定 --member 归属成员。")


def main():
    p = argparse.ArgumentParser(prog="import_bills", description="微信/支付宝账单导入")
    sub = p.add_subparsers(dest="cmd")
    sp = sub.add_parser("list"); sp.set_defaults(fn=cmd_list)
    sp = sub.add_parser("preview"); sp.add_argument("file"); sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_preview)
    sp = sub.add_parser("import"); sp.add_argument("file"); sp.add_argument("--yes", action="store_true")
    sp.add_argument("--member", default="本人"); sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_import)
    args = p.parse_args()
    if not hasattr(args, "fn"):
        p.print_help()
        sys.exit(2)
    try:
        args.fn(args)
    except ValueError as e:
        print("error: {}".format(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
