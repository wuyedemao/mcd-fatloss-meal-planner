# 麦麦减脂配餐助手

> 减脂期想吃麦当劳，不用再靠意志力硬扛——先算清楚这餐有多少额度，再从真实菜单里搜出最划算的那一份。

一个基于**麦当劳中国 MCP** 的 Skill：读取你的身高、体重、减脂目标，算出当天热量预算，
调用 `list-nutrition-foods` 拉实时营养数据、`available-coupons` / `query-store-coupons` 查可用优惠券，
在热量窗口内搜出一份**蛋白质优先、控钠控脂、还能用券省钱**的套餐，并告诉你大致价格。

「麦当劳程序员创意开发大赛」参赛作品 · 非麦当劳官方产品。

---

## 它和"问 AI 麦当劳什么低卡"有什么不一样

大部分回答是这样产生的：模型凭记忆说"板烧鸡腿堡热量低"，然后编一个数字给你。

这个 Skill 走的是另一条路：

| 环节 | 常见做法 | 本项目的做法 |
| --- | --- | --- |
| 热量预算 | 拍一个"减脂吃 500 大卡" | Mifflin-St Jeor 算 BMR → TDEE → 按周减重比例推缺口，再拆到餐次 |
| 营养数据 | 模型记忆，经常不准 | 实时调用 `list-nutrition-foods`，解析 MCP 返回的 **toon** 格式 |
| 选餐 | 挑一个"看起来健康"的堡 | 在热量窗口内做组合搜索，蛋白质密度、钠、脂肪一起算分 |
| 价格 | 不给，或瞎报 | `available-coupons` 拿券价、`query-meals` 拿菜单价，算清省多少 |
| 安全边界 | 用户要 500 大卡就配 500 大卡 | 三道护栏拦下极端节食，BMI 过低直接不给减脂方案 |

---

## 效果演示

输入：男，28 岁，178 cm，82 kg，每周 1-3 次轻运动，午餐，每周减 0.5%。

```
## 你的减脂能量账本
BMR 1798 · TDEE 2472 · 日目标 2021 kcal · 缺口 451 kcal · BMI 25.9
午餐预算 707 kcal（可接受 601–763）

### 方案 1：板烧鸡腿堡 + 玉米杯 + 纯牛奶
620 kcal | 蛋白质 34 g | 脂肪 23 g | 碳水 74 g | 钠 945 mg | 23.90 元（用券省 16.10 元）

- 比预算低 87 kcal，今天还剩 1401 kcal 额度
- 蛋白质 34 g，每 100 kcal 换到 5.4 g，是这份菜单里最高的一档
- 主餐选板烧鸡腿堡（400 kcal / 24 g 蛋白），而不是靠大份薯条和含糖饮料把热量撑满
- 饮品选纯牛奶：同样 130 kcal，它带 6.5 g 蛋白质，可乐只有糖
- 注意钠 945 mg 已接近单餐上限，另外两餐吃清淡点
```

完整输出见 [`examples/demo-output.md`](examples/demo-output.md)（该文件由离线演示数据生成，标注了来源）。

---

## 目标用户

- **在减脂、但公司楼下只有麦当劳**的上班族：需要一份"不破坏进度、也不用饿着"的具体点单方案。
- **刚开始记录热量、算不明白 BMR/TDEE** 的人：不用学公式，直接拿结果。
- **想吃又想省钱**的学生党：券价和菜单价一起算，同一份套餐能差 16 元。
- **做健康管理内容的创作者**：需要一个能批量产出"某热量档位下的麦当劳搭配"的工具。

---

## 安装

### 前置条件：申请麦当劳 MCP Token

1. 打开 <https://open.mcd.cn/mcp>，用手机号登录
2. 右上角进入「控制台」→ 点「激活」申请 Token
3. 同意服务协议后一键复制

### 方式一：作为 WorkBuddy / Claude Code Skill 使用（推荐）

```bash
# 克隆到本地
git clone https://github.com/wuyedemao/mcd-fatloss-meal-planner.git
cd mcd-fatloss-meal-planner

# 装进 WorkBuddy 的技能目录（在仓库根目录执行）
# Windows
xcopy /E /I . "%USERPROFILE%\.workbuddy\skills\mcd-fatloss-meal-planner"
# macOS / Linux
mkdir -p ~/.workbuddy/skills/mcd-fatloss-meal-planner && cp -r . ~/.workbuddy/skills/mcd-fatloss-meal-planner
```

然后在 WorkBuddy 里配置 MCP 连接器：`专家·技能·连接器` → `连接器` → `自定义连接器` → `配置 MCP`，
粘贴 [`mcp-config.example.json`](mcp-config.example.json) 的内容，把 `${MCD_MCP_TOKEN}` 换成你的 Token，保存并启用。

### 方式二：纯命令行跑

```bash
export MCD_MCP_TOKEN="你的 Token"        # Windows PowerShell: $env:MCD_MCP_TOKEN="..."

python scripts/mcd_fatloss.py --sex male --age 28 --height 178 --weight 82 \
  --activity light --weekly-loss 0.5 --meal lunch --live
```

只依赖 Python 3.9+ 标准库，不需要 `pip install` 任何东西。

### 连通性自检

```bash
python scripts/mcp_client.py
```

输出里能看到 `tool_count`、工具名列表和服务器时间，就说明 Token 和网络都通了。

---

## 使用

### 对话方式（Skill 模式）

> 我 178、82 公斤，男，28 岁，每周跑两三次，想每周减 0.5 公斤，中午想吃麦当劳，帮我配一份。

> 今天想吃巨无霸，怎么搭才不超标？

> 我老婆 163、58 公斤，晚上想吃麦辣鸡腿堡，热量能卡在多少？

### 命令行方式

