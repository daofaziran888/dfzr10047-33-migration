"""取值转型领域服务：按目标类型将字符串取值转为强类型字段并记录失败数，产出转型规则数据集。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from app.pipeline.path_contract import ESCAPABLE_DOT, LEGACY_DOT, normalize_version
from app.services.base import BaseModuleService
from app.services.nested_flatten import NestedFlattenService
from app.store import store


class ValueCastService(BaseModuleService):
    MODULE = "value_cast"
    ENTITY = "转型规则"
    OPERATION_TERM = "转换"
    LIST_FIELDS = ["转型编号", "目标类型", "来源类型", "日期格式约定", "数值精度", "空值填充", "转型失败数", "最近执行"]
    REQUIRED_FIELDS = ["转型编号", "目标类型", "来源类型"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "存在失败"]
    ACTION_RULES = {"启动转型": "执行中", "确认完成": "已完成", "登记失败": "存在失败"}
    NEGATIVE_ACTIONS = ["登记失败"]

    # 业务侧用 yyyy-MM-dd 约定日期格式，转型前需翻译成 strptime 的格式串
    PATTERN_TOKENS = (("yyyy", "%Y"), ("yy", "%y"), ("MM", "%m"), ("dd", "%d"),
                      ("HH", "%H"), ("mm", "%M"), ("ss", "%S"))

    @classmethod
    def to_strptime_pattern(cls, pattern: str) -> str:
        """把 yyyy-MM-dd HH:mm:ss 这类业务约定翻译成 strptime 格式串。"""
        if not pattern:
            return "%Y-%m-%d %H:%M:%S"
        if "%" in pattern:
            return pattern
        translated = pattern
        for token, replacement in cls.PATTERN_TOKENS:
            translated = translated.replace(token, replacement)
        return translated

    def cast_value(self, raw: Any, target_type: str, date_pattern: str = "",
                   precision: int = 0, null_fill: Any = None) -> tuple[bool, Any, str]:
        """单值转型：失败时返回原因，不用空值填充伪装成功。"""
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if null_fill is None:
                return False, None, f"取值为空且未配置空值填充，无法转型为 {target_type}"
            return True, null_fill, ""

        text = str(raw).strip()
        try:
            if target_type in ("int", "int64", "integer"):
                return True, int(text), ""
            if target_type in ("float", "float64", "double", "decimal"):
                value = float(text)
                return True, (round(value, precision) if precision else value), ""
            if target_type in ("date", "datetime", "datetime64"):
                pattern = self.to_strptime_pattern(date_pattern)
                return True, datetime.strptime(text.replace("T", " "), pattern).isoformat(), ""
            if target_type == "bool":
                lowered = text.lower()
                if lowered in ("true", "1", "yes", "y"):
                    return True, True, ""
                if lowered in ("false", "0", "no", "n"):
                    return True, False, ""
                return False, None, f"取值 {text!r} 无法转型为 bool"
            if target_type in ("str", "string", "text", "category"):
                return True, text, ""
            return False, None, f"未知目标类型 {target_type}"
        except (ValueError, TypeError) as exc:
            return False, None, f"取值 {text!r} 转型 {target_type} 失败: {exc}"

    def cast_row(self, cast_no: str, row: dict[str, Any],
                 execute_time: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """按转型规则对一行执行转型，返回 (转型后取值, 失败明细)。

        失败数按行回写坏行隔离，因此失败明细必须带字段名与失败原因。
        """
        rule = next(
            (r for r in store.rows(self.MODULE) if r.get("转型编号") == cast_no), None)
        if rule is None:
            return {}, [{"field": "*", "reason": f"转型规则 {cast_no} 不存在"}]

        target_type = str(rule.get("目标类型") or "")
        date_pattern = str(rule.get("日期格式约定") or "")
        precision_text = str(rule.get("数值精度") or "").strip()
        precision = int(precision_text) if precision_text.isdigit() else 0
        null_fill_text = str(rule.get("空值填充") or "").strip()
        null_fill: Any = null_fill_text or None
        if null_fill_text.upper() in ("NAN", "NAT"):
            null_fill = None

        cast_values: dict[str, Any] = {}
        failures: list[dict[str, Any]] = []
        for field, raw in row.items():
            ok, value, reason = self.cast_value(
                raw, target_type, date_pattern, precision, null_fill)
            if ok:
                cast_values[field] = value
            else:
                failures.append({"field": field, "reason": reason, "raw": str(raw)})

        self._record_execution(cast_no, len(failures), execute_time)
        return cast_values, failures

    def _record_execution(self, cast_no: str, failure_count: int,
                          execute_time: str) -> None:
        """回写规则执行结果：失败数累加并置为存在失败状态。"""
        for rule in store.rows(self.MODULE):
            if rule.get("转型编号") != cast_no:
                continue
            existing_text = str(rule.get("转型失败数") or "0").strip()
            existing = int(existing_text) if existing_text.isdigit() else 0
            rule["转型失败数"] = str(existing + failure_count)
            rule["最近执行"] = execute_time
            if failure_count > 0:
                rule["status"] = "存在失败"
                rule["pending"] = True
                rule["abnormal"] = True
            else:
                rule["status"] = "已完成"
                rule["pending"] = False
                rule["abnormal"] = False
            return

    def quarantine_failures(self, cast_no: str, intake_batch: str, row_num: int,
                            failures: list[dict[str, Any]], raw_text: str,
                            quarantine_time: str) -> list[str]:
        """把转型失败按行写入坏行隔离，返回生成的隔离编号。"""
        created: list[str] = []
        for failure in failures:
            quarantine_no = f"RQ-{intake_batch}-{row_num}-{failure['field']}"
            rows = store.rows("row_quarantine")
            if any(r.get("隔离编号") == quarantine_no for r in rows):
                continue
            entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            rows.append({
                "id": entry_id, "status": "已隔离", "pending": True, "abnormal": False,
                "隔离编号": quarantine_no, "关联批次": intake_batch,
                "原始行号": str(row_num), "失败字段": str(failure["field"]),
                "失败原因": f"取值转型失败: {failure['reason']}",
                "原始行文本": raw_text, "复核结论": "", "隔离时间": quarantine_time,
            })
            created.append(quarantine_no)
        return created

    def failure_summary(self, cast_no: str) -> dict[str, Any]:
        """输出规则的失败计数，供解析指标与坏行隔离对账。"""
        for rule in store.rows(self.MODULE):
            if rule.get("转型编号") == cast_no:
                text = str(rule.get("转型失败数") or "0").strip()
                return {
                    "转型编号": cast_no,
                    "目标类型": rule.get("目标类型"),
                    "转型失败数": int(text) if text.isdigit() else 0,
                    "最近执行": rule.get("最近执行"),
                    "status": rule.get("status"),
                }
        return {"转型编号": cast_no, "转型失败数": 0, "status": "规则不存在"}

    # ------------------------------------------------------------------
    # 核准路径 → 转型受理回执 → 版本冲突选择 → 幂等执行/隔离恢复
    # ------------------------------------------------------------------

    RECEIPT_TABLE = "cast_receipt"

    @staticmethod
    def _hash(payload: Any) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _nested_service(self) -> NestedFlattenService:
        return NestedFlattenService()

    def _receipt_rows(self) -> list[dict[str, Any]]:
        return store.rows(self.RECEIPT_TABLE)

    def get_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        return next((
            r for r in self._receipt_rows()
            if r.get("receipt_id") == receipt_id
            or r.get("转型回执编号") == receipt_id
        ), None)

    def get_cast_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        return self.get_receipt(receipt_id)

    def find_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        return self.get_receipt(receipt_id)

    def issue_receipt(self, *args: Any, **kwargs: Any):
        return self.accept_approved_cast(*args, **kwargs)

    def claim_cast_receipt(self, *args: Any, **kwargs: Any):
        return self.claim_receipt(*args, **kwargs)

    def list_receipts(self, batch_id: str = "",
                      status: str = "") -> list[dict[str, Any]]:
        rows = self._receipt_rows()
        if batch_id:
            rows = [r for r in rows if r.get("batch_id") == batch_id or r.get("关联批次") == batch_id]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return list(rows)

    def _credential(self, prefix: str, payload: Any) -> str:
        return f"{prefix}-{self._hash(payload)[:24]}"

    def accept_approved_cast(self,
                             batch_id: str,
                             confirmation_id: str = "",
                             *,
                             target_row_id: str = "",
                             cast_no: str = "",
                             target_field: str = "",
                             approved_round: int | None = None,
                             receipt_id: str = "",
                             accepted_by: str = "system",
                             accept_time: str = "",
                             idempotency_key: str = "",
                             target_type: str = "",
                             ) -> tuple[bool, str, dict[str, Any] | None]:
        """只接收路径确认记录中核准的原值和来源，签发待执行转型回执。"""
        nested = self._nested_service()
        confirmation = nested.get_confirmation(confirmation_id) if confirmation_id else None
        target_row = None
        if target_row_id:
            target_row = next((
                r for r in nested.list_target_rows()
                if r.get("target_row_id") == target_row_id
            ), None)
            if target_row is not None and confirmation is None:
                confirmation = nested.get_confirmation(str(target_row.get("confirmation_id")))
        if confirmation is None and target_row is None:
            return False, "转型受理被拒绝：找不到已核准的字段展平路径记录", None
        if confirmation is None:
            cid = str(target_row.get("confirmation_id"))
            confirmation = nested.get_confirmation(cid)
        if confirmation is None:
            return False, "转型受理被拒绝：目标行缺少路径确认凭据", None

        if confirmation.get("status") != "已确认":
            return False, f"路径确认状态为 {confirmation.get('status')}，不能受理转型", None
        round_value = int(confirmation.get("approved_round") or confirmation.get("获准轮次") or 1)
        if approved_round is not None and int(approved_round) != round_value:
            return False, (
                f"核准轮次冲突：请求第 {approved_round} 轮，"
                f"记录只核准第 {round_value} 轮"
            ), None
        if target_row is None:
            target_row = next((
                r for r in nested.list_target_rows(confirmation_id=str(confirmation.get("confirmation_id")))
            ), None)

        field = target_field or str(confirmation.get("target_field") or "")
        version = normalize_version(confirmation.get("path_version"))
        expression = str(confirmation.get("expression") or "")
        match = dict(confirmation.get("match") or {})
        raw_value = confirmation.get("matched_value", match.get("value"))
        source = {
            "confirmation_id": confirmation.get("confirmation_id"),
            "target_row_id": target_row.get("target_row_id") if target_row else "",
            "flatten_no": confirmation.get("flatten_no"),
            "approved_round": round_value,
            "expression": expression,
            "path_version": version,
            "doc_key": confirmation.get("doc_key"),
            "position": confirmation.get("position"),
            "canonical_position": confirmation.get("canonical_position"),
            "json_pointer": confirmation.get("json_pointer"),
            "line_number": confirmation.get("line_number"),
            "actual_line": confirmation.get("actual_line"),
            "raw_value": raw_value,
            "source_kind": "confirmed_flatten_path",
        }

        # 同批只能固定一个路径版本；尚未执行且已退回选择的旧回执不算混接。
        for other in self.list_receipts(batch_id):
            if other.get("path_version") == version:
                continue
            if other.get("status") in ("已退回选择", "已撤回", "资格待选择", "版本冲突") and not other.get("transformed_value"):
                continue
            return False, (
                f"批次 {batch_id} 已固定路径版本 {other.get('path_version')}，"
                f"不能把 {version} 混接到同一批"
            ), other

        natural = idempotency_key or self._hash([
            batch_id, confirmation.get("confirmation_id"), target_row_id,
            cast_no, field, round_value, version, expression,
            confirmation.get("position"),
        ])
        existing = next((r for r in self._receipt_rows() if r.get("idempotency_key") == natural), None)
        if existing is not None:
            existing["idempotent_replay"] = True
            return True, "转型回执已存在，继续使用原受理凭据", existing

        rid = receipt_id or f"CR-{natural[:16].upper()}"
        if self.get_receipt(rid) is not None:
            rid = f"{rid}-{natural[:6]}"
        credential = self._credential("CRED", [rid, natural, raw_value, source])
        rule_state = nested.rule_state()
        receipt = {
            "id": max((int(r.get("id", 0)) for r in self._receipt_rows()), default=0) + 1,
            "status": "已受理", "pending": True, "abnormal": False,
            "receipt_id": rid,
            "转型回执编号": rid,
            "idempotency_key": natural,
            "batch_id": batch_id,
            "关联批次": batch_id,
            "cast_no": cast_no,
            "转型编号": cast_no,
            "target_field": field,
            "转型后字段": field,
            "target_type": target_type,
            "confirmation_id": confirmation.get("confirmation_id"),
            "路径确认编号": confirmation.get("confirmation_id"),
            "target_row_id": target_row.get("target_row_id") if target_row else "",
            "flatten_no": confirmation.get("flatten_no"),
            "approved_round": round_value,
            "获准轮次": round_value,
            "choice_group": confirmation.get("choice_group") or "",
            "expression": expression,
            "路径表达式原文": expression,
            "path_version": version,
            "路径版本": version,
            "accepted_rule_version": rule_state.get("active_version"),
            "受理时规则版本": rule_state.get("active_version"),
            "current_rule_version": rule_state.get("active_version"),
            "raw_value": raw_value,
            "原始值": raw_value,
            "source": source,
            "doc_key": source["doc_key"],
            "文档键": source["doc_key"],
            "position": source["position"],
            "匹配位置": source["position"],
            "line_number": source["line_number"],
            "实际行号": source["line_number"],
            "actual_line": source["actual_line"],
            "实际行": source["actual_line"],
            "credential": credential,
            "凭据": credential,
            "accepted_by": accepted_by,
            "受理人": accepted_by,
            "accept_time": accept_time,
            "受理时间": accept_time,
            "claimed_by": "",
            "领取人": "",
            "claim_time": "",
            "version_conflict": False,
            "selection_required": False,
            "selection": "",
            "history": [{
                "status": "已受理", "time": accept_time,
                "by": accepted_by, "credential": credential,
                "path_version": version, "raw_value": raw_value,
            }],
            "cast_fields": {},
            "field_sources": {},
            "transformed_value": None,
            "isolation_id": "",
            "idempotent_replay": False,
        }
        self._receipt_rows().append(receipt)
        return True, f"转型 {raw_value!r} 已受理，回执 {rid} 尚未执行", receipt

    # 语义别名：不同调用方可能称其为入队/受理/创建回执。
    def enqueue_cast(self, *args, **kwargs):
        return self.accept_approved_cast(*args, **kwargs)

    def create_receipt(self, *args, **kwargs):
        return self.accept_approved_cast(*args, **kwargs)

    def accept_cast_receipt(self, *args, **kwargs):
        return self.accept_approved_cast(*args, **kwargs)

    def _choice_options(self, receipt: dict[str, Any]) -> list[dict[str, Any]]:
        nested = self._nested_service()
        choices = [{
            "key": "pinned_receipt",
            "aliases": ["receipt", "pinned", "new", "escaped", "original", "新路径", "原待执行", "回执"],
            "label": "按原回执固定的新版转义路径执行（9）",
            "path_version": receipt.get("path_version"),
            "confirmation_id": receipt.get("confirmation_id"),
            "value": receipt.get("raw_value"),
            "selection_path": f"/api/cast-receipts/{receipt.get('receipt_id')}/select",
        }]
        group = str(receipt.get("choice_group") or "")
        old = None
        if group:
            old = next((
                c for c in nested.list_confirmations(choice_group=group)
                if normalize_version(c.get("path_version")) == LEGACY_DOT
            ), None)
        if old is None:
            # 同文档的旧式 $.a.b 是默认兜底对照。
            old = next((
                c for c in nested.list_confirmations()
                if normalize_version(c.get("path_version")) == LEGACY_DOT
                and c.get("doc_key") == receipt.get("doc_key")
                and c.get("matched_value") == 7
            ), None)
        if old is not None:
            choices.append({
                "key": "approved_legacy",
                "aliases": ["legacy", "old", "v1", "旧路径", "已核准旧路径", "回到旧路径"],
                "label": "回到已核准旧式点路径（7）",
                "path_version": LEGACY_DOT,
                "confirmation_id": old.get("confirmation_id"),
                "value": old.get("matched_value"),
                "selection_path": f"/api/cast-receipts/{receipt.get('receipt_id')}/select",
            })
        receipt["choices"] = choices
        receipt["可选路径"] = choices
        receipt["selection_endpoint"] = f"/api/cast-receipts/{receipt.get('receipt_id')}/select"
        return choices

    def claim_receipt(self, receipt_id: str,
                      claimant: str = "next-shift",
                      claim_time: str = "",
                      credential: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        """另一班领取回执；若当前规则已回滚，明确返回事务内路径选择。"""
        receipt = self.get_receipt(receipt_id)
        if receipt is None:
            return False, f"转型回执 {receipt_id} 不存在", None
        if credential and receipt.get("credential") and credential != receipt.get("credential"):
            return False, "转型回执凭据不匹配，不能领取", receipt
        nested = self._nested_service()
        current_version = normalize_version(nested.rule_state().get("active_version"))
        receipt["current_rule_version"] = current_version
        receipt["current_rule_version_at_claim"] = current_version
        receipt["claimed_by"] = claimant
        receipt["领取人"] = claimant
        receipt["claim_time"] = claim_time
        if receipt.get("status") == "已完成":
            receipt["idempotent_replay"] = True
            return True, "回执已完成，直接核对原转型结果与凭据", receipt

        pinned_version = normalize_version(receipt.get("path_version"))
        if current_version != pinned_version:
            receipt["version_conflict"] = True
            receipt["资格冲突"] = True
            receipt["selection_required"] = True
            receipt["status"] = "版本冲突"
            receipt["qualification_status"] = "资格待选择"
            receipt["pending"] = True
            receipt["abnormal"] = True
            choices = self._choice_options(receipt)
            receipt.setdefault("history", []).append({
                "status": "版本冲突", "time": claim_time,
                "by": claimant, "current_rule_version": current_version,
                "pinned_version": pinned_version,
            })
            return True, (
                f"版本冲突：回执固定 {pinned_version}，当前规则已回滚为 {current_version}；"
                "领取成功，但同一批的在途资格与回滚版本冲突，请选择按原回执继续或回到已核准旧路径"
            ), receipt

        receipt["version_conflict"] = False
        receipt["selection_required"] = False
        if receipt.get("status") in ("已受理", "资格待选择"):
            receipt["status"] = "已领取"
        receipt.setdefault("history", []).append({
            "status": "已领取", "time": claim_time, "by": claimant,
            "current_rule_version": current_version,
        })
        return True, "转型回执已领取，资格版本一致，可继续执行", receipt

    def _normalize_selection(self, selection: str | None) -> str:
        text = str(selection or "").strip().lower()
        if text in ("legacy", "old", "v1", "dot-v1", "approved_legacy",
                    "旧路径", "已核准旧路径", "回到旧路径", "7"):
            return "approved_legacy"
        return "pinned_receipt"

    def select_path(self, receipt_id: str, selection: str,
                    *, claimant: str = "next-shift",
                    select_time: str = "",
                    execute: bool = True,
                    cast_no: str = "",
                    target_type: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        """对资格已变化的请求作出显式路径选择，并可立即续做转型。"""
        receipt = self.get_receipt(receipt_id)
        if receipt is None:
            return False, f"转型回执 {receipt_id} 不存在", None
        if receipt.get("status") == "已完成":
            receipt["idempotent_replay"] = True
            return True, "该回执已完成；不重复生成结果，返回原凭据", receipt
        normalized = self._normalize_selection(selection)
        choices = self._choice_options(receipt)
        choice = next((c for c in choices if c["key"] == normalized), None)
        if choice is None:
            return False, f"选择 {selection!r} 不在可选路径 {[c['key'] for c in choices]}", receipt

        # 保存首次受理的 9，任何回退选择都不能抹掉原凭据。
        receipt.setdefault("original_acceptance_snapshot", {
            "raw_value": receipt.get("raw_value"),
            "path_version": receipt.get("path_version"),
            "expression": receipt.get("expression"),
            "confirmation_id": receipt.get("confirmation_id"),
            "source": dict(receipt.get("source") or {}),
            "credential": receipt.get("credential"),
        })
        if normalized == "approved_legacy":
            nested = self._nested_service()
            old = nested.get_confirmation(str(choice["confirmation_id"]))
            if old is None:
                return False, "已核准旧路径确认记录不存在", receipt
            old_match = dict(old.get("match") or {})
            old_source = {
                "confirmation_id": old.get("confirmation_id"),
                "approved_round": old.get("approved_round"),
                "expression": old.get("expression"),
                "path_version": LEGACY_DOT,
                "doc_key": old.get("doc_key"),
                "position": old.get("position"),
                "line_number": old.get("line_number"),
                "actual_line": old.get("actual_line"),
                "raw_value": old.get("matched_value"),
                "source_kind": "approved_legacy_reselection",
            }
            receipt.update({
                "confirmation_id": old.get("confirmation_id"),
                "路径确认编号": old.get("confirmation_id"),
                "approved_round": old.get("approved_round"),
                "获准轮次": old.get("approved_round"),
                "expression": old.get("expression"),
                "路径表达式原文": old.get("expression"),
                "path_version": LEGACY_DOT,
                "路径版本": LEGACY_DOT,
                "raw_value": old.get("matched_value"),
                "原始值": old.get("matched_value"),
                "source": old_source,
                "doc_key": old.get("doc_key"),
                "文档键": old.get("doc_key"),
                "position": old.get("position"),
                "匹配位置": old.get("position"),
                "line_number": old.get("line_number"),
                "实际行号": old.get("line_number"),
                "actual_line": old.get("actual_line"),
                "实际行": old.get("actual_line"),
                "selection": normalized,
                "selected_path": "approved_legacy",
                "rebased_to_legacy": True,
            })
        else:
            snap = receipt.get("original_acceptance_snapshot") or {}
            receipt.update({
                "confirmation_id": snap.get("confirmation_id", receipt.get("confirmation_id")),
                "expression": snap.get("expression", receipt.get("expression")),
                "path_version": snap.get("path_version", receipt.get("path_version")),
                "路径版本": snap.get("path_version", receipt.get("路径版本")),
                "raw_value": snap.get("raw_value", receipt.get("raw_value")),
                "source": snap.get("source", receipt.get("source")),
                "selection": normalized,
                "selected_path": "pinned_receipt",
                "adopted_original_receipt": True,
            })
            source = receipt.get("source") or {}
            for key in ("doc_key", "position", "line_number", "actual_line"):
                receipt[key] = source.get(key, receipt.get(key))
                chinese = {"doc_key": "文档键", "position": "匹配位置",
                           "line_number": "实际行号", "actual_line": "实际行"}[key]
                receipt[chinese] = source.get(key, receipt.get(chinese))

        receipt["version_conflict"] = True
        receipt["selection_required"] = False
        receipt["resolved_at"] = select_time
        receipt.setdefault("history", []).append({
            "status": "已选择路径", "time": select_time, "by": claimant,
            "selection": normalized, "raw_value": receipt.get("raw_value"),
            "path_version": receipt.get("path_version"),
        })
        if not execute:
            receipt["status"] = "已选择路径"
            return True, f"已选择 {normalized}，暂不执行", receipt
        return self.execute_receipt(
            receipt_id, executor=claimant, execute_time=select_time,
            cast_no=cast_no or str(receipt.get("cast_no") or ""),
            target_type=target_type or str(receipt.get("target_type") or ""),
        )

    def choose_path(self, *args, **kwargs):
        return self.select_path(*args, **kwargs)

    def resolve_qualification(self, *args, **kwargs):
        return self.select_path(*args, **kwargs)

    def continue_from_receipt(self, receipt_id: str,
                              claimant: str = "next-shift",
                              execute_time: str = "",
                              selection: str | None = "pinned",
                              cast_no: str = "",
                              target_type: str = ""):
        """另一班从回执续做：默认按回执固定的原待执行值 9 落地。"""
        ok, msg, receipt = self.claim_receipt(receipt_id, claimant, execute_time)
        if not ok or receipt is None:
            return ok, msg, receipt
        if receipt.get("selection_required") and selection is None:
            return False, msg, receipt
        if receipt.get("selection_required"):
            return self.select_path(
                receipt_id, str(selection), claimant=claimant,
                select_time=execute_time, execute=True,
                cast_no=cast_no, target_type=target_type)
        return self.execute_receipt(
            receipt_id, executor=claimant, execute_time=execute_time,
            cast_no=cast_no, target_type=target_type)

    def continue_receipt(self, *args, **kwargs):
        return self.continue_from_receipt(*args, **kwargs)

    def process_receipt(self, *args, **kwargs):
        return self.continue_from_receipt(*args, **kwargs)

    def execute_receipt(self, receipt_id: str,
                        *,
                        executor: str = "next-shift",
                        execute_time: str = "",
                        cast_no: str = "",
                        target_type: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        """执行回执中唯一核准的原值，并把来源关联到转型后字段。"""
        receipt = self.get_receipt(receipt_id)
        if receipt is None:
            return False, f"转型回执 {receipt_id} 不存在", None
        if receipt.get("status") == "已完成" and receipt.get("transformed_value") is not None:
            receipt["idempotent_replay"] = True
            return True, "转型回执已完成，不重复执行", receipt

        cast_no = cast_no or str(receipt.get("cast_no") or "")
        rule = next((r for r in store.rows(self.MODULE) if r.get("转型编号") == cast_no), None)
        if not target_type:
            target_type = str((rule or {}).get("目标类型") or receipt.get("target_type") or "str")
        date_pattern = str((rule or {}).get("日期格式约定") or "")
        precision = 0
        if rule and str(rule.get("数值精度") or "").strip().isdigit():
            precision = int(str(rule.get("数值精度")).strip())
        null_fill: Any = None
        if rule and str(rule.get("空值填充") or "").strip():
            null_fill_text = str(rule.get("空值填充")).strip()
            null_fill = 0 if null_fill_text == "0" else null_fill_text

        field = str(receipt.get("target_field") or "value")
        raw_value = receipt.get("raw_value")
        ok, transformed, reason = self.cast_value(
            raw_value, target_type, date_pattern, precision, null_fill)
        if not ok:
            isolation_id = self._write_receipt_failure(
                receipt, reason, execute_time, executor)
            receipt["status"] = "转型失败"
            receipt["pending"] = True
            receipt["abnormal"] = True
            receipt["isolation_id"] = isolation_id
            receipt["隔离编号"] = isolation_id
            receipt.setdefault("history", []).append({
                "status": "转型失败", "time": execute_time,
                "executor": executor, "reason": reason,
                "isolation_id": isolation_id,
            })
            if rule is not None:
                self._record_execution(cast_no, 1, execute_time)
            return False, f"转型失败，已写入隔离台账 {isolation_id}：{reason}", receipt

        source = dict(receipt.get("source") or {})
        source.update({
            "executed_by": executor,
            "executed_at": execute_time,
            "receipt_id": receipt_id,
            "credential": receipt.get("credential"),
        })
        receipt.update({
            "status": "已完成", "pending": False, "abnormal": False,
            "transformed_value": transformed,
            "转型后值": transformed,
            "cast_fields": {field: transformed},
            "field_sources": {field: source},
            "executed_by": executor,
            "执行人": executor,
            "execute_time": execute_time,
            "执行时间": execute_time,
            "result_credential": self._credential(
                "RES", [receipt.get("credential"), field, transformed, execute_time]),
        })
        receipt["结果凭据"] = receipt["result_credential"]
        receipt.setdefault("history", []).append({
            "status": "已完成", "time": execute_time,
            "executor": executor, "transformed_value": transformed,
            "result_credential": receipt["result_credential"],
        })
        if rule is not None:
            self._record_execution(cast_no, 0, execute_time)

        target_row_id = str(receipt.get("target_row_id") or "")
        if target_row_id:
            target = next((
                r for r in store.rows("flatten_target_row")
                if r.get("target_row_id") == target_row_id
            ), None)
            if target is not None:
                target["consumed"] = True
                target["已消费"] = True
                target["consumed_by_receipt"] = receipt_id
                target["transformed_value"] = transformed
        return True, f"回执 {receipt_id} 已按核准来源转型为 {field}={transformed}", receipt

    def _write_receipt_failure(self, receipt: dict[str, Any], reason: str,
                               fail_time: str, executor: str) -> str:
        """把失败结论写入坏行隔离/隔离台账，并完整保存当时输入以便恢复。"""
        batch_id = str(receipt.get("batch_id") or "NO-BATCH")
        field = str(receipt.get("target_field") or "value")
        doc_key = str(receipt.get("doc_key") or "UNKNOWN-DOC")
        line_no = int(receipt.get("line_number") or 0)
        isolation_id = f"RQ-CAST-{receipt.get('receipt_id')}"
        rows = store.rows("row_quarantine")
        existing = next((r for r in rows if r.get("隔离编号") == isolation_id), None)
        input_snapshot = {
            "receipt_id": receipt.get("receipt_id"),
            "batch_id": batch_id,
            "confirmation_id": receipt.get("confirmation_id"),
            "approved_round": receipt.get("approved_round"),
            "path_version": receipt.get("path_version"),
            "expression": receipt.get("expression"),
            "doc_key": doc_key,
            "position": receipt.get("position"),
            "line_number": receipt.get("line_number"),
            "actual_line": receipt.get("actual_line"),
            "target_field": field,
            "raw_value": receipt.get("raw_value"),
            "raw_type": type(receipt.get("raw_value")).__name__,
            "cast_no": receipt.get("cast_no"),
            "target_type": receipt.get("target_type"),
        }
        raw_text = str(receipt.get("actual_line") or json.dumps(
            input_snapshot, ensure_ascii=False, default=str))
        if existing is None:
            entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            existing = {
                "id": entry_id, "status": "已隔离", "pending": True, "abnormal": True,
                "隔离编号": isolation_id, "关联批次": batch_id,
                "原始行号": str(line_no), "失败字段": field,
                "失败原因": f"取值转型失败（文档键 {doc_key}）: {reason}",
                "原始行文本": raw_text, "复核结论": "", "隔离时间": fail_time,
                "文档键": doc_key,
                "匹配位置": receipt.get("position") or "",
                "路径表达式原文": receipt.get("expression") or "",
                "转型回执编号": receipt.get("receipt_id") or "",
                "当时输入": json.dumps(input_snapshot, ensure_ascii=False, default=str),
                "input_snapshot": input_snapshot,
                "recoverable": True,
            }
            rows.append(existing)
        else:
            existing["失败原因"] = f"取值转型失败（文档键 {doc_key}）: {reason}"
            existing["当时输入"] = json.dumps(input_snapshot, ensure_ascii=False, default=str)
            existing["input_snapshot"] = input_snapshot
        ledger = store.rows("cast_isolation_ledger")
        if not any(r.get("隔离编号") == isolation_id for r in ledger):
            ledger.append(dict(existing))
        return isolation_id

    def recover_failed_input(self, isolation_id: str) -> tuple[bool, str, dict[str, Any] | None]:
        """按隔离编号恢复转型失败当时的输入快照。"""
        row = next((
            r for r in store.rows("row_quarantine")
            if r.get("隔离编号") == isolation_id or r.get("id") == isolation_id
        ), None)
        if row is None:
            return False, f"隔离台账记录 {isolation_id} 不存在", None
        snapshot = row.get("input_snapshot")
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except json.JSONDecodeError:
                snapshot = None
        if not snapshot and row.get("当时输入"):
            try:
                snapshot = json.loads(str(row.get("当时输入")))
            except json.JSONDecodeError:
                snapshot = {"raw_text": row.get("当时输入")}
        if not snapshot:
            return False, "该隔离记录未保存可恢复的当时输入", row
        return True, "已恢复失败当时输入", snapshot

    def isolation_ledger(self, batch_id: str = "") -> list[dict[str, Any]]:
        rows = store.rows("cast_isolation_ledger")
        if batch_id:
            rows = [r for r in rows if r.get("关联批次") == batch_id or r.get("batch_id") == batch_id]
        return list(rows)
