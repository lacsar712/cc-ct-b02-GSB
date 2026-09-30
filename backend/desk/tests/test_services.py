import pytest

from desk.models import OffsetSubmission, ToolPause, ToolPauseLog
from desk.services import (
    PauseStateUnchanged,
    apply_verdict,
    claim_next_pending_submission,
    set_tool_pause,
)


@pytest.fixture
def machinist(db, django_user_model):
    return django_user_model.objects.create_user(
        username="op", password="x", role="machinist"
    )


def _submit(tool, offset=5):
    return OffsetSubmission.objects.create(
        tool_code=tool, offset_um=offset, status=OffsetSubmission.Status.PENDING
    )


@pytest.mark.django_db
def test_claims_oldest_pending_first(machinist):
    a = _submit("T01")
    b = _submit("T02")
    got = claim_next_pending_submission()
    assert got.pk == a.pk
    assert got.status == OffsetSubmission.Status.PROCESSING
    got2 = claim_next_pending_submission()
    assert got2.pk == b.pk


@pytest.mark.django_db
def test_paused_knife_skipped_others_claimed_then_resume_claims_it(machinist):
    # 甲刀候审单时点暂停
    a = _submit("T01")
    pause = set_tool_pause("T01", paused=True, actor=machinist)
    assert pause.is_paused is True

    # 乙刀新交，应先被领（认领进程跳过甲刀、同轮照常领其它刀）
    b = _submit("T02")
    got = claim_next_pending_submission()
    assert got.pk == b.pk

    # 只剩暂停的甲刀：没有可领单据
    assert claim_next_pending_submission() is None
    a.refresh_from_db()
    assert a.status == OffsetSubmission.Status.PENDING

    # 恢复甲刀后，甲刀才被领
    set_tool_pause("T01", paused=False, actor=machinist)
    got = claim_next_pending_submission()
    assert got.pk == a.pk
    assert got.status == OffsetSubmission.Status.PROCESSING


@pytest.mark.django_db
def test_multiple_paused_tools_all_skipped(machinist):
    a = _submit("T01")
    b = _submit("T02")
    c = _submit("T03")
    set_tool_pause("T01", paused=True, actor=machinist)
    set_tool_pause("T02", paused=True, actor=machinist)
    # T01/T02 都暂停，最早可领的是 T03
    got = claim_next_pending_submission()
    assert got.pk == c.pk
    assert claim_next_pending_submission() is None
    set_tool_pause("T01", paused=False, actor=machinist)
    assert claim_next_pending_submission().pk == a.pk
    set_tool_pause("T02", paused=False, actor=machinist)
    assert claim_next_pending_submission().pk == b.pk


@pytest.mark.django_db
def test_pause_and_resume_write_audit_log(machinist):
    set_tool_pause("T07", paused=True, actor=machinist)
    set_tool_pause("T07", paused=False, actor=machinist)
    actions = list(
        ToolPauseLog.objects.filter(tool_code="T07").order_by("id").values_list(
            "action", "actor_name"
        )
    )
    assert actions == [
        (ToolPauseLog.Action.PAUSE, "op"),
        (ToolPauseLog.Action.RESUME, "op"),
    ]


@pytest.mark.django_db
def test_repeated_pause_is_unchanged_and_logs_nothing(machinist):
    set_tool_pause("T01", paused=True, actor=machinist)
    with pytest.raises(PauseStateUnchanged):
        set_tool_pause("T01", paused=True, actor=machinist)
    assert ToolPauseLog.objects.filter(tool_code="T01").count() == 1
    # 恢复一个本就正常的刀也视为未变更
    with pytest.raises(PauseStateUnchanged):
        set_tool_pause("T99", paused=False, actor=machinist)
    assert not ToolPauseLog.objects.filter(tool_code="T99").exists()


@pytest.mark.django_db
def test_gate_and_skip_share_single_source(machinist):
    """闸页 is_paused、认领跳过、库内 ToolPause 同源同表。"""
    _submit("T01")
    set_tool_pause("T01", paused=True, actor=machinist)
    # 库里只有一处真相
    assert ToolPause.objects.get(tool_code="T01").is_paused is True
    # 认领以同一张表为准跳过
    assert claim_next_pending_submission() is None
    # 直接改库（同源）后认领立刻可见，不存在第二份分叉状态
    ToolPause.objects.filter(tool_code="T01").update(is_paused=False)
    assert claim_next_pending_submission() is not None


@pytest.mark.django_db
def test_worker_uses_shared_claim_skip(machinist):
    from desk import worker

    _submit("T01")
    set_tool_pause("T01", paused=True, actor=machinist)
    assert worker.claim_one_pending() is False
    set_tool_pause("T01", paused=False, actor=machinist)
    assert worker.claim_one_pending() is True
    row = OffsetSubmission.objects.get(tool_code="T01")
    assert row.status == OffsetSubmission.Status.DONE


@pytest.mark.django_db
def test_apply_verdict_thresholds():
    good = OffsetSubmission.objects.create(
        tool_code="T1", offset_um=12, status=OffsetSubmission.Status.PROCESSING
    )
    apply_verdict(good)
    assert good.verdict == OffsetSubmission.Verdict.PASS
    bad = OffsetSubmission.objects.create(
        tool_code="T2", offset_um=13, status=OffsetSubmission.Status.PROCESSING
    )
    apply_verdict(bad)
    assert bad.verdict == OffsetSubmission.Verdict.FAIL
