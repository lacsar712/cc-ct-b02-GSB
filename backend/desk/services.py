from django.conf import settings
from django.db import transaction
from django.utils import timezone

from desk.models import OffsetSubmission, ToolPause, ToolPauseEvent


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


def ensure_tool(tool_code: str) -> ToolPause:
    """提交时确保该刀的闸门行存在（默认不暂停）。"""
    tool, _ = ToolPause.objects.get_or_create(
        tool_code=tool_code,
        defaults={"is_paused": False},
    )
    return tool


def _lock_clause(qs):
    """Postgres 上同时锁候审单与其刀闸行并 SKIP LOCKED；其它库退化为普通行锁。"""
    using = qs.db
    if transaction.get_connection(using=using).vendor == "postgresql":
        # of=('self','tool')：候审单行与其刀闸行一并锁定，使暂停/恢复事务互斥。
        return qs.select_for_update(of=("self", "tool"), skip_locked=True)
    return qs.select_for_update()


def claim_next_pending():
    """认领下一条可处理的候审单。

    跳过规则与暂停台展示、库内 ToolPause.is_paused 完全同源：只领
    ``tool.is_paused=False`` 的候审单。FOR UPDATE OF 同时锁刀闸行，
    暂停/恢复事务一旦持有某刀的行锁，该刀候审单即在 SKIP LOCKED 下
    被跳过——暂停与认领同刀并发时只有一个结局，不会出现“已暂停仍被领走”。
    """
    with transaction.atomic():
        qs = (
            OffsetSubmission.objects.select_related("tool")
            .filter(
                status=OffsetSubmission.Status.PENDING,
                tool__is_paused=False,
            )
            .order_by("created_at", "id")
        )
        submission = _lock_clause(qs).first()
        if submission is None:
            return None

        submission.status = OffsetSubmission.Status.PROCESSING
        submission.save(update_fields=["status"])
        return submission


def set_tool_paused(tool_code: str, paused: bool, actor) -> tuple[ToolPause, bool]:
    """翻转某刀的暂停态并追加痕迹。

    返回 (闸门行, 是否发生了状态变化)。在刀闸行的行锁内完成读改写：
    与认领事务互斥，保证两人同时一笔暂停、一笔认领同刀时只有一种结局。
    """
    tool_code = tool_code.strip()
    action = (
        ToolPauseEvent.Action.PAUSE if paused else ToolPauseEvent.Action.RESUME
    )
    with transaction.atomic():
        # ensure_tool 内的 get_or_create 自带创建竞态重试；行锁只护读改写。
        ensure_tool(tool_code)
        tool = ToolPause.objects.select_for_update().get(tool_code=tool_code)
        if tool.is_paused == paused:
            return tool, False

        tool.is_paused = paused
        tool.changed_by = actor
        tool.changed_at = timezone.now()
        tool.save(update_fields=["is_paused", "changed_by", "changed_at"])
        ToolPauseEvent.objects.create(
            tool=tool,
            action=action,
            actor=actor if (actor and actor.pk) else None,
            actor_name=getattr(actor, "username", "") or "",
        )
        return tool, True
