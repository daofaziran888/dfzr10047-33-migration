"""变换级联：嵌套展平→取值转型→单位归一。

核心行为：
- 数组展开按 EXPAND / ARRAY_TO_STRING 两种策略决定一行展开成几行
- 展平层数超过上限时登记层数超限并中止该路径展平
- 转型失败的行按行号交给坏行隔离，不回填空值伪装成功
- 单位归一按来源查换算系数，系数冲突时拒绝归一并保留原始取值
"""
from __future__ import annotations

from typing import Any

from app.pipeline.path_contract import LEGACY_DOT, normalize_version, parse_path


class FlattenPath:
    """单条展平路径定义。"""

    def __init__(self, path_id: str, template_id: str, source_path: str,
                 target_columns: list[str], array_mode: str,
                 max_depth: int, empty_policy: str,
                 path_version: str = LEGACY_DOT):
        self.path_id = path_id
        self.template_id = template_id
        self.source_path = source_path
        self.target_columns = target_columns
        self.array_mode = array_mode
        self.max_depth = max_depth
        self.empty_policy = empty_policy
        self.path_version = normalize_version(path_version)
        self.flattened_rows = 0
        self.status = "待展平"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "template_id": self.template_id,
            "source_path": self.source_path,
            "target_columns": self.target_columns,
            "array_mode": self.array_mode,
            "max_depth": self.max_depth,
            "empty_policy": self.empty_policy,
            "path_version": self.path_version,
            "flattened_rows": self.flattened_rows,
            "status": self.status,
        }


class CastRule:
    """取值转型规则。"""

    def __init__(self, rule_id: str, field_name: str, source_type: str,
                 target_type: str, date_pattern: str = "", precision: int = 0,
                 null_fill: Any = None):
        self.rule_id = rule_id
        self.field_name = field_name
        self.source_type = source_type
        self.target_type = target_type
        self.date_pattern = date_pattern
        self.precision = precision
        self.null_fill = null_fill
        self.failure_count = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "field_name": self.field_name,
            "source_type": self.source_type,
            "target_type": self.target_type,
            "date_pattern": self.date_pattern,
            "precision": self.precision,
            "null_fill": str(self.null_fill),
            "failure_count": self.failure_count,
        }


class UnitFactor:
    """单位归一系数。"""

    def __init__(self, factor_id: str, field_name: str, source_unit: str,
                 target_unit: str, multiplier: float, applies_to: str,
                 effective_from: str):
        self.factor_id = factor_id
        self.field_name = field_name
        self.source_unit = source_unit
        self.target_unit = target_unit
        self.multiplier = multiplier
        self.applies_to = applies_to
        self.effective_from = effective_from
        self.status = "已生效"

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "field_name": self.field_name,
            "source_unit": self.source_unit,
            "target_unit": self.target_unit,
            "multiplier": self.multiplier,
            "applies_to": self.applies_to,
            "effective_from": self.effective_from,
            "status": self.status,
        }


