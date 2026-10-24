"""麦当劳 MCP 最小可用客户端（Streamable HTTP / JSON-RPC 2.0）。

只依赖标准库。因为 MCP 的 Streamable HTTP 传输可能返回 `text/event-stream`，
所以响应体需要同时兼容「裸 JSON」和「SSE 帧」两种形态。

用法：
    client = McdMcpClient()          # 从环境变量 MCD_MCP_TOKEN / MCD_MCP_URL 读取
    client.initialize()
    items = client.call_json("list-nutrition-foods", {})
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_URL = "https://mcp.mcd.cn"


class McpError(RuntimeError):
    """MCP 调用失败（网络、鉴权、限流、协议错误）。"""


class McdMcpClient:
    def __init__(self, url: str | None = None, token: str | None = None, timeout: int = 45):
        self.url = (url or os.environ.get("MCD_MCP_URL") or DEFAULT_URL).rstrip("/")
        self.token = token or os.environ.get("MCD_MCP_TOKEN") or ""
        self.timeout = timeout
        self.session_id: str | None = None
        self._seq = 0
        self.tools: list[dict] = []

    # ------------------------------------------------------------------ 传输层

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def _post(self, payload: dict) -> dict | None:
        if not self.token:
            raise McpError(
                "缺少 MCP Token。先在 https://open.mcd.cn/mcp 用手机号登录、控制台激活 Token，"
                "再设置环境变量 MCD_MCP_TOKEN。"
            )

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "User-Agent": "mcd-fatloss-meal-planner/1.0.0",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code == 401:
                raise McpError(f"HTTP 401：Token 无效、过期或未提供。{detail}") from exc
            if exc.code == 429:
                raise McpError("HTTP 429：触发限流（600 次/分钟），请降低调用频率。") from exc
            raise McpError(f"HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise McpError(f"网络不可达：{exc.reason}") from exc

        if not raw.strip():
            return None  # 通知（notification）返回 202 空体，属正常
        return self._decode_body(raw, payload.get("id"))

    @staticmethod
    def _decode_body(raw: str, want_id) -> dict:
        """兼容裸 JSON 与 SSE 两种响应。"""
        text = raw.strip()

        if text.startswith("{") or text.startswith("["):
            data = json.loads(text)
            return data if isinstance(data, dict) else {"result": data}

        # SSE：逐帧取 data:，选 id 匹配的那一帧
        frames: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                frames.append(json.loads(chunk))
            except json.JSONDecodeError:
                continue
        if not frames:
            raise McpError(f"无法解析响应：{text[:300]}")
        for frame in frames:
            if frame.get("id") == want_id:
                return frame
        return frames[-1]

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            payload["params"] = params
        res = self._post(payload) or {}
        if "error" in res:
            err = res["error"]
            raise McpError(f"{method} 失败：{err.get('code')} {err.get('message')}")
        return res

    # ------------------------------------------------------------------ 协议层

    def initialize(self) -> dict:
        res = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcd-fatloss-meal-planner", "version": "1.0.0"},
            },
        )
        # 握手完成后必须发一条 initialized 通知，否则部分服务端拒绝后续调用
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res.get("result", {})

    def list_tools(self) -> list[dict]:
        res = self._rpc("tools/list", {})
        self.tools = res.get("result", {}).get("tools", [])
        return self.tools

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        """调用工具并返回拼接后的纯文本负载（内容可能是 JSON，也可能是 TOON）。"""
        res = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        result = res.get("result", {})

        if result.get("isError"):
            raise McpError(f"工具 {name} 返回错误：{self._flatten(result)[:400]}")

        text = self._flatten(result)
        if not text:
            raise McpError(f"工具 {name} 返回空结果")
        return text

    def call_json(self, name: str, arguments: dict | None = None):
        """调用工具并把结果解析成 Python 对象（JSON 优先，失败回退 TOON）。"""
        from toon import toon_decode  # 延迟导入，避免循环依赖

        text = self.call_tool(name, arguments)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        decoded = toon_decode(text)
        if decoded is not None:
            return decoded
        try:
            from toon import toon_rows

            rows = toon_rows(text)
            if rows:
                return rows
        except Exception:  # noqa: BLE001 - 解析兜底，不掩盖原始错误
            pass
        return {"_raw": text}

    @staticmethod
    def _flatten(result: dict) -> str:
        if isinstance(result.get("structuredContent"), (dict, list)):
            return json.dumps(result["structuredContent"], ensure_ascii=False)
        parts = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)


def preflight(url: str | None = None, token: str | None = None) -> dict:
    """连通性自检：握手 → 列工具 → 调一次 now-time-info。"""
    client = McdMcpClient(url=url, token=token, timeout=20)
    info = client.initialize()
    tools = client.list_tools()
    now = client.call_tool("now-time-info", {})
    return {
        "server": info.get("serverInfo", {}),
        "protocol": info.get("protocolVersion"),
        "session": bool(client.session_id),
        "tool_count": len(tools),
        "tool_names": [t.get("name") for t in tools],
        "now": now.strip()[:200],
    }


if __name__ == "__main__":
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="麦当劳 MCP 连通性自检 / 原始返回抓取")
    ap.add_argument("--dump", metavar="TOOL", help="调用指定工具并把原始返回存成文件，如 list-nutrition-foods")
    ap.add_argument("--out", default=None, help="保存路径，默认 docs/_raw/<tool>.txt")
    ap.add_argument("--args", default="{}", help="工具入参 JSON，默认 {}")
    ns = ap.parse_args()

    if ns.dump:
        import os

        try:
            client = McdMcpClient()
            client.initialize()
            text = client.call_tool(ns.dump, json.loads(ns.args))
            out = ns.out or os.path.join("docs", "_raw", ns.dump + ".txt")
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print("已保存 %d 字符 -> %s" % (len(text), out))
            print("--- 前 800 字符 ---")
            print(text[:800])
        except McpError as exc:
            print(f"[失败] {exc}")
            raise SystemExit(1)
        raise SystemExit(0)

    try:
        print(json.dumps(preflight(), ensure_ascii=False, indent=2))
    except McpError as exc:
        print(f"[失败] {exc}")
        raise SystemExit(1)
