"""可转义点路径的解析与位置取证。

版本语义必须显式固定：

- ``dot-v1``：历史旧式点路径，``.`` 永远表示嵌套层级，反斜杠只是普通字符。
- ``escape-v2``：新版可转义路径，``\\.`` 表示字面点号键，``\\\\`` 表示字面反斜杠。

解析结果不只给值，还保留路径表达式原文、规范化位置、文档键和实际行，
供路径确认页与转型回执长期核对。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any


LEGACY_DOT = "dot-v1"
ESCAPABLE_DOT = "escape-v2"

_VERSION_ALIASES = {
    "v1": LEGACY_DOT,
    "legacy": LEGACY_DOT,
    "old": LEGACY_DOT,
    "dot": LEGACY_DOT,
    "dot-v1": LEGACY_DOT,
    "legacy-dot": LEGACY_DOT,
    "old-dot": LEGACY_DOT,
    "旧式": LEGACY_DOT,
    "旧式点路径": LEGACY_DOT,
    "历史版本": LEGACY_DOT,
    "v2": ESCAPABLE_DOT,
    "new": ESCAPABLE_DOT,
    "escape": ESCAPABLE_DOT,
    "escaped": ESCAPABLE_DOT,
    "escapable": ESCAPABLE_DOT,
    "escape-v2": ESCAPABLE_DOT,
    "literal-dot": ESCAPABLE_DOT,
    "new-escape": ESCAPABLE_DOT,
    "新版": ESCAPABLE_DOT,
    "可转义版": ESCAPABLE_DOT,
    "可转义版本": ESCAPABLE_DOT,
}


def normalize_version(version: Any = None, *, default: str = LEGACY_DOT) -> str:
    """把外部版本别名归一为 ``dot-v1`` / ``escape-v2``。"""
    if version is None or version == "":
        return default
    if isinstance(version, int):
        version = f"v{version}"
    key = str(version).strip().lower()
    if key in _VERSION_ALIASES:
        return _VERSION_ALIASES[key]
    # 中文别名不做 lower 也能命中。
    if str(version).strip() in _VERSION_ALIASES:
        return _VERSION_ALIASES[str(version).strip()]
    raise ValueError(f"不支持的路径版本：{version!r}")


@dataclass(frozen=True)
class PathSegment:
    """单个路径段；``literal_dot`` 只用于新版转义语法。"""

    key: str
    array_marker: str = ""  # "", "[*]", 或 "[0]" 等
    literal_dot: bool = False
    original: str = ""

    @property
    def expands_array(self) -> bool:
        return self.array_marker == "[*]" or self.array_marker == "[]"

    @property
    def array_index(self) -> int | None:
        match = re.fullmatch(r"\[(\d+)\]", self.array_marker)
        return int(match.group(1)) if match else None


def _strip_dollar(expression: str) -> str:
    text = expression.strip()
    if text.startswith("$."):
        return text[2:]
    if text == "$":
        return ""
    if text.startswith("$"):
        return text[1:].lstrip(".")
    return text


def _split_legacy(text: str) -> list[str]:
    # 旧版按字面点切分；不识别反斜杠转义，确保历史表达式语义不变。
    return [part for part in text.split(".") if part != ""]


def _split_escapable(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == ".":
                current.append(".")
                i += 2
                continue
            if nxt == "\\":
                current.append("\\")
                i += 2
                continue
            # 未知转义保留反斜杠和下一字符，避免悄悄改写表达式。
            current.extend(["\\", nxt])
            i += 2
            continue
        if char == ".":
            parts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    parts.append("".join(current))
    return [part for part in parts if part != ""]


def parse_path(expression: str, version: Any = None) -> list[PathSegment]:
    """解析路径为段列表。

    空表达式返回空列表，代表文档根本身。支持三类写法：

    - 历史点路径 ``$.a.b``（v1 不识别反斜杠转义）；
    - 转义点路径 ``$.a\\.b``（v2 中 ``\\.`` 是字面点键）；
    - 括号字面键 ``$["a.b"]`` / ``$['a.b']`` 与 ``key[*]``、``key[0]``。
    """
    canonical_version = normalize_version(version)
    text = _strip_dollar(str(expression or ""))
    if text.startswith("."):
        text = text[1:]

    segments: list[PathSegment] = []
    i = 0
    while i < len(text):
        if text[i] == ".":
            i += 1
            continue

        if text[i] == "[":
            close = text.find("]", i + 1)
            if close == -1:
                # 残缺括号按普通键处理，交由后续匹配失败，不静默猜测。
                key = text[i:]
                segments.append(PathSegment(key=key, original=key))
                break
            inner_raw = text[i + 1:close].strip()
            quoted_key: str | None = None
            if len(inner_raw) >= 2 and inner_raw[0] in "'\"" and inner_raw[-1] == inner_raw[0]:
                quote = inner_raw[0]
                body = inner_raw[1:-1]
                quoted_key = re.sub(r"\\(.)", r"\1", body)
            if quoted_key is not None:
                segments.append(PathSegment(
                    key=quoted_key,
                    literal_dot="." in quoted_key,
                    original=text[i:close + 1],
                ))
            elif inner_raw in ("*", "[]") or re.fullmatch(r"\d+", inner_raw):
                marker = "[*]" if inner_raw == "*" else (
                    "[]" if inner_raw == "[]" else f"[{inner_raw}]")
                if segments and not segments[-1].array_marker:
                    previous = segments[-1]
                    segments[-1] = replace(
                        previous,
                        array_marker=marker,
                        original=previous.original + marker,
                    )
                else:
                    segments.append(PathSegment(key="", array_marker=marker, original=marker))
            else:
                segments.append(PathSegment(key=inner_raw, original=text[i:close + 1]))
            i = close + 1
            continue

        start = i
        buffer: list[str] = []
        while i < len(text) and text[i] not in ".[":
            if canonical_version == ESCAPABLE_DOT and text[i] == "\\" and i + 1 < len(text):
                nxt = text[i + 1]
                if nxt == ".":
                    buffer.append(".")
                    i += 2
                    continue
                if nxt == "\\":
                    buffer.append("\\")
                    i += 2
                    continue
                buffer.extend(["\\", nxt])
                i += 2
                continue
            buffer.append(text[i])
            i += 1
        key = "".join(buffer)
        if key:
            segments.append(PathSegment(
                key=key,
                literal_dot=("." in key and canonical_version == ESCAPABLE_DOT),
                original=text[start:i],
            ))
    return segments


def path_depth(expression: str, version: Any = None) -> int:
    """返回嵌套段数；转义的字面点在 v2 中不增加层级。"""
    return len(parse_path(expression, version))


def _location_key(key: str) -> str:
    if key == "" or re.search(r"[^A-Za-z0-9_一-鿿]", key):
        return f"[{key!r}]".replace("'", '"')
    return f".{key}"


def canonical_position(segments: list[PathSegment]) -> str:
    """生成无歧义 JSONPath，字面点键使用括号引号形式。"""
    position = "$"
    for segment in segments:
        position += _location_key(segment.key)
        if segment.array_marker:
            position += "[*]" if segment.expands_array else segment.array_marker
    return position


def _find_actual_line(raw_text: str | None, key: str) -> tuple[int, str]:
    if not raw_text:
        return 0, ""
    lines = raw_text.splitlines() or [raw_text]
    pattern = re.compile(r'(?<!\\)"' + re.escape(key) + r'"\s*:')
    for index, line in enumerate(lines, start=1):
        if pattern.search(line):
            return index, line.strip()
    return 1, lines[0].strip() if lines else raw_text.strip()


def resolve_matches(
    document: Any,
    expression: str,
    version: Any = None,
    *,
    doc_key: str = "",
    document_index: int = 0,
    source_text: str | None = None,
    line_number: int | None = None,
    actual_line: str | None = None,
) -> list[dict[str, Any]]:
    """返回路径在文档中的全部实际命中位置。

    每个命中都自带表达式原文和来源行；调用方后续只应消费这里核准的值。
    """
    canonical_version = normalize_version(version)
    segments = parse_path(expression, canonical_version)
    matches: list[dict[str, Any]] = []

    def append_match(node: Any, loc_segments: list[PathSegment],
                     array_indexes: list[int], loc_text: str,
                     pointer: str) -> None:
        final_key = loc_segments[-1].key if loc_segments else ""
        auto_line, auto_text = _find_actual_line(source_text, final_key)
        match_line = int(line_number or auto_line or 0)
        match_text = actual_line if actual_line is not None else auto_text
        concrete_position = "$" + loc_text
        matches.append({
            "value": node,
            "doc_key": doc_key or f"document-{document_index + 1}",
            "document_index": document_index,
            "expression": expression,
            "path_expression_raw": expression,
            "path_version": canonical_version,
            "position": concrete_position,
            "canonical_position": canonical_position(loc_segments),
            "json_pointer": pointer or "/",
            "location": f"root{loc_text}" if loc_text else "root",
            "array_indexes": list(array_indexes),
            "line_number": match_line,
            "actual_line": match_text,
            "source_line": match_text,
        })

    def walk(node: Any, index: int, loc_text: str, pointer: str,
             array_indexes: list[int]) -> None:
        if index >= len(segments):
            append_match(node, segments[:index], array_indexes, loc_text, pointer)
            return

        segment = segments[index]
        if not isinstance(node, dict) or segment.key not in node:
            return
        child = node[segment.key]
        key_loc = _location_key(segment.key)
        key_pointer = pointer + "/" + segment.key.replace("~", "~0").replace("/", "~1")
        next_loc = loc_text + key_loc
        next_pointer = key_pointer

        if segment.array_marker:
            if not isinstance(child, list):
                # 与既有解析器一致：[*] 也容忍单值节点。
                items = [child]
                indexes: list[int | None] = [None]
            elif segment.expands_array:
                items = child
                indexes = list(range(len(child)))
            else:
                wanted = segment.array_index
                if wanted is None or wanted >= len(child):
                    return
                items = [child[wanted]]
                indexes = [wanted]
            for item, array_index in zip(items, indexes):
                suffix = "[*]" if segment.expands_array else segment.array_marker
                item_loc = next_loc + suffix
                item_pointer = next_pointer + (f"/{array_index}" if array_index is not None else "")
                child_indexes = array_indexes + ([array_index] if array_index is not None else [])
                walk(item, index + 1, item_loc, item_pointer, child_indexes)
            return

        walk(child, index + 1, next_loc, next_pointer, array_indexes)

    walk(document, 0, "", "", [])
    return matches


def preview_choice(document: dict[str, Any], *,
                   old_expression: str = "$.a.b",
                   new_expression: str = "$.a\\.b",
                   doc_key: str = "DOC-LITERAL-DOT",
                   source_text: str | None = None) -> dict[str, Any]:
    """并列预览同一文档的旧嵌套路径与新字面点路径。"""
    old_matches = resolve_matches(
        document, old_expression, LEGACY_DOT,
        doc_key=doc_key, source_text=source_text)
    new_matches = resolve_matches(
        document, new_expression, ESCAPABLE_DOT,
        doc_key=doc_key, source_text=source_text)
    result = {
        "doc_key": doc_key,
        "document": document,
        "old": {
            "key": "legacy",
            "label": "旧式点路径（嵌套 a → b）",
            "expression": old_expression,
            "path_version": LEGACY_DOT,
            "matches": old_matches,
            "value": old_matches[0]["value"] if old_matches else None,
        },
        "new": {
            "key": "escaped",
            "label": "新版转义路径（字面键 a.b）",
            "expression": new_expression,
            "path_version": ESCAPABLE_DOT,
            "matches": new_matches,
            "value": new_matches[0]["value"] if new_matches else None,
        },
    }
    result["legacy"] = result["old"]
    result["escaped"] = result["new"]
    return result
