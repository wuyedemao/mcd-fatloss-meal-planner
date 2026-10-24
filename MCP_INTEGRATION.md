# MCP 接入说明

本文说明「麦麦减脂配餐助手」实际使用了哪些麦当劳 MCP 能力、怎么调用、为什么这么调用。

---

## 1. MCP Server

| 项 | 值 |
| --- | --- |
| 名称 | 麦当劳中国 MCP Server（`mcd-mcp`） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP（JSON-RPC 2.0） |
| 协议版本 | `2025-06-18` |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| Token 申请 | <https://open.mcd.cn/mcp> → 手机号登录 → 控制台 → 激活 |
| 限流 | 600 次 / 分钟，超出返回 429 |
| 错误码 | `401` Token 无效或过期；`429` 触发限流 |

握手流程（`scripts/mcp_client.py` 已实现）：

```
POST /  initialize          → 拿到 protocolVersion、serverInfo，响应头带回 Mcp-Session-Id
POST /  notifications/initialized   （通知，服务端返回 202 空体）
POST /  tools/list          → 拿到工具清单
POST /  tools/call          → 调用具体工具
```

后续请求都带上 `Mcp-Session-Id`。响应可能是普通 JSON，也可能是 `text/event-stream` 的 SSE 帧，
客户端两种都会解析。

---

## 2. 实际使用的 Tool

### 核心工具（缺一不可）

| Tool | 入参 | 关键返回字段 | 在本项目中的作用 |
| --- | --- | --- | --- |
| `list-nutrition-foods` | 无 | 餐品编码、名称、能量 kcal、蛋白质、脂肪、碳水化合物、钠、钙 | 所有营养计算的唯一数据源。没有它就没有方案 |
| `available-coupons` | 无 | 券名、`couponId`、商品编码、券价 | 麦麦省当前可领的券，决定"这餐多少钱"和"省多少" |
| `query-store-coupons` | `storeCode`（可选） | 门店维度的可用券 | 券是按门店放的，拿到门店后筛掉不可用的券 |
| `now-time-info` | 无 | 当前日期时间 | 判断券和活动是否还在有效期内，避免推荐过期券 |

### 增强工具（有则更好，无则降级）

| Tool | 入参 | 作用 | 缺失时的降级 |
| --- | --- | --- | --- |
| `query-nearby-stores` | `lat`、`lng` | 找到用户附近的门店，拿到 `storeCode` | 跳过门店环节，只给券价 |
| `query-meals` | `storeCode` | 拿到该门店在售菜单与售价 | 用券价代替，非券商品价格显示「以门店实际售价为准」 |
| `query-meal-detail` | 餐品编码 | 套餐组成与可替换项 | 当前版本按单品组合，不做套餐内替换 |
| `calculate-price` | 商品列表、券、门店 | 返回应付总价（单位：**分**） | 本地按「券价优先、否则菜单价」相加，并在输出中标明为估算 |

---

## 3. 调用流程

```
用户输入（性别/年龄/身高/体重/活动量/目标/餐次/忌口）
        │
        ▼
① 本地计算：BMR → TDEE → 安全缺口 → 单餐热量窗口
   （不消耗 MCP 配额，纯本地确定性计算）
        │
        ▼
② now-time-info           确认日期，判断券有效期
        │
        ▼
③ list-nutrition-foods    拿全量营养表（toon 格式）→ 解析成结构化数据
        │
        ▼
④ available-coupons       拿可领券列表
   query-store-coupons    有门店时拿门店可用券
        │
        ▼
⑤ 组合搜索（本地）
   候选 = 主食 ×（0-1 小食）×（0-1 饮品）
   过滤：热量窗口 / 脂肪 ≤32g / 钠 ≤1100mg / 蛋白 ≥max(18g, 预算×3.5%)
   排序：热量贴合 + 蛋白总量 + 蛋白密度 − 钠惩罚 − 脂肪惩罚
        │
        ▼
⑥ query-meals（可选）     拿门店售价
   calculate-price（可选） 确认应付金额（分 → 元）
        │
        ▼
⑦ 输出：能量账本 + 3 个方案 + 每方案热量/蛋白/钠/价格 + 剩余额度 + 免责声明
```

**设计取舍**：把能量计算和组合搜索放在本地，只在必要时调 MCP。
一次完整配餐通常只消耗 2-4 次调用（营养表 1 次、券 1-2 次、价格 0-2 次），
远低于 600 次/分钟的限流，也省去把 30 多款餐品的原始数据反复塞进上下文。

---

## 4. 数据格式：为什么需要写一个 TOON 解析器

`list-nutrition-foods` 返回的不是 JSON，而是为省 token 设计的 **toon** 格式：

```
foods[3]{code,name,energyKcal,protein,fat,carbohydrate,sodium}:
  1001,巨无霸,510,26,25,45,950
  1004,板烧鸡腿堡,400,24,15,45,850
  1204,玉米杯,90,3,1,19,5
```

