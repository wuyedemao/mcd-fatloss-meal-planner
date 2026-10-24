"""自测：不联网、不需要 Token，验证解析与计算链路。

    python scripts/selftest.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_fatloss import (  # noqa: E402
    Item,
    apply_pricing,
    body_metrics,
    build_coupon_index,
    build_plans,
    build_price_index,
    classify,
    kcal_window,
    load_fixture,
    meal_budget,
    mifflin_st_jeor,
    normalize_items,
    safe_deficit_target,
)
from toon import toon_decode, toon_rows  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print("  ok   %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s %s" % (name, detail))


def test_toon() -> None:
    print("[TOON 解析]")
    text = """foods[3]{code,name,energyKcal,protein,fat}:
  1001,巨无霸,510,26,25
  1002,麦香鱼,340,15,15
  1003,板烧鸡腿堡,400,24,15
meta:
  source: mcd-mcp
"""
    data = toon_decode(text)
    check("表头 + 行能被解码", bool(data) and len(data["foods"]) == 3)
    check("数值被还原成数字", data["foods"][0]["energyKcal"] == 510)
    check("嵌套标量被保留", data.get("meta", {}).get("source") == "mcd-mcp")
    check("中文值不被破坏", data["foods"][2]["name"] == "板烧鸡腿堡")

    rows = toon_rows("随便一段前言\nlist[2]{a,b}:\n  1,x\n  2,y\n")
    check("能从噪声文本里抽出表格", len(rows) == 2 and rows[1]["b"] == "y")
    check("完全无法解析时返回 None", toon_decode("hello world") is None)


def test_normalize() -> None:
    print("[数据归一化]")
    payload = {"data": {"list": [
        {"mealCode": "9001", "mealName": "测试堡", "energyKcal": "480", "protein": "22g",
         "fat": "20", "carbohydrate": "40", "sodium": "700mg"},
    ]}}
    items = normalize_items(payload)
    check("能从 data.list 里挖出餐品", len(items) == 1)
    if items:
        item = items[0]
        check("字段别名映射正确", item.code == "9001" and item.kcal == 480.0)
        check("带单位的字符串被清洗", item.protein == 22.0 and item.sodium == 700.0)

    check("主食识别", classify("双层吉士汉堡") == "main")
    check("主食优先于小食（含「鸡」）", classify("麦辣鸡腿堡") == "main")
    check("小食识别", classify("麦乐鸡（5块）") == "side")
    check("饮品识别", classify("零度可口可乐（中）") == "drink")
    check("甜品识别", classify("奥利奥麦旋风") == "dessert")


def test_energy() -> None:
    print("[能量学与护栏]")
    bmr = mifflin_st_jeor("male", 30, 180, 80)
    check("Mifflin-St Jeor 数值正确（男）", abs(bmr - 1780.0) < 0.01, f"got {bmr}")
    bmr_f = mifflin_st_jeor("female", 30, 165, 55)
    check("Mifflin-St Jeor 数值正确（女）", abs(bmr_f - 1270.25) < 0.01, f"got {bmr_f}")

    m = body_metrics("male", 28, 178, 82, "light")
    check("BMI 计算正确", abs(m["bmi"] - 25.9) < 0.05, f"got {m['bmi']}")

    normal = safe_deficit_target("male", 28, 178, 82, "light", 0.005)
    check("正常参数下产生缺口", normal["deficit_kcal"] > 200)
    check("正常参数下不触发护栏", not normal["notes"])

    aggressive = safe_deficit_target("male", 28, 178, 82, "light", 0.05)
    check("过激减重被压到 1%", any("1%" in n for n in aggressive["notes"]))

    floor_case = safe_deficit_target("female", 26, 163, 58, "sedentary", 0.01)
    check("不会跌破安全热量下限", floor_case["target_kcal"] >= int(1200))
    check("跌破下限时有提示", any("下限" in n for n in floor_case["notes"]))

    underweight = safe_deficit_target("female", 22, 165, 45, "sedentary", 0.01)
    check("BMI 过低时拦截减脂", underweight["blocked"] is True and underweight["deficit_kcal"] == 0)

    share = meal_budget(2000, "lunch")
    check("午餐占日摄入 35%", share["budget_kcal"] == 700)
    low, high = kcal_window(700, 0.12)
    check("热量窗口下沿低于上沿", low < 700 < high)


def test_plans() -> None:
    print("[组合搜索]")
    items, prices, coupons, _ = load_fixture()
    check("演示数据可加载", len(items) >= 20)

    budget = meal_budget(2000, "lunch")
    plans = build_plans(items, budget["budget_kcal"])
    check("能产出推荐", len(plans) >= 2)
    low, high = kcal_window(budget["budget_kcal"])
    check("所有推荐都落在热量窗口内", all(low <= p.kcal <= high for p in plans),
          str([p.kcal for p in plans]))
    check("默认不推甜点", all(not any(i.category == "dessert" for i in p.items) for p in plans))
    check("默认不推含糖饮料", all(
        not any(i.category == "drink" and i.kcal >= 100 and i.protein < 2 for i in p.items) for p in plans))
    check("每条推荐都有主食", all(any(i.category == "main" for i in p.items) for p in plans))
    check("推荐之间主食不重复", len({tuple(i.code for i in p.items if i.category == "main") for p in plans}) == len(plans))
    check("热量不超单餐脂肪上限", all(p.fat <= 32 for p in plans))
    check("每条推荐都给出理由", all(p.reasons for p in plans))

    with_dessert = build_plans(items, budget["budget_kcal"], allow_dessert=True, top=8)
    check("显式允许后甜点可进候选", any(any(i.category == "dessert" for i in p.items) for p in with_dessert))

    tight = build_plans(items, 320)
    check("预算过窄时不会崩溃", isinstance(tight, list))


def test_pricing() -> None:
    print("[价格与优惠券]")
    raw = {"coupons": [{"couponId": "C1", "productCode": "1001", "couponPrice": 1290,
                         "couponName": "巨无霸单品券"},
                        {"couponId": "C2", "productCode": "1001", "couponPrice": 1590,
                         "couponName": "巨无霸超值券"}]}
    index = build_coupon_index(raw)
    check("券同时建立名称索引", index["by_name"].get("巨无霸单品券") == 1290)
    check("同一商品取最低券价", index["by_code"].get("1001") == 1290)

    menu = build_price_index({"list": [{"code": "1001", "price": 2450}]})
    check("菜单价（分）解析正确", menu.get("1001") == 2450)

    items = [Item(code="1001", name="巨无霸", kcal=510, protein=26, fat=25, carb=45, sodium=950)]
    plans = build_plans(items, 510, top=1, tolerance=0.2)
    if not plans:
        plans = [type("P", (), {"items": items, "codes": ["1001"], "price_cents": None, "saved_cents": 0})()]
    apply_pricing(plans, menu, index)
    check("用券后价格下降并记下省额", plans[0].price_cents == 1290 and plans[0].saved_cents == 1160)

    missing = build_plans(items, 510, top=1, tolerance=0.2)
    if missing:
        apply_pricing(missing, {}, {})
        check("价格缺失时不瞎猜", missing[0].price_cents is None)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_toon()
    test_normalize()
    test_energy()
    test_plans()
    test_pricing()
    print()
    if FAILED:
        print("失败 %d 项：%s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("全部通过：%d 项检查" % PASSED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
