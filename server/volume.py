"""体积估算：按类别先验（单件典型体积 mL）× 数量；宿管/超管可逐件修正。
精度为"粗略参考"，用于冰箱占用容量估计。类别表需与 pi/classify.py CATEGORIES 保持一致。
"""

CATEGORY_VOL_ML = {
    "水果": 200,
    "蔬菜": 300,
    "奶制品": 200,
    "肉禽": 350,
    "水产": 300,
    "饮料": 500,
    "零食熟食": 150,
    "餐盒剩菜": 800,
    "其他": 300,
}
DEFAULT_VOL_ML = 300

STOCK_STATUSES = ("active", "pending_claim")   # 都算物理在库


def est_ml(item: dict) -> int:
    manual = item.get("volume_ml")
    if manual:
        return int(manual)
    per = CATEGORY_VOL_ML.get((item.get("category") or "").strip(), DEFAULT_VOL_ML)
    qty = max(int(item.get("quantity") or 1), 1)
    return per * qty


def attach(items: list[dict]) -> list[dict]:
    for it in items:
        it["vol_est_ml"] = est_ml(it)
        it["vol_manual"] = bool(it.get("volume_ml"))
    return items