直接 `json.loads` 会抛异常。`scripts/toon.py` 实现了宽容解码：

1. 按缩进建树，识别 `key[N]{field1,field2}:` 表头 + N 行 CSV
2. 值做类型还原（int / float / bool / null / 引号字符串）
3. 解析失败时 `toon_rows()` 兜底扫描第一个表格块；再失败则返回 `None`，由上层把原文交给模型读

同时 `normalize_items()` 做了字段别名映射（`energyKcal` / `kcal` / `calories` / `能量` / `热量` 都认），
因为不同工具返回的字段命名并不完全统一，硬编码单一字段名会在版本升级后静默失效。

---

## 4.1 商品编码、菜名、价格的三处断点（实测）

三个接口各自只给一半信息，串不起来——这是接入过程中最花时间的地方：

| 接口 | 给了什么 | 缺什么 |
| --- | --- | --- |
| `list-nutrition-foods` | `productName` + 全部营养字段 | **商品编码** |
| `query-meals` | `{"code":"9900014838","tags":[...]}`，15 个分类共 129 个编码 | **菜名、价格** |
| `query-meal-detail` | `code / name / rounds / image`，套餐组成与换料差价 | **单品售价**（只有 `diffPrice`，如「换燕麦奶 +¥2」） |
| `calculate-price` | `productList[]`，每项含 `productName` + `subtotal`（分） | 需要传 `productCode` |

所以价格链路只能是：

```
query-meals → 129 个编码
  → calculate-price(items=[{productCode, quantity:1}]) 分批（40 个/批）
  → 从 productList 取 productName + subtotal
  → 按归一化菜名与营养表对齐 → 建立「菜名 → 单价」
```

两个坑值得记下来：

1. **`query-meal-detail` 没有单品价格。** 深度遍历解析时很容易先撞上
   `rounds[].choices[].modification.values[].price`，把「换燕麦奶 2 元」当成商品价，
   表现是"有价格但全都对不上"。
2. **价格字段叫 `subtotal`**，不是 `price`。响应顶层还有个 `price: 140480`（整单合计，单位分），
   按 `price` 取会把整单金额当成单品价。

另外，菜单里大量是「三件套」「双人餐」这类组合名，营养表里只有单品，
两边名称并不能完全对齐。因此当价格数据可用时，本项目会把候选池收敛到「能算出价格」的餐品，
保证推出来的每一份套餐都能报出总价；匹配不足 8 项时退回全量候选，只标注「N/M 项有价」。

---

## 5. 业务价值

### 对用户

- **可执行**：不是"少吃点油炸的"，而是"点板烧鸡腿堡 + 玉米杯 + 纯牛奶，620 kcal，用券 23.9 元，今天还剩 1401 kcal"。
- **省时间**：不用自己算 BMR、不用一款款对热量表、不用翻券包比价。
- **省钱**：券价与菜单价一起算，同一份套餐在演示数据里能差 16 元。
- **有边界**：极端节食会被拦下，BMI 过低直接不给减脂方案。工具不应该帮用户伤害自己。

### 对麦当劳

- **把"想吃但不敢吃"的人拉回门店**：减脂人群是最容易流失的客群，这个 Skill 给了他们一个能安心下单的理由。
- **带动券的使用与核销**：方案里天然包含"用哪张券最划算"，券的曝光和领取发生在决策瞬间。
- **提高客单价的结构性质量**：推荐里通常包含饮品或小食，但不是靠大份薯条凑热量，而是靠蛋白质——
  这类组合的复购率通常比"促销堆量"更健康。
- **数据反哺**：哪些餐品能在热量窗口里胜出、哪些券真正影响决策，是可被观察到的需求信号。

---

## 6. 错误处理与降级

| 场景 | 行为 |
| --- | --- |
| 未设置 `MCD_MCP_TOKEN` | 抛出明确提示，引导去 open.mcd.cn 申请；**不生成任何编造数据** |
| `401` | 提示 Token 无效或过期，停止流程 |
| `429` | 提示触发限流，降低调用频率 |
| 营养表返回无法解析 | 先试 JSON，再试 toon，最后把原文交回模型；不静默填 0 |
| 某餐品缺热量字段 | 直接丢弃该餐品，避免被当成 0 kcal 推荐出去 |
| 拿不到价格 | 显示「以门店实际售价为准」，不估算 |
| 热量窗口内无解 | 放宽一档并说明原因，不硬凑超标方案 |

---

## 7. 复现方式

```bash
# 1. 配置 Token
export MCD_MCP_TOKEN="你的 Token"

# 2. 连通性自检（握手 + 列工具 + 调 now-time-info）
python scripts/mcp_client.py

# 3. 真实调用
python scripts/mcd_fatloss.py --sex male --age 28 --height 178 --weight 82 \
  --activity light --weekly-loss 0.5 --meal lunch --live
```

真实调用记录（含工具返回摘要）见 [`docs/VERIFICATION.md`](docs/VERIFICATION.md)。
