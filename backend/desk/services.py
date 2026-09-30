from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from desk.models import OffsetSubmission, ToolPause, ToolPauseLog


class PauseStateUnchanged(Exception):
    """暂停 / 恢复时目标状态与库内暂停态一致，无需落库。"""


def evaluate_verdict(offset_um: int) -> str:
    if abs(offset_um) <= settings.OFFSET_TOLERANCE_UM:
        return OffsetSubmission.Verdict.PASS
    return OffsetSubmission.Verdict.FAIL


def apply_verdict(submission: OffsetSubmission) -> None:
    submission.verdict = evaluate_verdict(submission.offset_um)
    submission.status = OffsetSubmission.Status.DONE
    submission.reviewed_at = timezone.now()
    submission.save(
        update_fields=["verdict", "status", "reviewed_at"],
    )


@transaction.atomic
def claim_next_pending_submission() -> OffsetSubmission | None:
    """认领最早一条「刀具未暂停」的待复核单。

    暂停刀被同一条 SQL 直接排除：暂停台跳过逻辑与库内 ToolPause 暂停态同源，
    不存在「只改闸页 / 只改跳过」的分叉。行上的 ``SKIP LOCKED`` 与暂停操作
    互斥——同刀暂停与认领并发时，谁先拿到该刀候审单的行锁谁说了算，只许一种
    结局；被暂停事务锁住的刀会被跳过，同一轮继续认领其它刀。

    返回已置为「复核中」的单据；没有可领单据时返回 None。
    """
    paused_tools = ToolPause.objects.filter(is_paused=True).values("tool_code")
    submission = (
        OffsetSubmission.objects.select_for_update(skip_locked=True)
        .filter(status=OffsetSubmission.Status.PENDING)
        .exclude(tool_code__in=paused_tools)
        .order_by("created_at", "id")
        .first()
    )
    if submission is None:
        return None

    submission.status = OffsetSubmission.Status.PROCESSING
    submission.save(update_fields=["status"])
    return submission


@transaction.atomic
def set_tool_pause(tool_code: str, *, paused: bool, actor) -> ToolPause:
    """暂停或恢复一把刀，返回更新后的库内暂停态。

    先锁该刀全部候审单（与 worker 认领互斥），再锁 / 建 ToolPause 状态行，
    最后写痕迹簿——三步同一事务，原子提交：不会出现「显示已暂停却仍被领走」，
    也不会出现恢复半途中跳过逻辑仍按旧态执行。
    """
    tool_code = tool_code.strip()
    if not tool_code:
        raise ValueError("刀具编号不能为空")

    # 1. 锁住该刀所有候审单：worker 的 SKIP LOCKED 在此期间只会跳过它们。
    list(
        OffsetSubmission.objects.select_for_update()
        .filter(status=OffsetSubmission.Status.PENDING, tool_code=tool_code)
        .order_by("created_at", "id")
    )

    # 2. 锁 / 建该刀的暂停态行（并发首个暂停可能撞唯一约束，撞了重取即可）。
    created = False
    try:
        pause = ToolPause.objects.select_for_update().get(tool_code=tool_code)
    except ToolPause.DoesNotExist:
        if not paused:
            # 恢复一把库内从无暂停记录的刀：本就正常，不算状态变更。
            raise PauseStateUnchanged(f"刀具 {tool_code} 当前未暂停")
        try:
            # 内层 savepoint：撞唯一约束时只回滚到此处，外层事务仍可用。
            with transaction.atomic():
                pause = ToolPause.objects.create(
                    tool_code=tool_code,
                    is_paused=True,
                    updated_by=actor if getattr(actor, "pk", None) else None,
                )
            created = True
        except IntegrityError:
            pause = ToolPause.objects.select_for_update().get(tool_code=tool_code)

    if not created:
        if pause.is_paused == paused:
            # 状态未变：不落库、不记痕迹。
            label = "暂停中" if paused else "已恢复"
            raise PauseStateUnchanged(f"刀具 {tool_code} 已处于「{label}」状态")
        pause.is_paused = paused
        pause.updated_by = actor if getattr(actor, "pk", None) else None
        pause.save(update_fields=["is_paused", "updated_by", "updated_at"])

    # 3. 动作记痕迹簿。
    ToolPauseLog.objects.create(
        tool_code=tool_code,
        action=(
            ToolPauseLog.Action.PAUSE if paused else ToolPauseLog.Action.RESUME
        ),
        actor=pause.updated_by,
        actor_name=getattr(actor, "username", "") or "",
    )
    return pause
