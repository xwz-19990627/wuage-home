# wuage-home · 数据模型 v2（v0.1.5 起）

> SQLite 单库 `$WUAGE_DATA/ledger.db`（当前 `/root/wuage/data/ledger.db`），迁移 = 拷贝数据根 `/root/wuage`。schema 以 `scripts/ledger.py` 为准。

## 表结构（v2）

- **families**：家庭（`id, name, created_at`），默认“我的家庭”。
- **members**：成员（`id, family_id, name, relation, is_active`），种子=“本人”；被交易引用禁止删除。
- **accounts**：资金账户（`id, family_id, name, type`），种子=“现金”，v1.0 仅默认账户。
- **categories**：分类（`id, family_id, name UNIQUE, type, parent_id(预留二级), is_system, icon, color, sort_order, is_archived`）。
  种子 10 项：餐饮/交通/医疗/购物/教育/娱乐/居住/通讯/人情/其他；
  **规则**：手动新增（LLM 不自动建类）；可改名；被交易引用禁止删除；种子与“其他”不可删。
- **transactions**：财务事件（`id, family_id, account_id, member_id, category_id, amount_cents,
  kind, date, remark, merchant, source(manual/ai_parsed/ai_corrected), channel(manual/voice/wechat/import),
  raw_text, ai_confidence, corrected_from, tags, created_at`）。
- **drafts**：AI 草稿（`id, family_id, data_json, created_at, expired_at(+24h)`）。
- **legacy_ledger**：旧表备份（v1 数据迁移后保留）。

## 迁移（v1 → v2）

```bash
python3 scripts/ledger.py init      # 建表 + 种子
python3 scripts/ledger.py migrate   # 旧表(ledger/legacy_ledger) → transactions，幂等
```

分类映射：医疗健康→医疗、育儿教育→教育；未知分类→其他；旧 source(voice/wechat/import)→channel，source=manual。
