from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        MACHINIST = "machinist", "操作员"
        AUDITOR = "auditor", "复核员"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.MACHINIST,
    )

    @property
    def can_write(self) -> bool:
        return self.role == self.Role.MACHINIST


class ToolPause(models.Model):
    """每把刀一行的暂停闸门：闸页展示、认领跳过、动作记录三者同源。"""

    tool_code = models.CharField(max_length=32, primary_key=True)
    is_paused = models.BooleanField(default=False, db_index=True)
    changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tool_pause_changes",
    )
    changed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        state = "暂停" if self.is_paused else "正常"
        return f"{self.tool_code}（{state}）"


class OffsetSubmission(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待复核"
        PROCESSING = "processing", "复核中"
        DONE = "done", "已完成"

    class Verdict(models.TextChoices):
        PASS = "合格", "合格"
        FAIL = "超差", "超差"

    tool = models.ForeignKey(
        ToolPause,
        on_delete=models.PROTECT,
        related_name="submissions",
    )
    offset_um = models.IntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    verdict = models.CharField(
        max_length=8,
        choices=Verdict.choices,
        blank=True,
        default="",
    )
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.tool_id} {self.offset_um}µm"

    @property
    def tool_code(self) -> str:
        """外键 tool_id 直接就是刀具编号，兼容旧调用点。"""
        return self.tool_id


class ToolPauseEvent(models.Model):
    """痕迹簿：暂停/恢复动作只追加、不可改删。"""

    class Action(models.TextChoices):
        PAUSE = "pause", "暂停"
        RESUME = "resume", "恢复"

    tool = models.ForeignKey(
        ToolPause,
        on_delete=models.PROTECT,
        related_name="events",
    )
    action = models.CharField(max_length=8, choices=Action.choices)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tool_pause_events",
    )
    actor_name = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.tool_id} {self.get_action_display()} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
