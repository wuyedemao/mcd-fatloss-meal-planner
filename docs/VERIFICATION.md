# 真实调用验证记录

> **状态**：待用真实 Token 跑一次后自动生成（见下方「一条命令完成」）。
> `scripts/fixtures/` 里的只是离线演示用的近似值，不是官方数据；对外展示的结果必须来自真实调用。

## 一条命令完成

```bash
# 0. 申请 Token：https://open.mcd.cn/mcp → 手机号登录 → 控制台 → 激活 → 复制
# 1. 配置环境变量（不要写进任何文件、不要提交）
export MCD_MCP_TOKEN="你的 Token"        # Windows PowerShell: $env:MCD_MCP_TOKEN="..."

# 2. 连通性自检：握手 → tools/list → now-time-info
python scripts/mcp_client.py

# 3. 真实配餐，结果直接写成 docs/live-output.md
python scripts/mcd_fatloss.py --sex male --age 28 --height 178 --weight 82 \
  --activity light --weekly-loss 0.5 --meal lunch --live --out docs/live-output.md

# 4. 提交
git add docs/live-output.md && git commit -m "docs: 补充真实 MCP 调用结果" && git push
```

跑完 `docs/live-output.md` 就是实测结果，其中「数据来源」一行会写明本次调用了哪些 MCP 工具。
本文件只负责说明复现方式，不需要手工填写。

---

## 这次真实调用验证了什么

| 验证项 | 期望 | 怎么看 |
| --- | --- | --- |
| 握手与鉴权 | `initialize` 成功，`Mcp-Session-Id` 有返回 | `python scripts/mcp_client.py` 输出里 `session: true` |
| 工具可见性 | 至少能看到 `list-nutrition-foods`、`available-coupons` | 自检输出里的 `tool_names` 列表 |
| TOON 解析 | 营养表能被解析成结构化数据，不是一整块原文 | 配餐结果里出现了具体餐品名和热量 |
| 热量窗口 | 每条推荐都落在 `[预算×85%, 预算×108%]` | 控制台输出的方案热量 vs 上方预算行 |
| 价格与券 | 有门店时显示真实价格与「用券省 X 元」，无门店时显示「—」 | 方案表格的价格列 |

---

## 失败时怎么判断

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `HTTP 401` | Token 无效或过期 | 回控制台重新激活并复制 |
| `HTTP 429` | 触发 600 次/分钟限流 | 等一分钟再试，不要循环调用 |
| 营养表解析后为空 | 服务端返回结构变了 | 把原始文本打个 issue，或跑 `python scripts/toon.py` 单独验证解码器 |
| 价格列全是 `—` | 未传 `--store-code` 或 `--lat/--lng` | 加上门店参数重跑 |
| 没有任何方案 | 预算区间内无解 | 换餐次或放宽 `--weekly-loss`，脚本会说明原因 |

---

## 备注

- 单次完整配餐的 MCP 调用次数通常为 2-4 次，远低于限流上限。
- Token 属于个人凭证，不要提交到仓库，也不要粘贴到 Issue 或截图里。
- `docs/live-output.md` 中不含 Token，只有餐品与计算结果，可以公开。
