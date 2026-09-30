"""真实 PostgreSQL 上的并发测试：暂停与认领同一把刀的竞争。

用 transaction=True 让数据真正提交、跨连接可见；每个工作线程使用独立连接，
复现「两人几乎同时一笔点暂停一笔认领同刀」。
"""

import threading

import pytest
from django.db import connection, transaction

from desk.models import OffsetSubmission, ToolPause, ToolPauseLog
from desk.services import claim_next_pending_submission, set_tool_pause


@pytest.fixture
def machinist(db, django_user_model):
    return django_user_model.objects.create_user(
        username="op", password="x", role="machinist"
    )


def _submit(tool, offset=5):
    return OffsetSubmission.objects.create(
        tool_code=tool, offset_um=offset, status=OffsetSubmission.Status.PENDING
    )


def _in_thread(fn):
    box = {}

    def runner():
        try:
            fn()
        except Exception as exc:  # 记录线程内异常，主线程断言
            import traceback

            box["error"] = repr(exc)
            box["tb"] = traceback.format_exc()
        finally:
            connection.close()

    t = threading.Thread(target=runner)
    t.start()
    return t, box


@pytest.mark.django_db(transaction=True)
def test_pause_inflight_locks_knife_claim_skips_to_other(machinist):
    """暂停事务进行中（已锁候审单、暂停态尚未提交）时，认领必须跳过该刀、
    同轮领走其它刀；暂停提交后继续跳过，恢复后才领。"""
    _submit("T01")
    _submit("T02")

    pause_started = threading.Event()
    release_pause = threading.Event()

    def pauser():
        with transaction.atomic():
            # 外层事务保持打开：行锁持有、ToolPause 行已插入但未提交
            set_tool_pause("T01", paused=True, actor=machinist)
            pause_started.set()
            assert release_pause.wait(timeout=10)

    pt, pbox = _in_thread(pauser)
    assert pause_started.wait(timeout=10)

    claim_box = {}

    def claimer():
        first = claim_next_pending_submission()
        claim_box["first"] = first.tool_code if first else None
        second = claim_next_pending_submission()
        claim_box["second"] = second.tool_code if second else None

    try:
        ct, cbox = _in_thread(claimer)
        ct.join(timeout=10)
        assert not ct.is_alive()
        assert "error" not in cbox, cbox
        # 暂停中的 T01 被跳过，同轮照常认领其它刀 T02
        assert claim_box["first"] == "T02"
        # 再领时只剩锁住的 T01 → 跳过，无可领
        assert claim_box["second"] is None
        # T01 仍候审，未被领走
        assert (
            OffsetSubmission.objects.filter(
                tool_code="T01", status=OffsetSubmission.Status.PENDING
            ).count()
            == 1
        )
    finally:
        release_pause.set()
        pt.join(timeout=10)
        assert "error" not in pbox, pbox

    # 暂停已提交：即使候审单解锁，认领仍按库内暂停态跳过 T01
    assert claim_next_pending_submission() is None
    assert (
        OffsetSubmission.objects.filter(
            tool_code="T01", status=OffsetSubmission.Status.PENDING
        ).count()
        == 1
    )

    # 恢复后 T01 立刻可领
    set_tool_pause("T01", paused=False, actor=machinist)
    got = claim_next_pending_submission()
    assert got is not None and got.tool_code == "T01"


@pytest.mark.django_db(transaction=True)
def test_simultaneous_pause_and_claim_has_single_outcome(machinist):
    """两人几乎同时一笔点暂停一笔认领同刀，只许一种结局：

    - 暂停先成：该刀仍候审且暂停，认领返回空（没有其它刀可领）；
    - 认领先成：该刀已复核中，暂停随后落库。
    两种结局都不得「显示已暂停却仍在暂停后被领走」，且无异常、无重复痕迹。
    """
    n = 40
    saw_pause_won = False
    saw_claim_won = False
    for i in range(n):
        code = f"T{i:03d}"
        _submit(code)
        barrier = threading.Barrier(2)
        result = {}

        def claimer():
            barrier.wait()
            sub = claim_next_pending_submission()
            result["claim"] = sub.pk if sub else None

        def pauser():
            barrier.wait()
            set_tool_pause(code, paused=True, actor=machinist)

        ct, cbox = _in_thread(claimer)
        pt, pbox = _in_thread(pauser)
        ct.join(timeout=15)
        pt.join(timeout=15)
        assert not ct.is_alive() and not pt.is_alive(), f"线程卡死 i={i}"
        assert "error" not in cbox, cbox
        assert "error" not in pbox, pbox

        row = OffsetSubmission.objects.get(tool_code=code)
        pause = ToolPause.objects.get(tool_code=code)
        logs = ToolPauseLog.objects.filter(tool_code=code)

        assert pause.is_paused is True  # 暂停最终必然落库且唯一
        assert pause.pk is not None
        assert logs.count() == 1  # 恰好一条痕迹，无重复

        if result["claim"] is None:
            # 暂停先成：刀必须仍是候审态，绝没有被领走
            saw_pause_won = True
            assert row.status == OffsetSubmission.Status.PENDING
        else:
            # 认领先成：被领的就是这把刀，且发生在暂停落库之前
            saw_claim_won = True
            assert result["claim"] == row.pk
            assert row.status == OffsetSubmission.Status.PROCESSING

    # 两种结局都应在足够多次竞争中实际出现，证明测试确实覆盖了双向竞争
    assert saw_pause_won, "未观察到「暂停先成」结局"
    assert saw_claim_won, "未观察到「认领先成」结局"


@pytest.mark.django_db(transaction=True)
def test_concurrent_pauses_on_same_tool_leave_single_state_and_log(machinist):
    """同一把刀并发多次暂停：恰好一次成功落库并写一条痕迹，其余得到
    「状态未变」冲突；库内只留一行暂停态、一条痕迹，无唯一约束错误泄漏。"""
    from desk.services import PauseStateUnchanged

    _submit("T01")
    n = 3
    barrier = threading.Barrier(n)
    outcomes = []
    outcome_lock = threading.Lock()

    def make_pauser():
        def fn():
            barrier.wait()
            try:
                set_tool_pause("T01", paused=True, actor=machinist)
                with outcome_lock:
                    outcomes.append("paused")
            except PauseStateUnchanged:
                with outcome_lock:
                    outcomes.append("unchanged")

        t, box = _in_thread(fn)
        return t, box

    workers = [make_pauser() for _ in range(n)]
    for t, box in workers:
        t.join(timeout=15)
        assert not t.is_alive()
        assert "error" not in box, box  # 不允许出现唯一约束等非预期异常
    assert sorted(outcomes).count("paused") == 1
    assert sorted(outcomes).count("unchanged") == n - 1
    assert ToolPause.objects.filter(tool_code="T01").count() == 1
    assert ToolPauseLog.objects.filter(tool_code="T01").count() == 1
