"""可转义点路径、版本冲突回执和隔离恢复的验收测试。"""
from __future__ import annotations

import pytest

from app.pipeline.path_contract import ESCAPABLE_DOT, LEGACY_DOT
from app.pipeline.transform_stage import FlattenPath, TransformStage
from app.services.nested_flatten import NestedFlattenService
from app.services.path_workbench import path_workbench
from app.services.value_cast import ValueCastService
from app.store import store


DOCUMENT = {"a": {"b": 7}, "a.b": 9}
SOURCE = '{\n  "a": {"b": 7},\n  "a.b": 9\n}\n'


@pytest.fixture(autouse=True)
def reset_store():
    store.reload_seed()
    yield
    store.reload_seed()


def test_legacy_and_escaped_paths_resolve_distinct_values_with_positions():
    svc = NestedFlattenService()
    preview = svc.preview_old_and_new(DOCUMENT, source_text=SOURCE)
    assert preview["old"]["value"] == 7
    assert preview["new"]["value"] == 9
    old_match = preview["old"]["matches"][0]
    new_match = preview["new"]["matches"][0]
    assert old_match["path_version"] == LEGACY_DOT
    assert new_match["path_version"] == ESCAPABLE_DOT
    assert old_match["position"] == "$.a.b"
    assert new_match["position"] == '$["a.b"]'
    assert old_match["line_number"] == 2
    assert new_match["line_number"] == 3
    assert '"b": 7' in old_match["actual_line"]
    assert '"a.b": 9' in new_match["actual_line"]


def test_historical_backslash_is_not_escape_syntax_in_v1():
    svc = NestedFlattenService()
    ok, _message, record = svc.confirm_path(
        r"$.a\.b", DOCUMENT, "v1", batch_id="B-V1-BACKSLASH")
    assert ok is False
    assert record is None


def test_receipt_pins_nine_through_rollback_and_keeps_old_seven_queryable():
    scenario = path_workbench.provision_literal_dot_scenario()
    rid = scenario["new_receipt"]["receipt_id"]
    assert scenario["new_receipt"]["status"] == "已受理"
    assert scenario["new_receipt"]["transformed_value"] is None
    assert scenario["old_receipt"]["transformed_value"] == 7

    path_workbench.paths.rollback_editor_rule(rollback_time="t-rollback")
    ok, message, receipt = path_workbench.casts.claim_receipt(rid, "next-shift", "t-claim")
    assert ok is True
    assert receipt["selection_required"] is True
    assert receipt["version_conflict"] is True
    assert {choice["key"] for choice in receipt["choices"]} == {
        "pinned_receipt", "approved_legacy"}
    assert "selection_endpoint" in receipt

    ok, message, done = path_workbench.casts.continue_from_receipt(
        rid, "next-shift", "t-execute", selection="pinned")
    assert ok is True, message
    assert done["raw_value"] == 9
    assert done["transformed_value"] == 9
    assert done["path_version"] == ESCAPABLE_DOT
    source = done["field_sources"]["b"]
    assert source["expression"] == r"$.a\.b"
    assert source["position"] == '$["a.b"]'
    assert source["actual_line"] == '"a.b": 9'
    assert done["credential"]
    assert done["result_credential"]

    old = path_workbench.casts.get_receipt("CR-OLD-AB-7")
    assert old["transformed_value"] == 7


def test_legacy_reselection_adopts_seven_without_erasing_nine_credential():
    scenario = path_workbench.provision_literal_dot_scenario()
    rid = scenario["new_receipt"]["receipt_id"]
    path_workbench.paths.rollback_editor_rule()
    path_workbench.casts.claim_receipt(rid)
    ok, _msg, done = path_workbench.casts.select_path(rid, "legacy")
    assert ok is True
    assert done["raw_value"] == 7
    assert done["transformed_value"] == 7
    assert done["path_version"] == LEGACY_DOT
    snapshot = done["original_acceptance_snapshot"]
    assert snapshot["raw_value"] == 9
    assert snapshot["credential"] == done["credential"]


def test_migration_and_confirmed_target_rows_are_idempotent():
    svc = NestedFlattenService()
    first = svc.migrate_dot_path()
    second = svc.migrate_dot_path()
    assert first[0] is True
    assert second[2]["already_adopted"] is True
    count_after_migrations = len(store.rows("nested_flatten"))

    ok, _msg, confirmation = svc.confirm_path(
        r"$.a\.b", DOCUMENT, "v2", batch_id="B-IDEM",
        target_field="b", approved_round=3, confirmation_id="PC-IDEM")
    assert ok is True
    target_count = len(svc.list_target_rows(confirmation_id="PC-IDEM"))
    ok2, _msg2, replay = svc.confirm_path(
        r"$.a\.b", DOCUMENT, "v2", batch_id="B-IDEM",
        target_field="b", approved_round=3, confirmation_id="PC-IDEM")
    assert ok2 is True
    assert replay["idempotent_replay"] is True
    assert len(svc.list_target_rows(confirmation_id="PC-IDEM")) == target_count
    assert len(store.rows("nested_flatten")) == count_after_migrations


def test_different_path_versions_cannot_be_mixed_in_one_batch():
    paths = NestedFlattenService()
    casts = ValueCastService()
    _ok, _m, old = paths.confirm_path("$.a.b", DOCUMENT, "v1", batch_id="MIX")
    _ok, _m, new = paths.confirm_path(r"$.a\.b", DOCUMENT, "v2", batch_id="MIX")
    ok, _m, _r = casts.accept_approved_cast("MIX", old["confirmation_id"])
    assert ok is True
    ok, message, receipt = casts.accept_approved_cast("MIX", new["confirmation_id"])
    assert ok is False
    assert "混接" in message


def test_cast_failure_is_quarantined_with_doc_key_and_recoverable_input():
    paths = NestedFlattenService()
    casts = ValueCastService()
    _ok, _m, confirmation = paths.confirm_path(
        "$.broken", {"broken": "not-int"}, "v1", batch_id="B-FAIL")
    ok, _m, receipt = casts.accept_approved_cast(
        "B-FAIL", confirmation["confirmation_id"], cast_no="VC-003",
        target_field="broken", target_type="int64")
    assert ok is True
    ok, message, failed = casts.execute_receipt(receipt["receipt_id"], target_type="int64")
    assert ok is False
    isolation_id = failed["isolation_id"]
    row = next(r for r in store.rows("row_quarantine") if r["隔离编号"] == isolation_id)
    assert row["文档键"]
    assert "文档键" in row["失败原因"]
    ok, message, snapshot = casts.recover_failed_input(isolation_id)
    assert ok is True
    assert snapshot["raw_value"] == "not-int"
    assert snapshot["expression"] == "$.broken"


def test_transform_stage_supports_versioned_literal_path():
    stage = TransformStage()
    stage.register_flatten_path(FlattenPath(
        "NF-LIT", "ET", r"$.a\.b", ["v"], "EXPAND", 3, "SKIP", ESCAPABLE_DOT))
    ok, rows, _msg = stage.flatten_document("NF-LIT", [DOCUMENT])
    assert ok is True
    assert rows == [{"v": 9}]

    stage.register_flatten_path(FlattenPath(
        "NF-OLD", "ET", "$.a.b", ["v"], "EXPAND", 3, "SKIP", LEGACY_DOT))
    ok, rows, _msg = stage.flatten_document("NF-OLD", [DOCUMENT])
    assert ok is True
    assert rows == [{"v": 7}]