```bash
# 基础用法
python scripts/mcd_fatloss.py --sex female --age 26 --height 163 --weight 58 --meal dinner --live

# 指定门店，拉真实菜单价和门店券
python scripts/mcd_fatloss.py --sex male --age 28 --height 178 --weight 82 \
  --meal lunch --store-code 1234567 --live

# 按城市找门店，拿到真实售价（推荐）
python scripts/mcd_fatloss.py --sex male --age 28 --height 178 --weight 82 \
  --meal lunch --city 上海 --keyword 人民广场 --live

# 输出 JSON，给上游 Agent 或工作流消费
python scripts/mcd_fatloss.py --sex male --age 28 --height 178 --weight 82 --meal lunch --json

# 不加 --live 则走离线演示数据，用于验证流程，不需要 Token
python scripts/mcd_fatloss.py --meal lunch
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `--sex` / `--age` / `--height` / `--weight` | 基础信息，影响 BMR |
| `--activity` | `sedentary` / `light` / `moderate` / `active` |
| `--weekly-loss` | 每周减重占体重的百分比，默认 `0.5`（即 0.5%），上限 1% |
| `--target-kcal` | 直接指定日摄入目标，覆盖推算值（仍受下限护栏约束） |
| `--meal` | `breakfast` / `lunch` / `dinner` / `snack` |
| `--allow-dessert` | 允许甜点进入候选（默认排除） |
| `--live` | 调用真实 MCP |
| `--json` | 输出结构化结果 |
| `--out` | 把 Markdown 结果写入文件 |

---

## 算法

### 1. 能量账本

- **BMR**：Mifflin-St Jeor 公式，`10×体重 + 6.25×身高 − 5×年龄 + (男 5 / 女 −161)`
- **TDEE**：BMR × 活动系数（1.2 / 1.375 / 1.55 / 1.725）
- **日缺口**：`体重 × 周减重比例 × 7700 ÷ 7`，并封顶为 TDEE 的 25%
- **单餐预算**：日摄入目标 × 餐次占比（早 25% / 午 35% / 晚 30% / 加餐 10%）

### 2. 三道安全护栏

| 护栏 | 规则 | 触发后的行为 |
| --- | --- | --- |
| 热量下限 | 日摄入 ≥ `max(BMR×1.1, 男 1500 / 女 1200)` | 上调到下限，并向用户解释原因 |
| 减重速度 | 每周 ≤ 体重的 1% | 压到 1% 重算 |
| 体重过低 | BMI < 18.5 | 不提供减脂方案，改为维持热量配餐 |

用户要求"一天 800 大卡"这类极端目标时，Skill 会拒绝并给出安全方案，而不是顺从。

### 3. 组合搜索

- 候选 = 主食 ×（0-1 小食）×（0-1 饮品），小食按蛋白密度剪枝到 16 项，饮品按热量剪枝到 12 项
- 热量窗口：`[预算×85%, 预算×108%]`，**下浮比上浮宽松**——减脂期宁可欠一点，不要超
- 硬约束：单餐脂肪 ≤ 32 g、钠 ≤ 1100 mg、蛋白质 ≥ `max(18 g, 预算×3.5%)`
- 排序分：`2.2×热量贴合 + 0.95×蛋白质总量 + 0.55×蛋白质密度 − 0.6×钠惩罚 − 0.5×脂肪惩罚`
- 默认排除甜点（−0.35）和含糖饮料（−0.45），用户主动要求才放进来
- 三条推荐的主食互不重复

---

## 目录结构

```
mcd-fatloss-meal-planner/
├── SKILL.md                    # Skill 主文件，给 Agent 的执行指令
├── README.md                   # 本文件
├── CONTEST_DECLARATION.md      # 参赛声明（官方原文，未改动）
├── MCP_INTEGRATION.md          # MCP 接入说明：Server / Tool / 调用流程 / 业务价值
├── mcp-config.example.json     # 脱敏配置示例，仅环境变量占位符
├── workbuddy.md                # WorkBuddy 开发对话上下文
├── scripts/
│   ├── mcd_fatloss.py          # 核心算法 + CLI
│   ├── mcp_client.py           # MCP Streamable HTTP 客户端（标准库实现）
│   ├── toon.py                 # TOON 格式解码器
│   ├── selftest.py             # 39 项离线自测
│   └── fixtures/               # 离线演示数据（非官方数据，仅用于流程验证）
├── examples/demo-output.md     # 演示输出
└── docs/VERIFICATION.md        # 真实 MCP 调用记录（复现用）
```

---

## 自测

```bash
python scripts/selftest.py
```

离线运行，覆盖 TOON 解析、字段归一化、餐品分类、BMR/TDEE、三道护栏、组合搜索约束、价格与券计算，共 39 项断言。

---

## 已知限制

- 套餐内商品替换（如换配餐）依赖 `query-meal-detail`，当前版本按单品组合搜索，未处理套餐内的替换规则。
- 券与商品的编码体系不一致，部分券无法自动匹配到具体商品，此时只展示券名与券价。
- 营养数据以 MCP 返回为准；若某餐品缺少热量字段，会被直接丢弃而不是按 0 kcal 处理。
- 不含疾病相关的饮食方案（糖尿病、肾病、痛风等），这类情况请就医。

---

## 免责声明

本项目为「麦当劳程序员创意开发大赛」参赛作品，由参赛者独立开发，**非麦当劳官方产品**，不代表麦当劳立场。
所有计算结果仅供参考，**不构成医疗、营养或其他专业建议**。餐品信息、价格及供应状态以麦当劳官方渠道实时结果为准。
使用麦当劳 MCP 服务需遵守麦当劳中国的《使用条款》与《MCP 服务规则》。

---

## License

[MIT](LICENSE)
