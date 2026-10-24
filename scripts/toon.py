"""TOON（Token-Oriented Object Notation）解码器。

麦当劳 MCP 的营养数据为了节省 token 采用了紧凑的 toon 格式，典型形态：

    foods[2]{code,name,energyKcal,protein}:
      M001,巨无霸,510,26
      M002,麦香鱼,340,16

它不是 JSON，直接用 json.loads 会抛异常。这里提供一个宽容解码器：
先按缩进建树，再按表头字段把行切成字典；解析不动就返回 None，
由调用方决定降级策略（把原文交给模型读）。
"""

from __future__ import annotations

import csv
import re

_HEADER = re.compile(r"^(?P<key>[^\[\]:]+?)\[(?P<n>\d+)\]\{(?P<fields>[^}]*)\}:\s*$")
_KV = re.compile(r"^(?P<key>[^:]+):\s?(?P<val>.*)$")
_QUOTED = re.compile(r'^"(.*)"$')


def _coerce(token: str):
    t = token.strip()
    if t == "":
        return None
    quoted = _QUOTED.match(t)
    if quoted:
        return quoted.group(1)
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "nil", "none"):
        return None
    try:
        if re.fullmatch(r"-?\d+", t):
            return int(t)
        if re.fullmatch(r"-?\d*\.\d+", t):
            return float(t)
    except ValueError:
        pass
    return t


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _split_row(row: str) -> list[str]:
    try:
        return next(csv.reader([row], skipinitialspace=True))
    except (StopIteration, csv.Error):
        return row.split(",")


def _prepare(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        out.append((_indent_of(raw), raw.strip()))
    return out


def _parse_block(lines: list[tuple[int, str]], i: int, indent: int):
    """解析 indent 层级的顺序块，返回 (dict, 下一行下标)。"""
    obj: dict = {}
    while i < len(lines):
        cur_indent, content = lines[i]
        if cur_indent < indent:
            break
        if cur_indent > indent:  # 悬空缩进，跳过
            i += 1
            continue

        header = _HEADER.match(content)
        if header:
            key = header.group("key").strip()
            fields = [f.strip() for f in header.group("fields").split(",") if f.strip()]
            rows = []
            i += 1
            while i < len(lines) and lines[i][0] > cur_indent:
                cells = _split_row(lines[i][1])
                rows.append({f: _coerce(v) for f, v in zip(fields, cells)})
                i += 1
            obj[key] = rows
            continue

        kv = _KV.match(content)
        if kv:
            key = kv.group("key").strip()
            val = kv.group("val").strip()
            if val == "":
                # 嵌套对象
                i += 1
                if i < len(lines) and lines[i][0] > cur_indent:
                    child, i = _parse_block(lines, i, lines[i][0])
                    obj[key] = child
                else:
                    obj[key] = {}
            else:
                obj[key] = _coerce(val)
                i += 1
            continue

        # 既不是表头也不是键值对：放弃
        return None, len(lines)

    return obj, i


def toon_decode(text: str):
    """完整解码；结构不可识别时返回 None。"""
    if not text or "{" not in text or "}:" not in text.replace("}: ", "}:"):
        # 没有表头的话交给 toon_rows 兜底
        rows = toon_rows(text)
        return {"rows": rows} if rows else None

    lines = _prepare(text)
    if not lines:
        return None
    value, _ = _parse_block(lines, 0, lines[0][0])
    return value


def toon_rows(text: str) -> list[dict]:
    """从任意文本中抽取第一个表格块，返回字典列表。"""
    lines = _prepare(text)
    for idx, (_, content) in enumerate(lines):
        header = _HEADER.match(content)
        if not header:
            continue
        fields = [f.strip() for f in header.group("fields").split(",") if f.strip()]
        base = lines[idx][0]
        rows: list[dict] = []
        j = idx + 1
        while j < len(lines) and lines[j][0] > base:
            cells = _split_row(lines[j][1])
            rows.append({f: _coerce(v) for f, v in zip(fields, cells)})
            j += 1
        return rows
    return []


if __name__ == "__main__":
    demo = """foods[3]{code,name,energyKcal,protein,fat}:
  M001,巨无霸,510,26,25
  M002,麦香鱼,340,16,15
  M003,板烧鸡腿堡,400,24,18
meta:
  source: mcd-mcp
  count[2]:
    1,a
"""
    import json
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(toon_decode(demo), ensure_ascii=False, indent=2))
