# 真实调用验证记录

> 本文件用于记录本项目对麦当劳 MCP 的**真实**调用。
> 所有 `fixtures/` 中的数据只是离线演示用的近似值，不是官方数据；对外展示的结果必须来自下面的真实调用。

## 复现步骤

```bash
# 0. 申请 Token：https://open.mcd.cn/mcp → 手机号登录 → 控制台 → 激活 → 复制

# 1. 配置环境变量（不要写进任何文件）
export MCD_MCP_TOKEN="你的 Token"          # Windows PowerShell: $env:MCD_MCP_TOKEN="..."

# 2. 连通性自检：握手 → tools/list → now-time-info
python scripts/mcp_client.py

# 3. 真实配餐
python scripts/mcd_fatloss.py --sex male --age 28 --height 178 --weight 82 \
  --activity light --weekly-loss 0.5 --meal lunch --live --out docs/live-output.md
```

## 自检脚本的预期输出形态

```json
{
  "server": { "name": "mcd-mcp", "version": "..." },
  "protocol": "2025-06-18",
  "session": true,
  "tool_count": 30,
  "tool_names": ["list-nutrition-foods", "available-coupons", "..."]
}
```

`tool_count` 与 `tool_names` 以服务端实际返回为准（官方版本日志显示工具会持续新增）。

---

## 实测记录

> 提交前请用真实 Token 跑一遍第 2、3 步，把输出粘贴到下面。没有这一段，项目就只是"看起来能跑"。

### 环境

- 日期：
- 客户端：WorkBuddy / 命令行
- 协议版本：
- 工具总数：

### 调用 1：`now-time-info`

```
（粘贴返回内容）
```

### 调用 2：`list-nutrition-foods`

返回格式说明（JSON / TOON）：______
返回餐品条数：______
前 3 条样例：

```
（粘贴前 3 条）
```

### 调用 3：`available-coupons`

返回券数量：______
样例：

```
（粘贴 1-2 条券，注意隐去与个人账号绑定的信息）
```

### 调用 4：`query-meals` / `calculate-price`（可选，需门店编码）

```
（粘贴返回内容）
```

### 最终配餐结果

```
（粘贴 python scripts/mcd_fatloss.py ... --live 的控制台输出）
```

---

## 备注

- 单次完整配餐的 MCP 调用次数通常为 2-4 次，远低于 600 次/分钟的限流。
- 若返回 401，检查 Token 是否正确、是否过期；若返回 429，降低调用频率。
- Token 属于个人凭证，请勿提交到仓库，也不要粘贴到 Issue 中。
