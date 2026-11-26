"""路径迁移、确认位置、规则回滚与转型回执的 HTTP 接口。

通用资源接口仍负责模块表格；这些联动接口保留完整表达式原文、位置、
实际行、版本和回执凭据，供路径确认页与转型页跨页核对。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.services.nested_flatten import NestedFlattenService
from app.services.path_workbench import path_workbench
from app.services.value_cast import ValueCastService

router = APIRouter(prefix="/api", tags=["路径迁移与转型回执"])
paths = NestedFlattenService()
casts = ValueCastService()


def _documents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "documents" in payload:
        docs = payload["documents"]
        return docs if isinstance(docs, list) else [docs]
    if "document" in payload:
        doc = payload["document"]
        return doc if isinstance(doc, list) else [doc]
    # 场景接口允许不传文档，使用内置 a.b=7 / a\.b=9 对照文档。
    return [{"a": {"b": 7}, "a.b": 9}]


def _result(ok: bool, message: str, entry: Any = None, **extra: Any) -> dict[str, Any]:
    result = {"ok": ok, "message": message, "entry": entry}
    if isinstance(entry, dict):
        result.setdefault("receipt", entry if "receipt_id" in entry else None)
        result.setdefault("confirmation", entry if "confirmation_id" in entry else None)
    result.update(extra)
    return result


@router.post("/path-workbench/scenario")
def provision_scenario() -> dict[str, Any]:
    """幂等准备题述完整场景，便于页面和接口验收。"""
    return {"ok": True, "message": "对照场景已就绪", **path_workbench.provision_literal_dot_scenario()}


@router.post("/path-workbench/rollback-and-claim")
def rollback_and_claim(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    result = path_workbench.rollback_and_claim(
        receipt_id=str(payload.get("receipt_id") or "CR-NEW-AB-9"),
        claimant=str(payload.get("claimant") or "next-shift"),
        rollback_time=str(payload.get("rollback_time") or ""),
        claim_time=str(payload.get("claim_time") or ""),
    )
    return _result(bool(result["ok"]), result["message"], result.get("receipt"), **result)


@router.get("/path-workbench/scenario")
def get_scenario() -> dict[str, Any]:
    snapshot = path_workbench.snapshot()
    if not snapshot["confirmations"]:
        return path_workbench.provision_literal_dot_scenario()
    return snapshot


@router.post("/path-preview")
def path_preview(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    expression = str(payload.get("expression") or payload.get("路径") or "$.a.b")
    version = payload.get("path_version") or payload.get("version") or "v1"
    result = paths.preview_path(
        expression, _documents(payload), version,
        doc_key=str(payload.get("doc_key") or ""),
        source_texts=payload.get("source_text") or payload.get("source_texts"),
    )
    return {"ok": result["ok"], "message": "预览完成" if result["ok"] else "未匹配到位置", **result}


@router.post("/path-preview/choice")
def path_preview_choice(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    document = payload.get("document") or {"a": {"b": 7}, "a.b": 9}
    result = paths.preview_old_and_new(
        document,
        old_expression=str(payload.get("old_expression") or "$.a.b"),
        new_expression=str(payload.get("new_expression") or r"$.a\.b"),
        doc_key=str(payload.get("doc_key") or "DOC-LITERAL-DOT"),
        source_text=payload.get("source_text"),
    )
    return {"ok": True, "message": "新旧路径并列预览完成", **result}


@router.post("/path-migrations")
def migrate_path(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, entry = paths.migrate_dot_path(
        new_expression=str(payload.get("new_expression") or r"$.a\.b"),
        old_expression=str(payload.get("old_expression") or "$.a.b"),
        flatten_no=str(payload.get("flatten_no") or ""),
        old_flatten_no=str(payload.get("old_flatten_no") or ""),
        template_id=str(payload.get("template_id") or "ET-PATH-CONTRACT"),
        target_field=str(payload.get("target_field") or "b"),
        operator=str(payload.get("operator") or ""),
        migration_time=str(payload.get("migration_time") or ""),
    )
    return _result(ok, message, entry)


@router.post("/path-confirmations")
def confirm_path(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, record = paths.confirm_path(
        str(payload.get("expression") or payload.get("路径表达式原文") or ""),
        _documents(payload),
        payload.get("path_version") or payload.get("version") or "v1",
        confirmation_id=str(payload.get("confirmation_id") or ""),
        flatten_no=str(payload.get("flatten_no") or ""),
        batch_id=str(payload.get("batch_id") or ""),
        target_field=str(payload.get("target_field") or ""),
        approved_round=int(payload.get("approved_round") or payload.get("round") or 1),
        selected_index=int(payload.get("selected_index") or 0),
        selected_position=str(payload.get("selected_position") or ""),
        doc_key=str(payload.get("doc_key") or ""),
        confirmed_by=str(payload.get("confirmed_by") or ""),
        confirm_time=str(payload.get("confirm_time") or ""),
        choice_group=str(payload.get("choice_group") or ""),
        template_id=str(payload.get("template_id") or "ET-PATH-CONTRACT"),
        source_texts=payload.get("source_text") or payload.get("source_texts"),
    )
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return _result(ok, message, record, confirmation=record)


@router.get("/path-confirmations")
def list_confirmations(batch_id: str = "", choice_group: str = "") -> dict[str, Any]:
    items = paths.list_confirmations(batch_id, choice_group)
    return {"items": items, "total": len(items)}


@router.get("/path-target-rows")
def list_target_rows(batch_id: str = "", confirmation_id: str = "") -> dict[str, Any]:
    items = paths.list_target_rows(batch_id, confirmation_id)
    return {"items": items, "total": len(items)}


@router.get("/path-rule")
def rule_state() -> dict[str, Any]:
    return {"ok": True, "rule_state": paths.rule_state()}


@router.post("/path-rule/deploy")
def deploy_rule(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, state = paths.deploy_rule_version(
        payload.get("version") or "v2",
        operator=str(payload.get("operator") or "release-bot"),
        deploy_time=str(payload.get("deploy_time") or ""),
        note=str(payload.get("note") or ""),
    )
    return _result(ok, message, state, rule_state=state)


@router.post("/path-rule/rollback")
def rollback_rule(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, state = paths.rollback_editor_rule(
        payload.get("target_version") or payload.get("version") or "v1",
        operator=str(payload.get("operator") or "maintainer"),
        rollback_time=str(payload.get("rollback_time") or ""),
        note=str(payload.get("note") or "编辑器规则回滚"),
    )
    return _result(ok, message, state, rule_state=state)


@router.get("/source-locations/{reference}")
def source_location(reference: str) -> dict[str, Any]:
    location = paths.get_source_location(reference)
    if location is None:
        raise HTTPException(status_code=404, detail="实际命中位置不存在")
    return {"ok": True, "location": location, "entry": location}


@router.post("/cast-receipts")
def create_cast_receipt(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, receipt = casts.accept_approved_cast(
        str(payload.get("batch_id") or ""),
        str(payload.get("confirmation_id") or ""),
        target_row_id=str(payload.get("target_row_id") or ""),
        cast_no=str(payload.get("cast_no") or ""),
        target_field=str(payload.get("target_field") or ""),
        approved_round=payload.get("approved_round"),
        receipt_id=str(payload.get("receipt_id") or ""),
        accepted_by=str(payload.get("accepted_by") or "api"),
        accept_time=str(payload.get("accept_time") or ""),
        idempotency_key=str(payload.get("idempotency_key") or ""),
        target_type=str(payload.get("target_type") or ""),
    )
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return _result(ok, message, receipt, receipt=receipt)


@router.get("/cast-receipts")
def get_cast_receipts(batch_id: str = "", status: str = "") -> dict[str, Any]:
    items = casts.list_receipts(batch_id, status)
    return {"items": items, "total": len(items)}


@router.get("/cast-receipts/{receipt_id}")
def get_cast_receipt(receipt_id: str) -> dict[str, Any]:
    receipt = casts.get_receipt(receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="转型回执不存在")
    return {"ok": True, "receipt": receipt, "entry": receipt}


@router.post("/cast-receipts/{receipt_id}/claim")
def claim_cast_receipt(receipt_id: str,
                       payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, receipt = casts.claim_receipt(
        receipt_id,
        claimant=str(payload.get("claimant") or "next-shift"),
        claim_time=str(payload.get("claim_time") or ""),
        credential=str(payload.get("credential") or ""),
    )
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return _result(ok, message, receipt, receipt=receipt)


@router.post("/cast-receipts/{receipt_id}/select")
def select_cast_path(receipt_id: str,
                     payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    ok, message, receipt = casts.select_path(
        receipt_id,
        str(payload.get("selection") or payload.get("path") or "pinned"),
        claimant=str(payload.get("claimant") or "next-shift"),
        select_time=str(payload.get("select_time") or payload.get("execute_time") or ""),
        execute=bool(payload.get("execute", True)),
        cast_no=str(payload.get("cast_no") or ""),
        target_type=str(payload.get("target_type") or ""),
    )
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return _result(ok, message, receipt, receipt=receipt)


@router.post("/cast-receipts/{receipt_id}/execute")
def execute_cast_receipt(receipt_id: str,
                         payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    selection = payload.get("selection")
    if selection is not None:
        ok, message, receipt = casts.continue_from_receipt(
            receipt_id,
            claimant=str(payload.get("executor") or payload.get("claimant") or "next-shift"),
            execute_time=str(payload.get("execute_time") or ""),
            selection=str(selection),
            cast_no=str(payload.get("cast_no") or ""),
            target_type=str(payload.get("target_type") or ""),
        )
    else:
        ok, message, receipt = casts.execute_receipt(
            receipt_id,
            executor=str(payload.get("executor") or "next-shift"),
            execute_time=str(payload.get("execute_time") or ""),
            cast_no=str(payload.get("cast_no") or ""),
            target_type=str(payload.get("target_type") or ""),
        )
    if not ok:
        # 转型失败已经写入隔离台账，因此返回 200 + ok=false 更便于页面展示恢复入口。
        return _result(False, message, receipt, receipt=receipt)
    return _result(ok, message, receipt, receipt=receipt)


@router.get("/isolation-ledger")
def isolation_ledger(batch_id: str = "") -> dict[str, Any]:
    items = casts.isolation_ledger(batch_id)
    return {"items": items, "total": len(items)}


@router.post("/isolation-records/{isolation_id}/recover")
def recover_isolation(isolation_id: str) -> dict[str, Any]:
    ok, message, snapshot = casts.recover_failed_input(isolation_id)
    if not ok:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True, "message": message, "input_snapshot": snapshot, "entry": snapshot}


# 模块名风格别名：部分调用方按 nested_flatten / value_cast 资源命名查找联动动作。
_ALIASES = (
    ("/nested_flatten/preview", path_preview, ("POST",)),
    ("/nested_flatten/preview-choice", path_preview_choice, ("POST",)),
    ("/nested_flatten/migrate", migrate_path, ("POST",)),
    ("/nested_flatten/confirm", confirm_path, ("POST",)),
    ("/nested_flatten/confirmation-list", list_confirmations, ("GET",)),
    ("/nested_flatten/target-rows", list_target_rows, ("GET",)),
    ("/nested_flatten/rule", rule_state, ("GET",)),
    ("/nested_flatten/rollback", rollback_rule, ("POST",)),
    ("/value_cast/receipts", get_cast_receipts, ("GET",)),
    ("/value_cast/receipts/create", create_cast_receipt, ("POST",)),
    ("/value_cast/receipts/{receipt_id}", get_cast_receipt, ("GET",)),
    ("/value_cast/receipts/{receipt_id}/claim", claim_cast_receipt, ("POST",)),
    ("/value_cast/receipts/{receipt_id}/select", select_cast_path, ("POST",)),
    ("/value_cast/receipts/{receipt_id}/execute", execute_cast_receipt, ("POST",)),
    ("/value_cast/isolation-ledger", isolation_ledger, ("GET",)),
)
for alias_path, endpoint, methods in _ALIASES:
    router.add_api_route(alias_path, endpoint, methods=list(methods))
