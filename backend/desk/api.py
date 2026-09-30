from datetime import datetime
from typing import Optional

from django.db.models import Count
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError

from desk.auth_utils import bearer_auth, create_access_token, verify_password
from desk.models import OffsetSubmission, ToolPause, ToolPauseLog, User
from desk.services import PauseStateUnchanged, set_tool_pause

api = NinjaAPI(title="数控刀补复核台", version="1.1")


class HealthOut(Schema):
    status: str


class LoginIn(Schema):
    username: str
    password: str


class LoginOut(Schema):
    token: str
    username: str
    role: str
    can_write: bool


class SubmissionIn(Schema):
    tool_code: str
    offset_um: int


class SubmissionOut(Schema):
    id: int
    tool_code: str
    offset_um: int
    status: str
    verdict: str
    tool_paused: bool
    created_at: datetime
    reviewed_at: Optional[datetime]


def _paused_tool_codes() -> set[str]:
    """库内暂停态：认领跳过、闸页展示、单据标记共用这一份来源。"""
    return set(
        ToolPause.objects.filter(is_paused=True).values_list("tool_code", flat=True)
    )


def _to_out(row: OffsetSubmission, paused_codes: set[str] | None = None) -> SubmissionOut:
    if paused_codes is None:
        paused_codes = _paused_tool_codes()
    return SubmissionOut(
        id=row.id,
        tool_code=row.tool_code,
        offset_um=row.offset_um,
        status=row.status,
        verdict=row.verdict or "",
        tool_paused=row.tool_code in paused_codes,
        created_at=row.created_at,
        reviewed_at=row.reviewed_at,
    )


class ToolPauseIn(Schema):
    tool_code: str


class ToolPauseOut(Schema):
    tool_code: str
    is_paused: bool
    pending_count: int
    updated_by_name: str
    updated_at: Optional[datetime]


class ToolPauseLogOut(Schema):
    id: int
    tool_code: str
    action: str
    actor_name: str
    created_at: datetime


@api.get("/health", response=HealthOut)
def health(request: HttpRequest):
    return {"status": "ok"}


@api.post("/auth/login", response=LoginOut)
def login(request: HttpRequest, body: LoginIn):
    try:
        user = User.objects.get(username=body.username)
    except User.DoesNotExist:
        raise HttpError(401, "用户名或密码错误")
    if not verify_password(body.password, user.password):
        raise HttpError(401, "用户名或密码错误")
    token = create_access_token(user)
    return {
        "token": token,
        "username": user.username,
        "role": user.role,
        "can_write": user.can_write,
    }


@api.get("/submissions", response=list[SubmissionOut], auth=bearer_auth)
def list_submissions(request: HttpRequest):
    rows = list(OffsetSubmission.objects.all()[:200])
    paused_codes = _paused_tool_codes()
    return [_to_out(r, paused_codes) for r in rows]


@api.get("/submissions/{submission_id}", response=SubmissionOut, auth=bearer_auth)
def get_submission(request: HttpRequest, submission_id: int):
    try:
        row = OffsetSubmission.objects.get(pk=submission_id)
    except OffsetSubmission.DoesNotExist:
        raise HttpError(404, "刀补记录不存在")
    return _to_out(row)


@api.post("/submissions", response=SubmissionOut, auth=bearer_auth)
def create_submission(request: HttpRequest, body: SubmissionIn):
    user: User = request.auth
    if not user.can_write:
        raise HttpError(403, "当前账号只读，不能提交刀补")
    tool_code = body.tool_code.strip()
    if not tool_code:
        raise HttpError(400, "刀具编号不能为空")
    row = OffsetSubmission.objects.create(
        tool_code=tool_code,
        offset_um=body.offset_um,
        submitted_by=user,
        status=OffsetSubmission.Status.PENDING,
    )
    return _to_out(row)


@api.get("/tool-pauses", response=list[ToolPauseOut], auth=bearer_auth)
def list_tool_pauses(request: HttpRequest):
    """暂停台：列出库内有暂停态行或当前有待复核单的刀具。

    is_paused 直接读 ToolPause 表（与 worker 跳过同一张表、同一个真相）。
    """
    pause_rows = {
        p.tool_code: p for p in ToolPause.objects.select_related("updated_by")
    }
    pending_counts = dict(
        OffsetSubmission.objects.filter(status=OffsetSubmission.Status.PENDING)
        .values("tool_code")
        .annotate(n=Count("id"))
        .values_list("tool_code", "n")
    )
    codes = sorted(set(pause_rows) | set(pending_counts))
    result = []
    for code in codes:
        pause = pause_rows.get(code)
        result.append(
            ToolPauseOut(
                tool_code=code,
                is_paused=bool(pause and pause.is_paused),
                pending_count=pending_counts.get(code, 0),
                updated_by_name=(
                    pause.updated_by.username
                    if pause and pause.updated_by_id
                    else ""
                ),
                updated_at=pause.updated_at if pause else None,
            )
        )
    return result


def _change_pause(request: HttpRequest, body: ToolPauseIn, *, paused: bool) -> ToolPauseOut:
    user: User = request.auth
    if not user.can_write:
        # 复核员对闸页与痕迹簿只读。
        raise HttpError(403, "当前账号只读，不能暂停或恢复刀具")
    tool_code = body.tool_code.strip()
    if not tool_code:
        raise HttpError(400, "刀具编号不能为空")
    try:
        pause = set_tool_pause(tool_code, paused=paused, actor=user)
    except PauseStateUnchanged as exc:
        raise HttpError(409, str(exc))
    pending_count = OffsetSubmission.objects.filter(
        status=OffsetSubmission.Status.PENDING, tool_code=tool_code
    ).count()
    return ToolPauseOut(
        tool_code=pause.tool_code,
        is_paused=pause.is_paused,
        pending_count=pending_count,
        updated_by_name=user.username,
        updated_at=pause.updated_at,
    )


@api.post("/tool-pauses/pause", response=ToolPauseOut, auth=bearer_auth)
def pause_tool(request: HttpRequest, body: ToolPauseIn):
    return _change_pause(request, body, paused=True)


@api.post("/tool-pauses/resume", response=ToolPauseOut, auth=bearer_auth)
def resume_tool(request: HttpRequest, body: ToolPauseIn):
    return _change_pause(request, body, paused=False)


@api.get("/tool-pause-logs", response=list[ToolPauseLogOut], auth=bearer_auth)
def list_tool_pause_logs(request: HttpRequest):
    rows = ToolPauseLog.objects.select_related("actor")[:200]
    return [
        ToolPauseLogOut(
            id=r.id,
            tool_code=r.tool_code,
            action=r.action,
            actor_name=r.actor_name
            or (r.actor.username if r.actor_id else ""),
            created_at=r.created_at,
        )
        for r in rows
    ]
