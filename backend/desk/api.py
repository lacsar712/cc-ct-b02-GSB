from datetime import datetime
from typing import Optional

from django.db.models import Count, Q
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError

from desk.auth_utils import bearer_auth, create_access_token, verify_password
from desk.models import OffsetSubmission, ToolPause, ToolPauseEvent, User
from desk.services import ensure_tool, set_tool_paused

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
    is_paused: bool
    created_at: datetime
    reviewed_at: Optional[datetime]


def _to_out(row: OffsetSubmission) -> SubmissionOut:
    return SubmissionOut(
        id=row.id,
        tool_code=row.tool_id,
        offset_um=row.offset_um,
        status=row.status,
        verdict=row.verdict or "",
        # 与认领跳过逻辑、暂停台同读 ToolPause.is_paused，绝无第二份状态。
        is_paused=row.tool.is_paused,
        created_at=row.created_at,
        reviewed_at=row.reviewed_at,
    )


class ToolOut(Schema):
    tool_code: str
    is_paused: bool
    pending_count: int
    changed_by_name: str
    changed_at: Optional[datetime]


class ToolActionOut(Schema):
    tool: ToolOut
    changed: bool
    action: str


def _to_tool_out(row: ToolPause) -> ToolOut:
    return ToolOut(
        tool_code=row.tool_code,
        is_paused=row.is_paused,
        pending_count=getattr(row, "pending_count", 0),
        changed_by_name=getattr(row.changed_by, "username", "") if row.changed_by_id else "",
        changed_at=row.changed_at,
    )


class ToolEventOut(Schema):
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
    rows = (
        OffsetSubmission.objects.select_related("tool")
        .all()[:200]
    )
    return [_to_out(r) for r in rows]


@api.get("/submissions/{submission_id}", response=SubmissionOut, auth=bearer_auth)
def get_submission(request: HttpRequest, submission_id: int):
    try:
        row = OffsetSubmission.objects.select_related("tool").get(pk=submission_id)
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
    tool = ensure_tool(tool_code)
    row = OffsetSubmission.objects.create(
        tool=tool,
        offset_um=body.offset_um,
        submitted_by=user,
        status=OffsetSubmission.Status.PENDING,
    )
    return _to_out(row)


def _tools_queryset():
    return (
        ToolPause.objects.select_related("changed_by")
        .annotate(
            pending_count=Count(
                "submissions",
                filter=Q(submissions__status=OffsetSubmission.Status.PENDING),
            )
        )
        .order_by("-is_paused", "tool_code")
    )


@api.get("/tools", response=list[ToolOut], auth=bearer_auth)
def list_tools(request: HttpRequest):
    """暂停台数据：各刀是否暂停直接取库内闸门行（认领跳过同读此源）。"""
    return [_to_tool_out(r) for r in _tools_queryset()]


def _require_writer(user: User) -> None:
    if not user.can_write:
        raise HttpError(403, "当前账号只读，不能暂停或恢复刀具")


def _flip_pause(user: User, tool_code: str, paused: bool, action: str) -> dict:
    _require_writer(user)
    normalized = tool_code.strip()
    if not normalized:
        raise HttpError(400, "刀具编号不能为空")
    _, changed = set_tool_paused(normalized, paused=paused, actor=user)
    tool = _tools_queryset().get(tool_code=normalized)
    return {"tool": _to_tool_out(tool), "changed": changed, "action": action}


@api.post("/tools/{tool_code}/pause", response=ToolActionOut, auth=bearer_auth)
def pause_tool(request: HttpRequest, tool_code: str):
    return _flip_pause(request.auth, tool_code, paused=True, action="pause")


@api.post("/tools/{tool_code}/resume", response=ToolActionOut, auth=bearer_auth)
def resume_tool(request: HttpRequest, tool_code: str):
    return _flip_pause(request.auth, tool_code, paused=False, action="resume")


@api.get("/tool-events", response=list[ToolEventOut], auth=bearer_auth)
def list_tool_events(request: HttpRequest):
    """痕迹簿：所有登录用户（含只读复核员）均可查看，仅追加不可改。"""
    rows = (
        ToolPauseEvent.objects.select_related("tool", "actor")
        .all()[:200]
    )
    return [
        ToolEventOut(
            id=r.id,
            tool_code=r.tool_id,
            action=r.action,
            actor_name=r.actor_name or (r.actor.username if r.actor_id else ""),
            created_at=r.created_at,
        )
        for r in rows
    ]
