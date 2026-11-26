"""嵌套展平领域服务：将多层嵌套路径解析为扁平列名并处理数组展开逻辑，产出展平路径数据集。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.pipeline.path_contract import (
    ESCAPABLE_DOT,
    LEGACY_DOT,
    normalize_version,
    parse_path,
    preview_choice,
    resolve_matches,
)
from app.services.base import BaseModuleService
from app.store import store


class NestedFlattenService(BaseModuleService):
    MODULE = "nested_flatten"
    ENTITY = "展平路径"
    OPERATION_TERM = "转换"
    LIST_FIELDS = ["展平编号", "关联模板", "原始路径", "路径版本", "展平后列名", "数组处理方式", "最大展开层数", "空节点处置", "展平行数"]
    REQUIRED_FIELDS = ["展平编号", "关联模板", "原始路径"]
    STATUS_ORDER = ["待展平", "待确认", "已确认", "已展平", "层数超限", "空节点过多"]
    ACTION_RULES = {"执行展平": "已展平", "登记超限": "层数超限", "标记空节点过多": "空节点过多"}
    NEGATIVE_ACTIONS = ["登记超限", "标记空节点过多"]

    EXPAND = "EXPAND"
    ARRAY_TO_STRING = "ARRAY_TO_STRING"
    MODES = (EXPAND, ARRAY_TO_STRING)

    SKIP = "SKIP"
    FILL_NULL = "FILL_NULL"
    EMPTY_POLICIES = (SKIP, FILL_NULL)

    # 空节点占比超过该值判定为空节点过多，展平结果不得供取值转型消费
    EMPTY_NODE_RATIO = 0.5

    def _find(self, flatten_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("展平编号") == flatten_no:
                return row
        return None

    def path_depth(self, source_path: str, path_version: Any = None) -> int:
        """计算路径层级数：$.a.b[*].c 记为 3 层。

        未显式给版本时按旧式点路径计算，避免新增转义语法改变历史记录。
        """
        version = normalize_version(path_version, default=LEGACY_DOT) if path_version is not None else LEGACY_DOT
        return len(parse_path(source_path, version))

    @staticmethod
    def stored_path_version(row: dict[str, Any] | None = None) -> str:
        """读取记录固定的路径版本；历史行没有版本字段时回落到 v1。"""
        if row:
            raw = row.get("路径版本") or row.get("path_version") or row.get("parser_version")
            if raw:
                try:
                    return normalize_version(raw)
                except ValueError:
                    return LEGACY_DOT
        return LEGACY_DOT

    def execute_flatten(self, flatten_no: str,
                        documents: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]], str]:
        """执行展平：数组处理方式决定一行展开成几行，产出扁平行清单。

        层数超限与空节点过多都不产出行，并写入对应的异常状态。
        """
        path_row = self._find(flatten_no)
        if path_row is None:
            return False, [], f"展平路径 {flatten_no} 不存在"

        source_path = str(path_row.get("原始路径") or "")
        target_columns = [
            c.strip() for c in str(path_row.get("展平后列名") or "").split(",") if c.strip()
        ]
        array_mode = str(path_row.get("数组处理方式") or self.EXPAND)
        empty_policy = str(path_row.get("空节点处置") or self.SKIP)
        max_depth_text = str(path_row.get("最大展开层数") or "0").strip()
        max_depth = int(max_depth_text) if max_depth_text.isdigit() else 0

        if array_mode not in self.MODES:
            return False, [], f"数组处理方式「{array_mode}」不在允许范围 {list(self.MODES)}"
        if empty_policy not in self.EMPTY_POLICIES:
            return False, [], f"空节点处置「{empty_policy}」不在允许范围 {list(self.EMPTY_POLICIES)}"
        if not target_columns:
            return False, [], f"展平路径 {flatten_no} 未声明展平后列名"

        raw_version = path_row.get("路径版本") or path_row.get("path_version")
        if raw_version:
            try:
                version = normalize_version(raw_version)
            except ValueError as exc:
                return False, [], str(exc)
        else:
            version = LEGACY_DOT
        depth = self.path_depth(source_path, version)
        if max_depth > 0 and depth > max_depth:
            path_row["status"] = "层数超限"
            path_row["pending"] = True
            path_row["abnormal"] = True
            path_row["展平行数"] = "0"
            return False, [], (
                f"路径 {source_path} 层级 {depth} 超过上限 {max_depth}，"
                f"已登记层数超限并中止展平"
            )

        contract_segments = parse_path(source_path, version)
        last_has_array_marker = bool(
            contract_segments and contract_segments[-1].array_marker
        )
        rows: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        empty_nodes = 0
        for doc_index, doc in enumerate(documents):
            matches = resolve_matches(
                doc, source_path, version,
                doc_key=str(doc.get("__doc_key") or doc.get("doc_key") or ""),
                document_index=doc_index,
                source_text=doc.get("__source_text") if isinstance(doc, dict) else None,
            ) if isinstance(doc, dict) else []
            if not matches:
                # 保留旧解析器对普通点路径的兜底行为。
                legacy_segments = [
                    s for s in source_path.replace("$.", "").split(".") if s
                ]
                values = self._resolve(doc, legacy_segments)
                if values is None:
                    empty_nodes += 1
                    if empty_policy == self.FILL_NULL:
                        rows.append({col: None for col in target_columns})
                    continue
                matches = [{"value": value} for value in values]
            for match in matches:
                value = match["value"]
                evidence.append(match)
                if array_mode == self.EXPAND:
                    items = value if (
                        isinstance(value, list) and not last_has_array_marker
                    ) else [value]
                    for item in items:
                        rows.append(self._project(item, target_columns))
                else:
                    values = value if isinstance(value, list) else [value]
                    joined = ";".join(str(v) for v in values)
                    rows.append({
                        target_columns[0]: joined,
                        **{c: "" for c in target_columns[1:]},
                    })

        total = len(documents)
        path_row["最近命中数"] = str(len(evidence))
        path_row["最近命中位置"] = json.dumps(
            [
                {key: match.get(key) for key in (
                    "doc_key", "position", "canonical_position",
                    "line_number", "actual_line", "path_version", "expression")}
                for match in evidence[:50]
            ],
            ensure_ascii=False,
        )
        if total > 0 and empty_nodes / total > self.EMPTY_NODE_RATIO:
            path_row["status"] = "空节点过多"
            path_row["pending"] = True
            path_row["abnormal"] = True
            path_row["展平行数"] = str(len(rows))
            return False, rows, (
                f"路径 {source_path} 空节点 {empty_nodes}/{total} 超过半数，"
                f"展平结果不得供取值转型消费"
            )

        path_row["status"] = "已展平"
        path_row["pending"] = False
        path_row["abnormal"] = False
        path_row["展平行数"] = str(len(rows))
        return True, rows, (
            f"展平完成：{total} 份文档按 {array_mode} 展开为 {len(rows)} 行"
        )

    def _resolve(self, node: Any, segments: list[str]) -> list[Any] | None:
        """按路径逐层解析，遇到 [*] 或列表即收集全部元素。"""
        if not segments:
            return [node]
        head, *rest = segments
        array_marker = head.endswith("[*]")
        key = head[:-3] if array_marker else head
        if not isinstance(node, dict):
            return None
        child = node.get(key)
        if child is None:
            return None
        if array_marker or isinstance(child, list):
            collected: list[Any] = []
            items = child if isinstance(child, list) else [child]
            for item in items:
                sub = self._resolve(item, rest)
                if sub is not None:
                    collected.extend(sub)
            return collected or None
        return self._resolve(child, rest)

    def _project(self, item: Any, target_columns: list[str]) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {target_columns[0]: item, **{c: "" for c in target_columns[1:]}}
        if len(target_columns) == 1:
            return {target_columns[0]: ";".join(f"{k}={v}" for k, v in item.items())}
        return {col: item.get(col, "") for col in target_columns}

    def row_multiplication(self, flatten_no: str, document_count: int) -> dict[str, Any]:
        """输出行倍增比：展平行数 / 文档数，供取值转型与输出契约行数下限核对。"""
        row = self._find(flatten_no)
        if row is None:
            return {"展平编号": flatten_no, "row_count": 0, "multiplier": 0.0}
        text = str(row.get("展平行数") or "0").strip()
        row_count = int(text) if text.isdigit() else 0
        multiplier = row_count / document_count if document_count > 0 else 0.0
        return {
            "展平编号": flatten_no,
            "原始路径": row.get("原始路径"),
            "数组处理方式": row.get("数组处理方式"),
            "document_count": document_count,
            "row_count": row_count,
            "multiplier": round(multiplier, 2),
            "status": row.get("status"),
        }

    def blocks_value_cast(self, flatten_no: str) -> tuple[bool, str]:
        """层数超限或空节点过多时取值转型不得启动。"""
        row = self._find(flatten_no)
        if row is None:
            return True, f"展平路径 {flatten_no} 不存在，取值转型不得启动"
        status = row.get("status")
        if status in ("层数超限", "空节点过多"):
            return True, (
                f"展平路径 {flatten_no} 状态为 {status}（{row.get('原始路径')}），"
                f"取值转型不得启动"
            )
        if status != "已展平":
            return True, f"展平路径 {flatten_no} 尚未展平完成，状态 {status}"
        return False, f"展平路径 {flatten_no} 结果可供取值转型消费"

    # ------------------------------------------------------------------
    # 可转义路径、版本规则、确认位置与幂等目标行
    # ------------------------------------------------------------------

    CONFIRM_TABLE = "path_confirmation"
    TARGET_TABLE = "flatten_target_row"
    DEPLOY_TABLE = "path_rule_deployment"

    @staticmethod
    def _hash(payload: Any) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _next_numeric_id(self, table: str) -> int:
        rows = store.rows(table)
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    def rule_state(self) -> dict[str, Any]:
        """返回编辑器/解析流水线当前规则版本，缺省固定为新版 v2。"""
        rows = store.rows(self.DEPLOY_TABLE)
        if not rows:
            rows.append({
                "id": 1,
                "status": "旧版已生效",
                "pending": False,
                "abnormal": False,
                "deployment_id": "PRD-CURRENT",
                "部署编号": "PRD-CURRENT",
                "active_version": LEGACY_DOT,
                "当前版本": LEGACY_DOT,
                "editor_rule_version": LEGACY_DOT,
                "history": [
                    {"version": LEGACY_DOT, "action": "初始版本", "time": ""},
                ],
                "updated_at": "",
            })
        return rows[-1]

    def deploy_rule_version(self, version: Any = ESCAPABLE_DOT,
                            operator: str = "system",
                            deploy_time: str = "",
                            note: str = "") -> tuple[bool, str, dict[str, Any]]:
        """上线指定解析语法版本；重复上线同一版本保持幂等。"""
        target = normalize_version(version)
        state = self.rule_state()
        current = normalize_version(state.get("active_version"))
        history = list(state.get("history") or [])
        if current == target:
            state["updated_at"] = deploy_time or state.get("updated_at", "")
            return True, f"路径规则已是 {target}，无需重复上线", state
        history.append({
            "from_version": current, "version": target,
            "action": "切换版本", "operator": operator,
            "time": deploy_time, "note": note,
        })
        state["active_version"] = target
        state["当前版本"] = target
        state["editor_rule_version"] = target
        state["status"] = "新版已生效" if target == ESCAPABLE_DOT else "已回滚旧版"
        state["pending"] = False
        state["abnormal"] = target == LEGACY_DOT
        state["history"] = history
        state["updated_at"] = deploy_time
        return True, f"路径解析规则已切换为 {target}", state

    def rollback_editor_rule(self, target_version: Any = LEGACY_DOT,
                             operator: str = "maintainer",
                             rollback_time: str = "",
                             note: str = "编辑器规则回滚") -> tuple[bool, str, dict[str, Any]]:
        """回滚编辑器规则；已发出的回执仍固定其核准时的版本和轮次。"""
        state = self.rule_state()
        previous = normalize_version(state.get("active_version"))
        target = normalize_version(target_version)
        history = list(state.get("history") or [])
        history.append({
            "from_version": previous,
            "version": target,
            "action": "回滚",
            "operator": operator,
            "time": rollback_time,
            "note": note,
        })
        state["active_version"] = target
        state["当前版本"] = target
        state["editor_rule_version"] = target
        state["status"] = "已回滚旧版"
        state["pending"] = False
        state["abnormal"] = True
        state["last_rollback"] = {
            "from_version": previous,
            "to_version": target,
            "operator": operator,
            "time": rollback_time,
            "note": note,
        }
        state["history"] = history
        state["updated_at"] = rollback_time
        return True, f"编辑器规则已从 {previous} 回滚到 {target}", state

    def preview_path(self, expression: str,
                     documents: list[dict[str, Any]] | dict[str, Any],
                     path_version: Any = None,
                     *,
                     doc_key: str = "",
                     source_texts: dict[str, str] | list[str] | str | None = None,
                     line_numbers: dict[str, int] | list[int] | None = None,
                     actual_lines: dict[str, str] | list[str] | None = None) -> dict[str, Any]:
        """只预览、不保存；返回每个原文档中的实际匹配位置。"""
        version = normalize_version(path_version)
        docs = [documents] if isinstance(documents, dict) else list(documents)
        all_matches: list[dict[str, Any]] = []
        document_previews = []
        for index, doc in enumerate(docs):
            preferred_key = doc_key if doc_key and len(docs) == 1 else ""
            doc_key_value = preferred_key
            source_text = source_texts if isinstance(source_texts, str) and len(docs) == 1 else None
            line_number = None
            actual_line = None
            if isinstance(doc, dict):
                doc_key_value = doc_key_value or str(
                    doc.get("__doc_key") or doc.get("doc_key") or f"DOC-{index + 1}")
                source_text = source_text or doc.get("__source_text")
                if source_texts is not None and not isinstance(source_texts, str):
                    source_text = source_texts[doc_key_value] if isinstance(source_texts, dict) else source_texts[index]
                if line_numbers is not None:
                    line_number = line_numbers[doc_key_value] if isinstance(line_numbers, dict) else line_numbers[index]
                if actual_lines is not None:
                    actual_line = actual_lines[doc_key_value] if isinstance(actual_lines, dict) else actual_lines[index]
            else:
                doc_key_value = preferred_key or f"DOC-{index + 1}"
            matches = resolve_matches(
                doc, expression, version,
                doc_key=doc_key_value,
                document_index=index,
                source_text=source_text,
                line_number=line_number,
                actual_line=actual_line,
            )
            all_matches.extend(matches)
            document_previews.append({
                "doc_key": doc_key_value,
                "document_index": index,
                "matched": bool(matches),
                "matches": matches,
            })
        return {
            "ok": bool(all_matches),
            "expression": expression,
            "path_expression_raw": expression,
            "path_version": version,
            "match_count": len(all_matches),
            "matches": all_matches,
            "documents": document_previews,
        }

    def preview_old_and_new(self, document: dict[str, Any] | None = None, *,
                            old_expression: str = "$.a.b",
                            new_expression: str = "$.a\\.b",
                            doc_key: str = "DOC-LITERAL-DOT",
                            source_text: str | None = None) -> dict[str, Any]:
        """并列预览旧点路径 7 与新转义路径 9，供确认页并列选择。"""
        if document is None:
            document = {"a": {"b": 7}, "a.b": 9}
        result = preview_choice(
            document,
            old_expression=old_expression,
            new_expression=new_expression,
            doc_key=doc_key,
            source_text=source_text,
        )
        result["rule_state"] = self.rule_state()
        return result

    def preview_migration(self, document: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """命名别名：迁移预览即新旧路径并列预览。"""
        return self.preview_old_and_new(document, **kwargs)

    def migrate_legacy_path(self, *args: Any, **kwargs: Any):
        """命名别名：发起旧式点路径迁移。"""
        return self.migrate_dot_path(*args, **kwargs)

    def confirm_migration(self, *args: Any, **kwargs: Any):
        """命名别名：服务端按版本确认迁移路径和匹配位置。"""
        return self.confirm_path(*args, **kwargs)

    def rollback_rule(self, *args: Any, **kwargs: Any):
        """命名别名：回滚编辑器/解析路径规则。"""
        return self.rollback_editor_rule(*args, **kwargs)

    def list_path_records(self, *args: Any, **kwargs: Any):
        """命名别名：列出路径确认记录。"""
        return self.list_confirmations(*args, **kwargs)

    def list_confirmed_paths(self, *args: Any, **kwargs: Any):
        return self.list_confirmations(*args, **kwargs)

    def list_confirmations(self, batch_id: str = "",
                           choice_group: str = "") -> list[dict[str, Any]]:
        rows = store.rows(self.CONFIRM_TABLE)
        if batch_id:
            rows = [r for r in rows if r.get("batch_id") == batch_id or r.get("关联批次") == batch_id]
        if choice_group:
            rows = [r for r in rows if r.get("choice_group") == choice_group]
        return list(rows)

    def get_confirmation(self, confirmation_id: str) -> dict[str, Any] | None:
        return next(
            (r for r in store.rows(self.CONFIRM_TABLE)
             if r.get("confirmation_id") == confirmation_id
             or r.get("路径确认编号") == confirmation_id),
            None,
        )

    def _find_confirmed_by_natural_key(self, natural_key: str) -> dict[str, Any] | None:
        return next(
            (r for r in store.rows(self.CONFIRM_TABLE)
             if r.get("natural_key") == natural_key),
            None,
        )

    def confirm_path(self,
                     expression: str,
                     documents: list[dict[str, Any]] | dict[str, Any],
                     path_version: Any,
                     *,
                     confirmation_id: str = "",
                     flatten_no: str = "",
                     batch_id: str = "",
                     target_field: str = "",
                     approved_round: int = 1,
                     selected_index: int = 0,
                     selected_position: str = "",
                     doc_key: str = "",
                     confirmed_by: str = "",
                     confirm_time: str = "",
                     choice_group: str = "",
                     template_id: str = "ET-PATH-CONTRACT",
                     array_mode: str = "EXPAND",
                     source_texts: dict[str, str] | list[str] | None = None,
                     ) -> tuple[bool, str, dict[str, Any] | None]:
        """按调用方声明的版本解析并保存核准位置；服务端绝不接收外部给的核准值。"""
        try:
            version = normalize_version(path_version)
        except ValueError as exc:
            return False, str(exc), None
        preview = self.preview_path(
            expression, documents, version,
            doc_key=doc_key, source_texts=source_texts)
        matches = preview["matches"]
        if doc_key:
            matches = [m for m in matches if m["doc_key"] == doc_key]
        if selected_position:
            position_matches = [
                m for m in matches
                if m["position"] == selected_position
                or m["canonical_position"] == selected_position
                or m["location"] == selected_position
            ]
            if position_matches:
                matches = position_matches
        if not matches:
            return False, f"路径 {expression}（{version}）未匹配到原文档位置，不能确认", None
        if selected_index >= len(matches):
            return False, f"匹配序号 {selected_index} 超出 {len(matches)} 个位置", None
        match = matches[selected_index]

        if not flatten_no:
            adopted_path_row = next((
                row for row in store.rows("nested_flatten")
                if str(row.get("原始路径") or row.get("路径表达式原文") or "") == expression
                and self.stored_path_version(row) == version
            ), None)
            if adopted_path_row is not None:
                flatten_no = str(adopted_path_row.get("展平编号") or "")

        round_no = int(approved_round or 1)
        natural_key = self._hash([
            batch_id, flatten_no, expression, version, round_no,
            match["doc_key"], match["position"], target_field,
        ])
        existing = self._find_confirmed_by_natural_key(natural_key)
        if existing is not None:
            existing["idempotent_replay"] = True
            return True, "该路径位置已经核准，沿用既有确认与目标行", existing

        cid = confirmation_id or f"PC-{self._hash(natural_key)[:12].upper()}"
        if self.get_confirmation(cid) is not None:
            cid = f"{cid}-{natural_key[:6]}"

        if not flatten_no:
            flatten_no = f"NF-PC-{natural_key[:10].upper()}"
        path_row = self._find(flatten_no)
        if path_row is None:
            path_row = {
                "id": self._next_numeric_id("nested_flatten"),
                "status": "已确认", "pending": False, "abnormal": False,
                "展平编号": flatten_no, "关联模板": template_id,
                "原始路径": expression, "展平后列名": target_field or "matched_value",
                "数组处理方式": array_mode, "最大展开层数": "0",
                "空节点处置": "SKIP", "展平行数": "0",
            }
            store.rows("nested_flatten").append(path_row)
        else:
            stored_version = self.stored_path_version(path_row)
            stored_expr = str(path_row.get("原始路径") or "")
            if stored_expr and stored_expr != expression:
                return False, (
                    f"展平路径 {flatten_no} 已固定表达式 {stored_expr!r}，"
                    f"不能用 {expression!r} 混接"
                ), None
            if path_row.get("路径版本") and stored_version != version:
                return False, (
                    f"展平路径 {flatten_no} 已固定版本 {stored_version}，"
                    f"不能按 {version} 混接"
                ), None

        path_row.update({
            "路径版本": version,
            "path_version": version,
            "原始路径原文": expression,
            "status": "已确认",
            "pending": False,
            "abnormal": False,
            "核准轮次": str(round_no),
            "approved_round": round_no,
            "路径确认编号": cid,
            "choice_group": choice_group,
            "最近核准值": json.dumps(match["value"], ensure_ascii=False, default=str),
        })

        target_row = self._ensure_target_row(
            natural_key=natural_key,
            batch_id=batch_id,
            flatten_no=flatten_no,
            confirmation_id=cid,
            target_field=target_field,
            round_no=round_no,
            match=match,
        )
        record = {
            "id": self._next_numeric_id(self.CONFIRM_TABLE),
            "status": "已确认", "pending": False, "abnormal": False,
            "confirmation_id": cid,
            "路径确认编号": cid,
            "natural_key": natural_key,
            "flatten_no": flatten_no,
            "展平编号": flatten_no,
            "batch_id": batch_id,
            "关联批次": batch_id,
            "template_id": template_id,
            "target_field": target_field,
            "转型后字段": target_field,
            "expression": expression,
            "路径表达式原文": expression,
            "原始路径": expression,
            "path_version": version,
            "路径版本": version,
            "approved_round": round_no,
            "获准轮次": round_no,
            "choice_group": choice_group,
            "match": match,
            "matched_value": match["value"],
            "核准原值": match["value"],
            "doc_key": match["doc_key"],
            "文档键": match["doc_key"],
            "position": match["position"],
            "匹配位置": match["position"],
            "canonical_position": match["canonical_position"],
            "json_pointer": match["json_pointer"],
            "line_number": match["line_number"],
            "实际行号": match["line_number"],
            "actual_line": match["actual_line"],
            "实际行": match["actual_line"],
            "confirmed_by": confirmed_by,
            "确认人": confirmed_by,
            "confirm_time": confirm_time,
            "确认时间": confirm_time,
            "target_row_id": target_row["target_row_id"],
            "idempotent_replay": False,
        }
        store.rows(self.CONFIRM_TABLE).append(record)
        return True, f"路径已按 {version} 确认，并保存匹配位置 {match['position']}", record

    def _ensure_target_row(self, *, natural_key: str, batch_id: str,
                           flatten_no: str, confirmation_id: str,
                           target_field: str, round_no: int,
                           match: dict[str, Any]) -> dict[str, Any]:
        """按自然键幂等生成已确认目标行；回滚/续做不得复制。"""
        table = store.rows(self.TARGET_TABLE)
        existing = next((r for r in table if r.get("natural_key") == natural_key), None)
        if existing is not None:
            existing["reused_count"] = int(existing.get("reused_count") or 0) + 1
            return existing
        target_row_id = f"TR-{natural_key[:12].upper()}"
        row = {
            "id": self._next_numeric_id(self.TARGET_TABLE),
            "status": "已确认", "pending": False, "abnormal": False,
            "target_row_id": target_row_id,
            "目标行编号": target_row_id,
            "natural_key": natural_key,
            "batch_id": batch_id,
            "关联批次": batch_id,
            "flatten_no": flatten_no,
            "展平编号": flatten_no,
            "confirmation_id": confirmation_id,
            "路径确认编号": confirmation_id,
            "target_field": target_field,
            "转型后字段": target_field,
            "approved_round": round_no,
            "获准轮次": round_no,
            "path_version": match["path_version"],
            "路径版本": match["path_version"],
            "expression": match["expression"],
            "路径表达式原文": match["expression"],
            "doc_key": match["doc_key"],
            "文档键": match["doc_key"],
            "position": match["position"],
            "匹配位置": match["position"],
            "line_number": match["line_number"],
            "实际行号": match["line_number"],
            "actual_line": match["actual_line"],
            "实际行": match["actual_line"],
            "raw_value": match["value"],
            "原值": match["value"],
            "consumed": False,
            "已消费": False,
            "reused_count": 0,
        }
        table.append(row)
        return row

    def list_target_rows(self, batch_id: str = "",
                         confirmation_id: str = "") -> list[dict[str, Any]]:
        rows = store.rows(self.TARGET_TABLE)
        if batch_id:
            rows = [r for r in rows if r.get("batch_id") == batch_id]
        if confirmation_id:
            rows = [r for r in rows if r.get("confirmation_id") == confirmation_id]
        return list(rows)

    def migrate_dot_path(self,
                         new_expression: str = "$.a\\.b",
                         old_expression: str = "$.a.b",
                         *,
                         flatten_no: str = "",
                         old_flatten_no: str = "",
                         template_id: str = "ET-PATH-CONTRACT",
                         target_field: str = "b",
                         operator: str = "",
                         migration_time: str = "",
                         ) -> tuple[bool, str, dict[str, Any]]:
        """发起旧式点路径迁移；重复发起时核对并返回已采用记录。"""
        adopted = next((
            row for row in store.rows("nested_flatten")
            if self.stored_path_version(row) == ESCAPABLE_DOT
            and str(row.get("原始路径") or row.get("路径表达式原文") or "") == new_expression
            and (not flatten_no or row.get("展平编号") == flatten_no)
        ), None)
        if adopted is not None:
            confirmed = [
                c for c in self.list_confirmations()
                if c.get("flatten_no") == adopted.get("展平编号")
            ]
            adopted["already_adopted"] = True
            return True, (
                "该迁移已有采用记录，回滚与续做不会再次生成已确认目标行"
            ), {
                "path_record": adopted,
                "confirmations": confirmed,
                "already_adopted": True,
            }

        no = flatten_no or f"NF-MIG-{self._hash([old_expression, new_expression, template_id])[:10].upper()}"
        row = {
            "id": self._next_numeric_id("nested_flatten"),
            "status": "待确认", "pending": True, "abnormal": False,
            "展平编号": no, "关联模板": template_id,
            "原始路径": new_expression, "展平后列名": target_field,
            "数组处理方式": "EXPAND", "最大展开层数": "0",
            "空节点处置": "SKIP", "展平行数": "0",
            "路径版本": ESCAPABLE_DOT, "path_version": ESCAPABLE_DOT,
            "迁移自旧路径": old_expression,
            "old_flatten_no": old_flatten_no,
            "迁移发起人": operator,
            "迁移时间": migration_time,
            "already_adopted": False,
        }
        store.rows("nested_flatten").append(row)
        return True, "已创建可转义路径迁移记录，等待路径确认", {
            "path_record": row,
            "confirmations": [],
            "already_adopted": False,
        }

    def get_source_location(self, reference: str) -> dict[str, Any] | None:
        """按确认编号/目标行编号打开实际命中文档位置。"""
        confirmation = self.get_confirmation(reference)
        if confirmation is not None:
            match = dict(confirmation.get("match") or {})
            match.setdefault("doc_key", confirmation.get("doc_key"))
            match.setdefault("position", confirmation.get("position"))
            match.setdefault("line_number", confirmation.get("line_number"))
            match.setdefault("actual_line", confirmation.get("actual_line"))
            return match
        target = next((
            r for r in store.rows(self.TARGET_TABLE)
            if r.get("target_row_id") == reference
            or r.get("目标行编号") == reference
        ), None)
        if target is not None:
            return {key: target.get(key) for key in (
                "doc_key", "position", "line_number", "actual_line",
                "expression", "path_version", "raw_value")}
        # 直接按表名查找，避免与 ValueCastService 形成导入环。
        receipt = next((
            r for r in store.rows("cast_receipt")
            if r.get("receipt_id") == reference
            or r.get("转型回执编号") == reference
        ), None)
        if receipt is not None and isinstance(receipt.get("source"), dict):
            location = dict(receipt["source"])
            location.setdefault("value", receipt.get("raw_value"))
            location.setdefault("raw_value", receipt.get("raw_value"))
            location.setdefault("confirmation_id", receipt.get("confirmation_id"))
            return location
        return None
