"""路径迁移—确认—转型回执联动工作台的应用服务。"""
from __future__ import annotations

from typing import Any

from app.pipeline.path_contract import ESCAPABLE_DOT
from app.services.nested_flatten import NestedFlattenService
from app.services.value_cast import ValueCastService


LITERAL_DOT_DOCUMENT: dict[str, Any] = {"a": {"b": 7}, "a.b": 9}
LITERAL_DOT_SOURCE = """{
  "a": {
    "b": 7
  },
  "a.b": 9
}
"""


class PathWorkbenchService:
    """组合嵌套展平服务与取值转型服务，沉淀完整在途回执场景。"""

    def __init__(self) -> None:
        self.paths = NestedFlattenService()
        self.casts = ValueCastService()

    def rollback_and_claim(self, receipt_id: str = "CR-NEW-AB-9",
                           claimant: str = "next-shift",
                           rollback_time: str = "",
                           claim_time: str = "") -> dict[str, Any]:
        """在另一页回滚后，由另一班领取同一回执并返回冲突选项。"""
        _ok, rollback_message, rule_state = self.paths.rollback_editor_rule(
            rollback_time=rollback_time)
        ok, claim_message, receipt = self.casts.claim_receipt(
            receipt_id, claimant=claimant, claim_time=claim_time)
        return {
            "ok": ok,
            "rollback_message": rollback_message,
            "claim_message": claim_message,
            "message": claim_message,
            "rule_state": rule_state,
            "receipt": receipt,
        }

    def snapshot(self) -> dict[str, Any]:
        """读取场景现状；不重新上线规则，因此不会覆盖人工回滚。"""
        preview = self.paths.preview_old_and_new(
            LITERAL_DOT_DOCUMENT,
            doc_key="DOC-A-DOT-B",
            source_text=LITERAL_DOT_SOURCE,
        )
        confirmations = self.paths.list_confirmations(choice_group="CG-AB-LITERAL")
        receipts = self.casts.list_receipts()
        return {
            "ok": True,
            "document": LITERAL_DOT_DOCUMENT,
            "source_text": LITERAL_DOT_SOURCE,
            "preview": preview,
            "rule_state": self.paths.rule_state(),
            "confirmations": confirmations,
            "receipts": receipts,
            "old_confirmation": next(
                (c for c in confirmations if c.get("confirmation_id") == "PC-OLD-AB-7"), None),
            "new_confirmation": next(
                (c for c in confirmations if c.get("confirmation_id") == "PC-NEW-AB-9"), None),
            "old_receipt": self.casts.get_receipt("CR-OLD-AB-7"),
            "new_receipt": self.casts.get_receipt("CR-NEW-AB-9"),
        }

    def provision_literal_dot_scenario(self) -> dict[str, Any]:
        """幂等录入嵌套 a.b=7 与字面键 a.b=9，并把新路径 9 置于已受理未执行。"""
        self.paths.deploy_rule_version(
            ESCAPABLE_DOT, operator="release-bot",
            deploy_time="2026-11-26T09:00:00",
            note="可转义点路径新版上线")

        preview = self.paths.preview_old_and_new(
            LITERAL_DOT_DOCUMENT,
            doc_key="DOC-A-DOT-B",
            source_text=LITERAL_DOT_SOURCE,
        )
        source_texts = {"DOC-A-DOT-B": LITERAL_DOT_SOURCE}

        ok_old, old_msg, old_confirmation = self.paths.confirm_path(
            "$.a.b", LITERAL_DOT_DOCUMENT, "dot-v1",
            confirmation_id="PC-OLD-AB-7",
            flatten_no="NF-OLD-AB",
            batch_id="BATCH-OLD-AB-7",
            target_field="b",
            approved_round=1,
            doc_key="DOC-A-DOT-B",
            confirmed_by="path-reviewer",
            confirm_time="2026-11-26T09:10:00",
            choice_group="CG-AB-LITERAL",
            source_texts=source_texts,
        )
        ok_new, new_msg, new_confirmation = self.paths.confirm_path(
            r"$.a\.b", LITERAL_DOT_DOCUMENT, "escape-v2",
            confirmation_id="PC-NEW-AB-9",
            flatten_no="NF-NEW-AB-ESCAPED",
            batch_id="BATCH-NEW-AB-9",
            target_field="b",
            approved_round=2,
            doc_key="DOC-A-DOT-B",
            confirmed_by="path-reviewer",
            confirm_time="2026-11-26T09:12:00",
            choice_group="CG-AB-LITERAL",
            source_texts=source_texts,
        )

        old_receipt = None
        new_receipt = None
        if ok_old and old_confirmation:
            _ok, _msg, old_receipt = self.casts.accept_approved_cast(
                "BATCH-OLD-AB-7",
                str(old_confirmation["confirmation_id"]),
                receipt_id="CR-OLD-AB-7",
                cast_no="VC-003",
                target_field="b",
                approved_round=1,
                accepted_by="old-job",
                accept_time="2026-11-26T09:20:00",
                target_type="int64",
            )
            if old_receipt and old_receipt.get("status") != "已完成":
                _ok, old_msg, old_receipt = self.casts.execute_receipt(
                    str(old_receipt["receipt_id"]),
                    executor="old-job",
                    execute_time="2026-11-26T09:21:00",
                    cast_no="VC-003",
                    target_type="int64",
                )

        if ok_new and new_confirmation:
            _ok, new_msg, new_receipt = self.casts.accept_approved_cast(
                "BATCH-NEW-AB-9",
                str(new_confirmation["confirmation_id"]),
                receipt_id="CR-NEW-AB-9",
                cast_no="VC-003",
                target_field="b",
                approved_round=2,
                accepted_by="queue-worker",
                accept_time="2026-11-26T09:30:00",
                target_type="int64",
            )

        return {
            "ok": ok_old and ok_new,
            "messages": {"old": old_msg, "new": new_msg},
            "document": LITERAL_DOT_DOCUMENT,
            "source_text": LITERAL_DOT_SOURCE,
            "preview": preview,
            "old_confirmation": old_confirmation,
            "new_confirmation": new_confirmation,
            "old_receipt": old_receipt,
            "new_receipt": new_receipt,
            "rule_state": self.paths.rule_state(),
            "confirmations": self.paths.list_confirmations(choice_group="CG-AB-LITERAL"),
            "receipts": self.casts.list_receipts(),
        }


path_workbench = PathWorkbenchService()
