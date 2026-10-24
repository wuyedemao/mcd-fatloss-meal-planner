"""麦麦减脂配餐助手 · 核心算法与命令行入口。

设计原则：
1. 热量预算由「个人参数 → BMR → TDEE → 安全缺口」推出来，不是拍脑袋定一个 500 kcal。
2. 缺口有硬护栏：日摄入不低于 max(BMR×1.1, 男 1500 / 女 1200)，周减重不超过体重的 1%。
3. 选套餐是「在预算区间内做多目标搜索」，蛋白质优先、钠和脂肪设上限，而不是让模型随手挑一个堡。
4. 数据全部来自麦当劳 MCP；离线模式只用于跑通流程和自测，输出一律标注为演示数据。

用法见 README。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict

from toon import toon_decode, toon_rows

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ACTIVITY_FACTOR = {
    "sedentary": 1.2,      # 久坐，几乎不运动
    "light": 1.375,        # 每周 1-3 次轻量运动
    "moderate": 1.55,      # 每周 3-5 次中等强度
    "active": 1.725,       # 每周 6-7 次高强度
}

MEAL_SHARE = {
    "breakfast": 0.25,
    "lunch": 0.35,
    "dinner": 0.30,
    "snack": 0.10,
}

MEAL_LABEL = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}

KCAL_PER_KG_FAT = 7700

# 单餐上限（一份套餐内的硬约束）
MAX_MEAL_FAT_G = 32.0
MAX_MEAL_SODIUM_MG = 1100.0

DISCLAIMER = (
    "提醒：以上结果为基于麦当劳餐品营养数据的热量与营养估算，仅供参考，不构成医疗或营养建议。"
    "减脂期间建议搭配蔬菜水果、保证饮水量；如你有慢性病、孕期或进食障碍史，请先咨询医生或注册营养师。"
    "餐品信息、价格与供应状态以麦当劳官方渠道为准。"
)

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Item:
    code: str
    name: str
    kcal: float
    protein: float
    fat: float
    carb: float
    sodium: float
    calcium: float = 0.0
    category: str = "other"
    price_cents: int | None = None

    @property
    def protein_density(self) -> float:
        """每 100 kcal 能换到多少克蛋白质——减脂期最该看的指标。"""
        return (self.protein / self.kcal * 100) if self.kcal > 0 else 0.0


@dataclass
class Plan:
    items: list[Item]
    kcal: float
    protein: float
    fat: float
    carb: float
    sodium: float
    score: float
    price_cents: int | None = None
    saved_cents: int = 0
    priced_items: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return " + ".join(i.name for i in self.items)

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.items]


# ---------------------------------------------------------------------------
# 数据归一化：MCP 返回的字段名并不完全统一，这里做宽容映射
# ---------------------------------------------------------------------------


def _num(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else 0.0


def _pick(record: dict, *candidates: str, contains: tuple[str, ...] = ()) -> object:
    lowered = {str(k).lower(): v for k, v in record.items()}
    for name in candidates:
        if name.lower() in lowered and lowered[name.lower()] not in (None, ""):
            return lowered[name.lower()]
    for key, value in lowered.items():
        if any(token in key for token in contains) and value not in (None, ""):
            return value
    return None


_RECORD_KEYS = ("foods", "list", "data", "items", "records", "result", "rows", "mealList", "products")


def find_records(payload) -> list[dict]:
    """从各种包裹层级里挖出「餐品列表」。"""
    if payload is None:
        return []
    if isinstance(payload, list):
        if payload and all(isinstance(x, dict) for x in payload):
            return payload
        for element in payload:
            found = find_records(element)
            if found:
                return found
        return []
    if isinstance(payload, dict):
        for key in _RECORD_KEYS:
            for real_key, value in payload.items():
                if str(real_key).lower() == key and isinstance(value, (list, dict)):
                    found = find_records(value)
                    if found:
                        return found
        for value in payload.values():
            if isinstance(value, (list, dict)):
                found = find_records(value)
                if found:
                    return found
    return []


def smart_parse(value, depth: int = 0):
    """把「JSON / TOON / 二次包装的字符串」统一解成 Python 对象。

    麦当劳 MCP 的返回可能是三层任一形态：
    1. 裸 JSON 对象；
    2. `{"success":true,"code":200,"data":{...}}` 这种网关信封，真正的数据在 data 里；
    3. data 里放着一整段 TOON 文本（不是 JSON）。
    线上第一版只处理了第 1 种，遇到第 2/3 种就会解出 0 条餐品——这里做递归拆包。
    """
    if depth > 8:
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        if text[0] in "{[":
            try:
                return smart_parse(json.loads(text), depth + 1)
            except json.JSONDecodeError:
                pass
        decoded = toon_decode(text)
        if decoded is not None and find_records(decoded):
            return smart_parse(decoded, depth + 1)
        rows = toon_rows(text)
        if rows:
            return rows
        return value
    if isinstance(value, dict):
        return {k: smart_parse(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [smart_parse(v, depth + 1) for v in value]
    return value


def records_of(payload) -> list[dict]:
    """先拆包再找列表，线上和离线都走这一条路径。"""
    return find_records(smart_parse(payload))


def normalize_items(payload) -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()
    for index, record in enumerate(records_of(payload)):
        if not isinstance(record, dict):
            continue
        name = _pick(record, "name", "mealName", "productName", "title", "foodName", contains=("餐品名", "名称"))
        if not name:
            continue
        code = _pick(record, "code", "mealCode", "productCode", "skuCode", "sku", "itemCode", "id")
        code = str(code) if code not in (None, "") else f"AUTO{index:03d}"
        if code in seen:
            continue
        seen.add(code)

        kcal = _num(_pick(record, "energyKcal", "kcal", "calories", "energy", contains=("kcal", "calorie", "能量", "热量", "卡")))
        item = Item(
            code=code,
            name=str(name).strip(),
            kcal=kcal,
            protein=_num(_pick(record, "protein", "proteinG", contains=("protein", "蛋白"))),
            fat=_num(_pick(record, "fat", "fatG", contains=("fat", "脂肪"))),
            carb=_num(_pick(record, "carbohydrate", "carb", "carbs", contains=("carb", "碳水"))),
            sodium=_num(_pick(record, "sodium", "sodiumMg", "na", contains=("sodium", "钠"))),
            calcium=_num(_pick(record, "calcium", "calciumMg", contains=("calcium", "钙"))),
        )
        # 热量缺失（=0）的餐品会被当成「零卡」误导用户，直接丢弃；
        # 但零度可乐、无糖饮品、矿泉水确实是 0 kcal，需要保留。
        if item.kcal <= 0 and not any(w in item.name for w in ("零度", "无糖", "矿泉水", "纯净水")):
            continue
        item.category = classify(item.name)
        items.append(item)
    return items


_DRINK_WORDS = ("可乐", "雪碧", "芬达", "咖啡", "拿铁", "奶铁", "美式", "玛奇朵", "卡布奇诺", "雪冰",
                "黑巧", "抹茶", "阿芙佳朵", "燕麦奶", "朱古力", "奶茶", "牛奶", "豆浆", "橙", "果汁",
                "矿泉水", "纯净水", "茶", "气泡", "零度", "无糖", "椰子水", "怡泉", "美汁源", "麦旋酷", "+C")
_DESSERT_WORDS = ("派", "冰淇淋", "新地", "麦旋风", "甜筒", "圣代", "布朗尼", "蛋糕", "曲奇",
                  "奶冻", "拉明顿", "爆珠")
_SIDE_WORDS = ("薯条", "薯饼", "玉米", "沙拉", "鸡块", "麦乐鸡", "鸡翅", "V翅", "鸡排", "鸡柳",
               "洋葱圈", "汤", "苹果", "蔬菜", "小食", "脆汁鸡", "鸡球", "油条", "香肠", "那么大")
_MAIN_WORDS = ("堡", "霸", "麦香鸡", "麦香鱼", "卷", "饭", "粥", "麦满分", "帕尼尼", "贝果",
               "bagel", "三明治", "意面")

# 早餐限定：午晚餐推荐里出现麦满分、油条会很出戏
_BREAKFAST_ONLY = ("麦满分", "粥", "油条", "薯饼", "炒蛋")


def filter_by_meal(items: list[Item], meal: str) -> list[Item]:
    """午/晚餐不推早餐限定品，避免出现「午餐吃猪柳蛋麦满分」这种不合场景的推荐。"""
    if meal == "breakfast":
        return items
    return [i for i in items if not any(w in i.name for w in _BREAKFAST_ONLY)]


def classify(name: str) -> str:
    """按名称归类，顺序即优先级：甜品/饮品先判，避免「麦旋风」被当成主食。"""
    for word in _DESSERT_WORDS:
        if word in name:
            return "dessert"
    for word in _DRINK_WORDS:
        if word in name:
            return "drink"
    for word in _MAIN_WORDS:  # 主食优先于小食：「麦辣鸡腿堡」含「鸡」但属于主食
        if word in name:
            return "main"
    for word in _SIDE_WORDS:
        if word in name:
            return "side"
    return "other"


# ---------------------------------------------------------------------------
# 能量学：BMR → TDEE → 安全缺口 → 单餐预算
# ---------------------------------------------------------------------------


def mifflin_st_jeor(sex: str, age: int, height_cm: float, weight_kg: float) -> float:
    """Mifflin-St Jeor 公式，目前临床与运动营养领域最常用的基础代谢估算。"""
    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age
    return base + (5.0 if sex == "male" else -161.0)


def body_metrics(sex: str, age: int, height_cm: float, weight_kg: float, activity: str = "light") -> dict:
    bmr = mifflin_st_jeor(sex, age, height_cm, weight_kg)
    tdee = bmr * ACTIVITY_FACTOR.get(activity, 1.375)
    bmi = weight_kg / ((height_cm / 100.0) ** 2)
    return {"bmr": round(bmr), "tdee": round(tdee), "bmi": round(bmi, 1)}


def safe_deficit_target(
    sex: str,
    age: int,
    height_cm: float,
    weight_kg: float,
    activity: str = "light",
    weekly_loss_pct: float = 0.005,
    explicit_target: float | None = None,
) -> dict:
    """给出日摄入目标与所有护栏调整，返回结构里带 notes 便于向用户解释。"""
    metrics = body_metrics(sex, age, height_cm, weight_kg, activity)
    bmr, tdee, bmi = metrics["bmr"], metrics["tdee"], metrics["bmi"]
    notes: list[str] = []

    if bmi < 18.5:
        return {
            **metrics,
            "target_kcal": round(tdee),
            "deficit_kcal": 0,
            "weekly_loss_pct": 0.0,
            "notes": ["你的 BMI 为 %.1f，已低于正常范围，不建议继续减脂。已改为按维持热量给出配餐。" % bmi],
            "blocked": True,
        }

    floor = max(bmr * 1.1, 1500.0 if sex == "male" else 1200.0)

    pct = float(weekly_loss_pct)
    if pct > 0.01:
        notes.append("每周减重 %.1f%% 超过安全上限（体重的 1%%），已下调至 1%%。" % (pct * 100))
        pct = 0.01
    if pct < 0:
        pct = 0.0

    daily_deficit = weight_kg * pct * KCAL_PER_KG_FAT / 7.0
    cap = tdee * 0.25  # 缺口不超过 TDEE 的 25%
    if daily_deficit > cap:
        notes.append("按每周 %.1f%% 计算出的缺口过大，已限制为 TDEE 的 25%%。" % (pct * 100))
        daily_deficit = cap

    target = tdee - daily_deficit
    if explicit_target:
        target = float(explicit_target)
        notes.append("使用了手动指定的日摄入目标 %d kcal。" % round(target))

    if target < floor:
        notes.append(
            "目标热量低于安全下限（BMR×1.1 与 %d kcal 取大值），已上调至 %d kcal。极端节食会掉肌肉、影响代谢。"
            % (int(1500 if sex == "male" else 1200), round(floor))
        )
        target = floor

    return {
        **metrics,
        "target_kcal": round(target),
        "deficit_kcal": round(tdee - target),
        "weekly_loss_pct": round(pct, 4),
        "notes": notes,
        "blocked": False,
    }


def kcal_window(budget: float, tolerance: float = 0.12) -> tuple[float, float]:
    """单餐可接受的热量区间。下浮比上浮宽松：减脂期吃到略低于预算是安全的。"""
    low = budget * max(0.70, 1 - tolerance - 0.03)
    high = budget * (1 + tolerance * 0.65)
    return low, high


def meal_budget(target_kcal: float, meal: str = "lunch", tolerance: float = 0.12) -> dict:
    share = MEAL_SHARE.get(meal, 0.35)
    budget = target_kcal * share
    budget = max(280.0, min(budget, 1150.0))
    low, high = kcal_window(budget, tolerance)
    return {
        "meal": meal,
        "share": share,
        "budget_kcal": round(budget),
        "low_kcal": round(low),
        "high_kcal": round(high),
        "tolerance": tolerance,
    }


# ---------------------------------------------------------------------------
# 组合搜索
# ---------------------------------------------------------------------------


def _combo_stats(items: list[Item]) -> dict:
    return {
        "kcal": sum(i.kcal for i in items),
        "protein": sum(i.protein for i in items),
        "fat": sum(i.fat for i in items),
        "carb": sum(i.carb for i in items),
        "sodium": sum(i.sodium for i in items),
    }


def _score(stats: dict, budget: float) -> float:
    kcal = max(stats["kcal"], 1.0)
    fit = 1.0 - min(abs(kcal - budget) / budget, 1.0)
    protein = stats["protein"]
    density = protein / kcal * 100

    protein_score = min(protein / 35.0, 1.15)
    density_score = min(density / 7.5, 1.15)
    sodium_penalty = max(stats["sodium"] - 800.0, 0.0) / 800.0
    fat_penalty = max(stats["fat"] - 22.0, 0.0) / 20.0

    return (
        2.2 * fit
        + 0.95 * protein_score
        + 0.55 * density_score
        - 0.60 * sodium_penalty
        - 0.50 * fat_penalty
    )


def _is_sugary_drink(item: Item) -> bool:
    """含糖饮料的启发式判断：热量不低、又几乎不提供蛋白质。"""
    return item.category == "drink" and item.kcal >= 100 and item.protein < 2.0


def _explain(plan_items: list[Item], stats: dict, budget: float) -> list[str]:
    reasons: list[str] = []
    gap = stats["kcal"] - budget
    if abs(gap) <= budget * 0.05:
        reasons.append("热量和你的单餐预算几乎对齐（差 %+d kcal）" % round(gap))
    elif gap < 0:
        reasons.append("热量比预算低 %d kcal，留出了弹性" % round(-gap))
    else:
        reasons.append("热量比预算高 %d kcal，可在下一餐找补" % round(gap))

    reasons.append("蛋白质 %.0f g，约 %d kcal 里每 100 kcal 换到 %.1f g" % (
        stats["protein"], round(stats["kcal"]), stats["protein"] / max(stats["kcal"], 1) * 100))

    main = next((i for i in plan_items if i.category == "main"), None)
    if main:
        reasons.append("主餐选了「%s」（%d kcal / 蛋白质 %.0f g），而不是靠大份薯条和含糖饮料把热量撑满"
                       % (main.name, round(main.kcal), main.protein))

    drinks = [i for i in plan_items if i.category == "drink"]
    for drink in drinks:
        if _is_sugary_drink(drink):
            break
        if drink.protein >= 5:
            reasons.append("饮品选了「%s」，%d kcal 里带 %.1f g 蛋白质，比同热量的含糖饮料更值" %
                           (drink.name, round(drink.kcal), drink.protein))
        elif drink.kcal <= 60:
            reasons.append("饮品选了「%s」（%d kcal），把预算留给能提供饱腹感的蛋白质" %
                           (drink.name, round(drink.kcal)))

    if stats["sodium"] > 900:
        reasons.append("注意这餐钠约 %d mg，已经接近单餐建议上限，当天其余两餐尽量清淡" % round(stats["sodium"]))
    return reasons


def build_plans(
    items: list[Item],
    budget_kcal: float,
    top: int = 3,
    max_items: int = 3,
    tolerance: float = 0.12,
    allow_dessert: bool = False,
) -> list[Plan]:
    """在热量区间内枚举「主食(+小食)(+饮品)」组合，按综合分排序。

    组合空间做了剪枝：小食按蛋白质密度留前 16，饮品按热量从低到高留前 12。
    这样 40×17×13 的规模在纯 Python 里也是毫秒级。

    容差是不对称的：宁可吃到预算的 85%，也不建议吃到 108% 以上——
    减脂期超出比略欠更麻烦，第二天容易用「反正都超了」把自己说服。
    """
    mains = [i for i in items if i.category == "main"][:60]
    side_pool = [i for i in items if i.category == "side"]
    if allow_dessert:
        side_pool += [i for i in items if i.category == "dessert"]
    sides = sorted(side_pool, key=lambda i: (-i.protein_density, i.kcal))[:16]
    drinks = sorted([i for i in items if i.category == "drink"], key=lambda i: i.kcal)[:12]

    low, high = kcal_window(budget_kcal, tolerance)
    min_protein = max(18.0, budget_kcal * 0.035)

    candidates: list[tuple[float, list[Item], dict]] = []

    def consider(combo: list[Item]) -> None:
        stats = _combo_stats(combo)
        if not (low <= stats["kcal"] <= high):
            return
        if stats["fat"] > MAX_MEAL_FAT_G or stats["sodium"] > MAX_MEAL_SODIUM_MG:
            return
        if stats["protein"] < min_protein:
            return
        score = _score(stats, budget_kcal)
        if any(i.category == "dessert" for i in combo):
            score -= 0.35  # 甜点只在用户明确要求时进候选，且不占优势
        if any(_is_sugary_drink(i) for i in combo):
            score -= 0.45  # 含糖饮料占掉 200 kcal 却换不到蛋白质，减脂期不该排在前面
        candidates.append((score, combo, stats))

    for main in mains:
        consider([main])
        if max_items >= 2:
            for side in sides:
                consider([main, side])
                for drink in drinks:
                    consider([main, side, drink])
            for drink in drinks:
                consider([main, drink])

    candidates.sort(key=lambda x: -x[0])

    # 同一个主食只保留最优解，保证推荐有变化而不是三个都是巨无霸
    picked: list[Plan] = []
    used_mains: set[str] = set()
    for score, combo, stats in candidates:
        main_code = next((i.code for i in combo if i.category == "main"), combo[0].code)
        if main_code in used_mains:
            continue
        used_mains.add(main_code)
        picked.append(
            Plan(
                items=combo,
                kcal=round(stats["kcal"], 1),
                protein=round(stats["protein"], 1),
                fat=round(stats["fat"], 1),
                carb=round(stats["carb"], 1),
                sodium=round(stats["sodium"], 1),
                score=round(score, 3),
                reasons=_explain(combo, stats, budget_kcal),
            )
        )
        if len(picked) >= top:
            break

    if not picked and mains:  # 区间太窄时放宽一次，避免空手而归
        loose = [i for i in items if 0 < i.kcal <= high * 1.1 and i.category == "main"]
        if loose:
            best = min(loose, key=lambda i: abs(i.kcal - budget_kcal))
            stats = _combo_stats([best])
            picked.append(
                Plan(
                    items=[best],
                    kcal=round(stats["kcal"], 1),
                    protein=round(stats["protein"], 1),
                    fat=round(stats["fat"], 1),
                    carb=round(stats["carb"], 1),
                    sodium=round(stats["sodium"], 1),
                    score=round(_score(stats, budget_kcal), 3),
                    reasons=["放宽热量区间后仍然只有这一项落在预算附近；建议当天用其他餐次补齐蛋白质"],
                )
            )
    return picked


# ---------------------------------------------------------------------------
# 价格与优惠券
# ---------------------------------------------------------------------------


_COMBO_HINTS = ("单人餐", "多人餐", "拼盘", "随心配", "乐园", "套餐")


def _walk_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_dicts(value)


def collect_menu_codes(payload) -> list[str]:
    """从 query-meals 里按「单品 → 饮品 → 组合餐」的顺序取出商品编码。

    query-meals 只返回 code 和 tags，没有菜名也没有价格，所以排序很重要：
    详情接口有调用上限，得先保证主食和小食被覆盖，饮品次之，套餐放最后。
    """
    primary: list[str] = []
    secondary: list[str] = []
    rest: list[str] = []
    for node in _walk_dicts(smart_parse(payload)):
        meals = node.get("meals")
        cat = node.get("name")
        if not isinstance(meals, list) or not cat:
            continue
        target = rest
        if any(w in str(cat) for w in ("饮品", "咖啡", "饮料")):
            target = secondary
        elif not any(w in str(cat) for w in _COMBO_HINTS):
            target = primary
        for meal in meals:
            if isinstance(meal, dict) and meal.get("code"):
                target.append(str(meal["code"]))

    ordered, seen = [], set()
    for code in primary + secondary + rest:
        if code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def parse_meal_detail(payload) -> dict:
    """从餐品详情里抠出「名称 + 价格(分)」，抠不到就返回空字典。"""
    result: dict = {}
    for node in _walk_dicts(smart_parse(payload)):
        name = _pick(node, "productName", "mealName", "title", "name", contains=("name",))
        price = _pick(node, "salePrice", "price", "originalPrice", "currentPrice", "amount")
        if not name:
            continue
        result.setdefault("name", str(name))
        if price in (None, ""):
            continue
        value = _num(price)
        if value <= 0:
            continue
        cents = int(round(value * 100)) if value < 100 else int(round(value))
        return {"name": str(name), "price_cents": cents}
    return result


def _norm_name(value: str) -> str:
    """菜名归一化：去掉空格、全角括号、书名号，便于跨接口按名字对齐。"""
    return re.sub(r"[\s\u3000（）()\[\]【】「」『』\"'“”]", "", str(value)).lower()


_COUPON_PRICE = re.compile(r"(\d+(?:\.\d+)?)\s*元\s*(.+)$")


def parse_coupon_name(name: str) -> tuple[float | None, str | None]:
    """从券名里抽出「券价 + 商品名」。

    麦当劳 available-coupons 不返回价格字段，价格写在 couponName 里：
        「11.9元麦辣鸡腿堡」 -> (11.9, 麦辣鸡腿堡)
        「免费脆薯饼」        -> (0.0, 脆薯饼)
        「19.9元王牌炸鸡任选2」-> (19.9, 王牌炸鸡任选2)  ← 组合券，对不上单品
    """
    text = str(name or "").strip()
    if not text:
        return None, None
    if text.startswith("免费"):
        product = text[2:].strip()
        return 0.0, product or None
    match = _COUPON_PRICE.search(text)
    if match:
        return float(match.group(1)), match.group(2).strip() or None
    return None, None


def build_coupon_index(payload) -> dict:
    """把券列表压成两张表：按商品编码、按商品名称。

    线上 list-nutrition-foods 不返回商品编码，只返 productName，
    所以必须保留名称索引，否则永远算不出券后价。
    """
    by_code: dict[str, int] = {}
    by_name: dict[str, int] = {}
    records = records_of(payload) or []
    for record in records:
        if not isinstance(record, dict):
            continue
        code = _pick(record, "productCode", "code", "mealCode", "skuCode", "couponProductCode")
        name = _pick(record, "productName", "couponName", "name", "title", "mealName")
        price = _pick(record, "couponPrice", "price", "salePrice", "amount", "discountPrice")
        name_value = str(name) if name else ""
        if price in (None, ""):
            parsed_price, parsed_product = parse_coupon_name(name_value)
            if parsed_price is not None:
                price = parsed_price
                if parsed_product:
                    name = parsed_product
            else:
                continue
        value = _num(price)
        # 官方说明价格单位为「分」；小于 100 的数值按「元」兜底，避免不同接口单位不一致
        cents = int(round(value * 100)) if value < 100 else int(round(value))
        if code not in (None, ""):
            key = str(code)
            if key not in by_code or cents < by_code[key]:
                by_code[key] = cents
        if name:
            key = _norm_name(name)
            if key and (key not in by_name or cents < by_name[key]):
                by_name[key] = cents
    return {"by_code": by_code, "by_name": by_name}


def build_price_index(payload) -> dict:
    """菜单价索引，同时按商品编码和归一化名称建表。

    营养表只给 productName 不给编码，所以名称索引是拿到价格的唯一桥梁。
    价格单位是分；小于 100 的值按元兜底。
    """
    index: dict[str, int] = {}
    for record in records_of(payload) or []:
        if not isinstance(record, dict):
            continue
        code = _pick(record, "code", "mealCode", "productCode", "skuCode", "itemCode")
        name = _pick(record, "name", "mealName", "productName", "title")
        price = _pick(record, "price", "salePrice", "originalPrice", "amount", "currentPrice")
        if price in (None, ""):
            continue
        value = _num(price)
        if value <= 0:
            continue
        cents = int(round(value * 100)) if value < 100 else int(round(value))
        if code not in (None, ""):
            index[str(code)] = cents
        if name:
            index.setdefault(_norm_name(name), cents)
    return index


def apply_pricing(plans: list[Plan], price_index: dict, coupon_index: dict) -> None:
    """能算多少算多少：只对拿到价格的商品累加，并记录覆盖了几项。

    营养接口不返回商品编码，只能靠名称与券对齐；拿不全时不编总价，
    而是标明「3 项中 2 项有价」，比报一个假数字诚实。
    """
    by_code = coupon_index.get("by_code", {}) if isinstance(coupon_index, dict) else {}
    by_name = coupon_index.get("by_name", {}) if isinstance(coupon_index, dict) else {}
    for plan in plans:
        total = 0
        saved = 0
        priced = 0
        for item in plan.items:
            norm = _norm_name(item.name)
            menu = price_index.get(item.code) or price_index.get(norm)
            coupon = by_code.get(item.code) or by_name.get(norm)
            if menu is not None and coupon is not None and coupon < menu:
                total += coupon
                saved += menu - coupon
                priced += 1
            elif coupon is not None:
                total += coupon
                priced += 1
            elif menu is not None:
                total += menu
                priced += 1
        if priced:
            plan.price_cents = total
            plan.saved_cents = saved
            plan.priced_items = priced


def _yuan(cents: int | None) -> str:
    return "—" if cents is None else "%.2f 元" % (cents / 100.0)


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def render_markdown(profile: dict, energy: dict, budget: dict, plans: list[Plan], source: str) -> str:
    lines: list[str] = []
    lines.append("## 你的减脂能量账本")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("| --- | --- |")
    lines.append("| 基础代谢 BMR | %d kcal |" % energy["bmr"])
    lines.append("| 日常总消耗 TDEE | %d kcal |" % energy["tdee"])
    lines.append("| 日摄入目标 | %d kcal |" % energy["target_kcal"])
    lines.append("| 预计日缺口 | %d kcal |" % energy["deficit_kcal"])
    lines.append("| BMI | %.1f |" % energy["bmi"])
    lines.append("| 本次餐次预算 | %d kcal（%d–%d） |" % (
        budget["budget_kcal"], budget["low_kcal"], budget["high_kcal"]))
    lines.append("")

    if energy.get("notes"):
        lines.append("**计算说明：**")
        for note in energy["notes"]:
            lines.append("- " + note)
        lines.append("")

    if not plans:
        lines.append("> 没有组合能落在这次的热量区间内。可以放宽餐次、换一家门店，或者把预算提到 %d kcal 以上再试。"
                     % budget["high_kcal"])
        return "\n".join(lines)

    lines.append("## 推荐套餐")
    lines.append("")
    for rank, plan in enumerate(plans, start=1):
        lines.append("### 方案 %d：%s" % (rank, plan.title))
        lines.append("")
        lines.append("| 热量 | 蛋白质 | 脂肪 | 碳水 | 钠 | 价格 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        price_text = _yuan(plan.price_cents)
        if plan.price_cents is not None and plan.priced_items < len(plan.items):
            price_text += "（%d/%d 项有价，其余以门店售价为准）" % (plan.priced_items, len(plan.items))
        if plan.saved_cents:
            price_text += "（用券省 %.2f 元）" % (plan.saved_cents / 100.0)
        lines.append("| %d kcal | %.0f g | %.0f g | %.0f g | %d mg | %s |" % (
            round(plan.kcal), plan.protein, plan.fat, plan.carb, round(plan.sodium), price_text))
        lines.append("")
        gap = round(plan.kcal - budget["budget_kcal"])
        remain = energy["target_kcal"] - round(plan.kcal)
        lines.append("- 相对预算 %+d kcal；吃完这餐，今天还剩约 **%d kcal** 的额度" % (gap, remain))
        for reason in plan.reasons:
            lines.append("- " + reason)
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("数据来源：%s" % source)
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def render_console(profile: dict, energy: dict, budget: dict, plans: list[Plan]) -> str:
    out = []
    out.append("=" * 62)
    out.append("麦麦减脂配餐助手")
    out.append("=" * 62)
    out.append("BMR %d kcal | TDEE %d kcal | 目标 %d kcal | 缺口 %d kcal | BMI %.1f"
               % (energy["bmr"], energy["tdee"], energy["target_kcal"], energy["deficit_kcal"], energy["bmi"]))
    out.append("本次餐次：%s，预算 %d kcal（%d–%d）"
               % (MEAL_LABEL.get(budget["meal"], budget["meal"]), budget["budget_kcal"],
                  budget["low_kcal"], budget["high_kcal"]))
    if energy.get("notes"):
        for note in energy["notes"]:
            out.append("  ! " + note)
    out.append("-" * 62)
    if not plans:
        out.append("没有可用组合，请放宽条件。")
        return "\n".join(out)
    for rank, plan in enumerate(plans, start=1):
        price = _yuan(plan.price_cents)
        if plan.price_cents is not None and plan.priced_items < len(plan.items):
            price += " (%d/%d 项有价)" % (plan.priced_items, len(plan.items))
        if plan.saved_cents:
            price += " (省 %.2f)" % (plan.saved_cents / 100.0)
        out.append("[%d] %s" % (rank, plan.title))
        out.append("    %d kcal | 蛋白 %.0f g | 脂肪 %.0f g | 碳水 %.0f g | 钠 %d mg | %s"
                   % (round(plan.kcal), plan.protein, plan.fat, plan.carb, round(plan.sodium), price))
        out.append("    今日剩余 %d kcal" % (energy["target_kcal"] - round(plan.kcal)))
    out.append("-" * 62)
    out.append(DISCLAIMER)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------


def load_fixture() -> tuple[list[Item], dict, dict, str]:
    here = os.path.dirname(os.path.abspath(__file__))
    fx = os.path.join(here, "fixtures")

    def read(name):
        with open(os.path.join(fx, name), encoding="utf-8") as fh:
            return json.load(fh)

    items = normalize_items(read("nutrition_fixture.json"))
    coupons = build_coupon_index(read("coupons_fixture.json"))
    prices = build_price_index(read("nutrition_fixture.json"))
    return items, prices, coupons, "离线演示数据（fixtures/，非官方实时数据，仅用于流程验证）"


def load_live(store_code: str | None = None, city: str | None = None, keyword: str | None = None,
              max_detail: int = 150):
    from mcp_client import McdMcpClient, McpError  # 延迟导入，离线模式下不需要

    client = McdMcpClient()
    client.initialize()

    raw_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "_raw")

    def fetch(tool: str, args: dict | None = None) -> str:
        """调工具并把原始返回落盘。线上格式一旦变了，有原始样本才能修。"""
        text = client.call_tool(tool, args or {})
        try:
            os.makedirs(raw_dir, exist_ok=True)
            with open(os.path.join(raw_dir, tool + ".txt"), "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass
        return text

    detail_cache_path = os.path.join(raw_dir, "_detail_cache.json")

    def resolve_prices(store: str) -> dict:
        """query-meals 只有编码，营养表只有菜名，靠逐条 query-meal-detail 打通。

        结果缓存到本地，第二次跑直接读缓存，不再重复调用。
        """
        menu_text = fetch("query-meals", {"storeCode": store, "orderType": 1, "beType": 1})
        codes = collect_menu_codes(menu_text)
        if not codes:
            return {}

        cache: dict = {}
        try:
            with open(detail_cache_path, encoding="utf-8") as fh:
                cache = json.load(fh)
        except (OSError, json.JSONDecodeError):
            cache = {}

        index: dict = {}
        fetched = 0
        for code in codes:
            entry = cache.get(code)
            if entry is None:
                if fetched >= max_detail:
                    break
                try:
                    text = client.call_tool(
                        "query-meal-detail",
                        {"storeCode": store, "orderType": 1, "beType": 1, "code": code},
                    )
                except McpError:
                    continue
                if fetched == 0:
                    try:
                        with open(os.path.join(raw_dir, "meal_detail_sample.txt"), "w", encoding="utf-8") as fh:
                            fh.write(text)
                    except OSError:
                        pass
                fetched += 1
                entry = parse_meal_detail(text) or {"_empty": True}
                cache[code] = entry
                if fetched % 25 == 0:
                    sys.stderr.write("[价格] 已拉取 %d/%d 条餐品详情\n" % (fetched, min(len(codes), max_detail)))
                    sys.stderr.flush()

            if not entry or entry.get("_empty"):
                continue
            cents = entry.get("price_cents")
            if cents:
                index[code] = cents
                if entry.get("name"):
                    index[_norm_name(entry["name"])] = cents

        try:
            with open(detail_cache_path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, ensure_ascii=False)
        except OSError:
            pass
        sys.stderr.write("[价格] 已建立 %d 条价格映射\n" % len(index))
        sys.stderr.flush()
        return index

    nutrition_text = fetch("list-nutrition-foods")
    items = normalize_items(nutrition_text)
    if not items:
        raise McpError(
            "list-nutrition-foods 未返回可解析的餐品营养数据。原始返回已保存到 %s，"
            "把这行路径告诉开发者即可定位格式差异。"
            % os.path.join(raw_dir, "list-nutrition-foods.txt")
        )

    coupons_text = ""
    for tool in ("available-coupons", "query-store-coupons"):
        args = {"storeCode": store_code} if (tool == "query-store-coupons" and store_code) else {}
        try:
            coupons_text = fetch(tool, args)
            if records_of(coupons_text):
                break
        except McpError:
            continue

    price_index: dict = {}
    resolved_store = store_code
    try:
        if not resolved_store and city:
            # query-nearby-stores 没有经纬度参数，只能按城市/关键词找
            query = {"beType": 1, "searchType": 2, "city": city}
            if keyword:
                query["keyword"] = keyword
            stores = records_of(fetch("query-nearby-stores", query))
            if stores:
                resolved_store = str(_pick(stores[0], "storeCode", "code", "id") or "")
        if resolved_store:
            # 到店自取：orderType=1 / beType=1，beCode 不要传（传了会报错）
            price_index = resolve_prices(resolved_store)
    except McpError:
        price_index = {}

    source = "麦当劳 MCP 实时数据：list-nutrition-foods / available-coupons%s" % (
        " / query-meals" if price_index else "")
    return items, price_index, build_coupon_index(coupons_text), source


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcd_fatloss",
        description="麦麦减脂配餐助手：按你的减脂目标，从麦当劳实时营养数据里搜一份合适的套餐。",
    )
    parser.add_argument("--sex", choices=["male", "female"], default="male", help="生理性别（影响 BMR 计算）")
    parser.add_argument("--age", type=int, default=28)
    parser.add_argument("--height", type=float, default=175.0, help="身高 cm")
    parser.add_argument("--weight", type=float, default=78.0, help="体重 kg")
    parser.add_argument("--activity", choices=list(ACTIVITY_FACTOR), default="light")
    parser.add_argument("--weekly-loss", type=float, default=0.5, help="每周目标减重，体重的百分比，如 0.5 表示 0.5%%")
    parser.add_argument("--target-kcal", type=float, default=None, help="直接指定日摄入目标，覆盖推算值")
    parser.add_argument("--meal", choices=list(MEAL_SHARE), default="lunch")
    parser.add_argument("--allow-dessert", action="store_true", help="允许把甜点纳入候选（默认排除）")
    parser.add_argument("--store-code", default=None, help="门店编码，用于拉菜单价与门店可用券")
    parser.add_argument("--city", default=None, help="城市名，用于查门店从而拿到真实售价")
    parser.add_argument("--keyword", default=None, help="门店关键词，配合 --city 缩小范围")
    parser.add_argument("--max-detail", type=int, default=150, help="最多拉取多少个餐品详情来对齐价格，默认 150")
    parser.add_argument("--live", action="store_true", help="调用真实麦当劳 MCP（需要 MCD_MCP_TOKEN）")
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于被上游 Agent 消费")
    parser.add_argument("--out", default=None, help="把 Markdown 结果写入文件")
    args = parser.parse_args(argv)

    profile = {
        "sex": args.sex, "age": args.age, "height_cm": args.height,
        "weight_kg": args.weight, "activity": args.activity,
    }

    energy = safe_deficit_target(
        sex=args.sex, age=args.age, height_cm=args.height, weight_kg=args.weight,
        activity=args.activity, weekly_loss_pct=args.weekly_loss / 100.0,
        explicit_target=args.target_kcal,
    )
    budget = meal_budget(energy["target_kcal"], args.meal)

    if args.live:
        items, price_index, coupon_index, source = load_live(args.store_code, args.city, args.keyword, args.max_detail)
    else:
        items, price_index, coupon_index, source = load_fixture()

    items = filter_by_meal(items, args.meal)

    plans = build_plans(items, budget["budget_kcal"], top=3,
                        tolerance=budget["tolerance"], allow_dessert=args.allow_dessert)
    apply_pricing(plans, price_index, coupon_index)

    if args.json:
        payload = {
            "profile": profile,
            "energy": energy,
            "budget": budget,
            "plans": [
                {
                    "title": p.title,
                    "items": [asdict(i) for i in p.items],
                    "kcal": p.kcal, "protein": p.protein, "fat": p.fat,
                    "carb": p.carb, "sodium": p.sodium,
                    "price_yuan": None if p.price_cents is None else round(p.price_cents / 100.0, 2),
                    "saved_yuan": round(p.saved_cents / 100.0, 2),
                    "reasons": p.reasons,
                }
                for p in plans
            ],
            "source": source,
            "disclaimer": DISCLAIMER,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        print(text)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        return 0

    markdown = render_markdown(profile, energy, budget, plans, source)
    print(render_console(profile, energy, budget, plans))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(markdown + "\n")
        print("\n已写入 %s" % args.out)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(main())