class TransformStage:
    """变换级联执行器。"""

    def __init__(self) -> None:
        self._paths: dict[str, FlattenPath] = {}
        self._cast_rules: dict[str, CastRule] = {}
        self._unit_factors: dict[str, UnitFactor] = {}
        self._cast_failures: list[dict[str, Any]] = []
        self._conflicts: list[dict[str, Any]] = []

    # ---------- 嵌套展平 ----------

    def register_flatten_path(self, path: FlattenPath) -> None:
        self._paths[path.path_id] = path

    def flatten_document(self, path_id: str, documents: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]], str]:
        """展平嵌套文档数组。返回 (是否成功, 展平后的扁平行, 结论)。"""
        path = self._paths.get(path_id)
        if path is None:
            return False, [], f"展平路径 {path_id} 不存在"

        segments = parse_path(path.source_path, path.path_version)
        if len(segments) > path.max_depth:
            path.status = "层数超限"
            return False, [], (
                f"路径 {path.source_path} 层级 {len(segments)} 超过上限 {path.max_depth}，"
                f"已登记层数超限并中止展平"
            )

        rows: list[dict[str, Any]] = []
        empty_nodes = 0
        for doc in documents:
            values = self._resolve_path(doc, segments)
            if values is None:
                empty_nodes += 1
                if path.empty_policy == "FILL_NULL":
                    rows.append({col: None for col in path.target_columns})
                continue
            if path.array_mode == "EXPAND":
                for item in values:
                    rows.extend(self._project(item, path.target_columns))
            elif path.array_mode == "ARRAY_TO_STRING":
                joined = ";".join(str(v) for v in values)
                rows.append({
                    path.target_columns[0]: joined,
                    **{c: "" for c in path.target_columns[1:]},
                })
            else:
                rows.append({
                    path.target_columns[0]: values,
                    **{c: "" for c in path.target_columns[1:]},
                })

        total = len(documents)
        if total > 0 and empty_nodes / total > 0.5:
            path.status = "空节点过多"
            path.flattened_rows = len(rows)
            return False, rows, (
                f"路径 {path.source_path} 空节点 {empty_nodes}/{total} 超过半数，"
                f"标记空节点过多，展平结果不可直接供转型消费"
            )

        path.status = "已展平"
        path.flattened_rows = len(rows)
        return True, rows, f"展平完成，{len(documents)} 份文档展开为 {len(rows)} 行"

    def _resolve_path(self, doc: Any, segments: list[Any]) -> Any:
        """按路径逐层解析，遇到数组即收集全部元素。

        新路径版本传入 ``PathSegment``；同时保留旧式字符串段的测试/调用兼容。
        """
        if not segments:
            return [doc]
        head, *rest = segments
        if hasattr(head, "key"):
            key = head.key
            array_marker = bool(head.array_marker)
            expands_array = getattr(head, "expands_array", True)
            array_index = getattr(head, "array_index", None)
        else:
            array_marker = str(head).endswith("[*]")
            key = str(head)[:-3] if array_marker else str(head)
            expands_array = True
            array_index = None
        node = doc.get(key) if isinstance(doc, dict) else None
        if node is None:
            return None
        if array_marker or isinstance(node, list):
            if array_marker and not expands_array and isinstance(node, list):
                if array_index is None or array_index >= len(node):
                    return None
                node = [node[array_index]]
            collected: list[Any] = []
            for item in node if isinstance(node, list) else [node]:
                sub = self._resolve_path(item, rest)
                if sub is not None:
                    collected.extend(sub if isinstance(sub, list) else [sub])
            return collected or None
        return self._resolve_path(node, rest)

    def _project(self, item: Any, target_columns: list[str]) -> list[dict[str, Any]]:
        if not isinstance(item, dict):
            return [{target_columns[0]: item, **{c: "" for c in target_columns[1:]}}]
        if len(target_columns) == 1:
            key = target_columns[0]
            # 目标列名与节点字段同名时直接取值，否则退化为键值串接
            if key in item:
                return [{key: item[key]}]
            joined = ";".join(f"{k}={v}" for k, v in item.items())
            return [{key: joined}]
        return [{col: item.get(col, "") for col in target_columns}]

    # ---------- 取值转型 ----------

    def register_cast_rule(self, rule: CastRule) -> None:
        self._cast_rules[rule.field_name] = rule

    def cast_rows(self, batch_id: str, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """对扁平行执行转型。返回 (成功行, 失败行明细)。"""
        ok_rows: list[dict[str, Any]] = []
        failed_rows: list[dict[str, Any]] = []

        for idx, row in enumerate(rows):
            cast_row: dict[str, Any] = {}
            row_failures: list[str] = []
            for field, raw in row.items():
                rule = self._cast_rules.get(field)
                if rule is None:
                    cast_row[field] = raw
                    continue
                success, value, err = self._cast_one(raw, rule)
                if success:
                    cast_row[field] = value
                else:
                    rule.failure_count += 1
                    row_failures.append(err)
                    cast_row[field] = None
            if row_failures:
                failed_rows.append({
                    "batch_id": batch_id,
                    "row_index": idx,
                    "raw_row": dict(row),
                    "failed_fields": list(row_failures),
                })
                self._cast_failures.append({
                    "batch_id": batch_id, "row_index": idx,
                    "reasons": list(row_failures),
                })
            else:
                cast_row["__row_index"] = idx
                ok_rows.append(cast_row)

        return ok_rows, failed_rows

    def _cast_one(self, raw: Any, rule: CastRule) -> tuple[bool, Any, str]:
        """单值转型：失败返回原因，不用空值填充伪装成功。"""
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if rule.null_fill is None:
                return False, None, f"字段 {rule.field_name} 为空且未配置填充值"
            return True, rule.null_fill, ""

        try:
            if rule.target_type in ("int", "int64"):
                return True, int(str(raw).strip()), ""
            if rule.target_type in ("float", "float64", "decimal"):
                value = float(str(raw).strip())
                if rule.precision:
                    value = round(value, rule.precision)
                return True, value, ""
            if rule.target_type in ("date", "datetime", "datetime64"):
                from datetime import datetime
                text = str(raw).strip()
                pattern = rule.date_pattern or "%Y-%m-%d %H:%M:%S"
                return True, datetime.strptime(text.replace("T", " "), pattern).isoformat(), ""
            if rule.target_type == "category":
                return True, str(raw).strip(), ""
            return True, str(raw).strip(), ""
        except (ValueError, TypeError) as exc:
            return False, None, (
                f"字段 {rule.field_name} 取值 {raw!r} 转型 {rule.target_type} 失败: {exc}"
            )

    def get_cast_failures(self, batch_id: str) -> list[dict[str, Any]]:
        return [f for f in self._cast_failures if f["batch_id"] == batch_id]

    # ---------- 单位归一 ----------

    def register_unit_factor(self, factor: UnitFactor) -> None:
        """注册单位系数。同字段+同来源+同原始单位重复注册即系数冲突。"""
        key = (factor.field_name, factor.applies_to, factor.source_unit)
        existing = None
        for f in self._unit_factors.values():
            if (f.field_name, f.applies_to, f.source_unit) == key and f.status == "已生效":
                existing = f
                break
        if existing is not None and existing.multiplier != factor.multiplier:
            existing.status = "系数冲突"
            factor.status = "系数冲突"
            self._conflicts.append({
                "field_name": factor.field_name,
                "applies_to": factor.applies_to,
                "source_unit": factor.source_unit,
                "conflicting_factors": [existing.factor_id, factor.factor_id],
                "multipliers": [existing.multiplier, factor.multiplier],
            })
        self._unit_factors[factor.factor_id] = factor

    def normalize_units(self, batch_id: str, channel: str,
                        rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按来源归一单位。返回 (归一后的行, 被拒绝归一的字段清单)。"""
        refused: list[dict[str, Any]] = []
        out_rows: list[dict[str, Any]] = []

        for row in rows:
            new_row = dict(row)
            for field, raw in row.items():
                if field.startswith("__"):
                    continue
                factors = [
                    f for f in self._unit_factors.values()
                    if f.field_name == field and f.applies_to in (channel, "*")
                ]
                if not factors:
                    continue
                conflicted = [f for f in factors if f.status == "系数冲突"]
                if conflicted:
                    refused.append({
                        "batch_id": batch_id,
                        "field_name": field,
                        "channel": channel,
                        "raw_value": raw,
                        "reason": (
                            f"字段 {field} 在来源 {channel} 存在系数冲突 "
                            f"{[c.factor_id for c in conflicted]}，拒绝归一并保留原始取值"
                        ),
                    })
                    continue
                active = factors[0]
                if isinstance(raw, (int, float)):
                    new_row[field] = round(raw * active.multiplier, 6)
                    new_row[f"__unit_{field}"] = active.target_unit
            out_rows.append(new_row)

        return out_rows, refused

    def get_conflicts(self) -> list[dict[str, Any]]:
        return list(self._conflicts)

    def snapshot(self) -> dict[str, Any]:
        return {
            "flatten_paths": {k: v.to_dict() for k, v in self._paths.items()},
            "cast_rules": {k: v.to_dict() for k, v in self._cast_rules.items()},
            "unit_factors": {k: v.to_dict() for k, v in self._unit_factors.items()},
            "cast_failures": list(self._cast_failures),
            "unit_conflicts": list(self._conflicts),
        }
