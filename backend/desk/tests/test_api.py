import pytest
from ninja.testing import TestClient

from desk.auth_utils import create_access_token
from desk.models import OffsetSubmission, ToolPauseLog
from desk.api import api


@pytest.fixture
def client():
    return TestClient(api)


@pytest.fixture
def machinist(django_user_model):
    return django_user_model.objects.create_user(
        username="op", password="x", role="machinist"
    )


@pytest.fixture
def auditor(django_user_model):
    return django_user_model.objects.create_user(
        username="au", password="x", role="auditor"
    )


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def _submit(tool, offset=5):
    return OffsetSubmission.objects.create(
        tool_code=tool, offset_um=offset, status=OffsetSubmission.Status.PENDING
    )


@pytest.mark.django_db
def test_pause_shows_on_gate_and_submissions_share_source(client, machinist):
    _submit("T01")
    resp = client.post("/tool-pauses/pause", json={"tool_code": "T01"}, headers=_auth(machinist))
    assert resp.status_code == 200
    body = resp.json()
    assert body["tool_code"] == "T01"
    assert body["is_paused"] is True
    assert body["pending_count"] == 1
    assert body["updated_by_name"] == "op"

    # 闸页列表读同一状态
    gate = client.get("/tool-pauses", headers=_auth(machinist)).json()
    t01 = [r for r in gate if r["tool_code"] == "T01"][0]
    assert t01["is_paused"] is True

    # 总览单据标记同源
    subs = client.get("/submissions", headers=_auth(machinist)).json()
    assert [s for s in subs if s["tool_code"] == "T01"][0]["tool_paused"] is True


@pytest.mark.django_db
def test_pause_twice_conflicts_resume_restores(client, machinist):
    _submit("T01")
    r1 = client.post("/tool-pauses/pause", json={"tool_code": "T01"}, headers=_auth(machinist))
    assert r1.status_code == 200
    r2 = client.post("/tool-pauses/pause", json={"tool_code": "T01"}, headers=_auth(machinist))
    assert r2.status_code == 409
    r3 = client.post("/tool-pauses/resume", json={"tool_code": "T01"}, headers=_auth(machinist))
    assert r3.status_code == 200
    assert r3.json()["is_paused"] is False
    # 恢复后单据不再标记暂停
    subs = client.get("/submissions", headers=_auth(machinist)).json()
    assert [s for s in subs if s["tool_code"] == "T01"][0]["tool_paused"] is False


@pytest.mark.django_db
def test_auditor_is_read_only(client, auditor, machinist):
    _submit("T01")
    # 操作员先暂停一次产生数据
    client.post("/tool-pauses/pause", json={"tool_code": "T01"}, headers=_auth(machinist))
    # 复核员可读闸页
    gate = client.get("/tool-pauses", headers=_auth(auditor))
    assert gate.status_code == 200
    assert gate.json()[0]["is_paused"] is True
    # 复核员可读痕迹簿
    logs = client.get("/tool-pause-logs", headers=_auth(auditor))
    assert logs.status_code == 200
    assert len(logs.json()) == 1
    # 复核员不可暂停 / 恢复
    assert (
        client.post("/tool-pauses/pause", json={"tool_code": "T02"}, headers=_auth(auditor)).status_code
        == 403
    )
    assert (
        client.post("/tool-pauses/resume", json={"tool_code": "T01"}, headers=_auth(auditor)).status_code
        == 403
    )
    # 状态未被复核员改动
    assert ToolPauseLog.objects.count() == 1


@pytest.mark.django_db
def test_pause_unknown_tool_still_registered_and_logged(client, machinist):
    # 暂停一把当前无候审单的刀：闸页登记、待审数 0、恢复前新单不会被领
    resp = client.post("/tool-pauses/pause", json={"tool_code": "T88"}, headers=_auth(machinist))
    assert resp.status_code == 200
    assert resp.json()["pending_count"] == 0
    _submit("T88")
    gate = [r for r in client.get("/tool-pauses", headers=_auth(machinist)).json()
            if r["tool_code"] == "T88"][0]
    assert gate["is_paused"] is True
    assert gate["pending_count"] == 1


@pytest.mark.django_db
def test_empty_tool_code_rejected(client, machinist):
    resp = client.post("/tool-pauses/pause", json={"tool_code": "   "}, headers=_auth(machinist))
    assert resp.status_code == 400


@pytest.mark.django_db
def test_logs_record_pause_and_resume_chronology(client, machinist):
    client.post("/tool-pauses/pause", json={"tool_code": "T05"}, headers=_auth(machinist))
    client.post("/tool-pauses/resume", json={"tool_code": "T05"}, headers=_auth(machinist))
    logs = client.get("/tool-pause-logs", headers=_auth(machinist)).json()
    # 痕迹簿按时间倒序：恢复在前、暂停在后
    assert [(l["action"], l["tool_code"], l["actor_name"]) for l in logs] == [
        ("resume", "T05", "op"),
        ("pause", "T05", "op"),
    ]


@pytest.mark.django_db
def test_gate_lists_tools_with_pending_without_pause_row(client, machinist):
    _submit("T03")
    gate = client.get("/tool-pauses", headers=_auth(machinist)).json()
    t03 = [r for r in gate if r["tool_code"] == "T03"][0]
    assert t03["is_paused"] is False
    assert t03["pending_count"] == 1
