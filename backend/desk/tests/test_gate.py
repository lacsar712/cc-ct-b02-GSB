"""暂停闸、认领跳过、痕迹簿与只读权限测试（SQLite/Postgres 均可运行）。"""

from django.test import TestCase
from ninja.testing import TestClient

from desk.api import api
from desk.auth_utils import create_access_token
from desk.models import OffsetSubmission, ToolPause, ToolPauseEvent, User
from desk.services import (
    apply_verdict,
    claim_next_pending,
    ensure_tool,
    set_tool_paused,
)


class GateFlowTests(TestCase):
    def setUp(self):
        self.machinist = User.objects.create_user(
            username="op", password="x", role=User.Role.MACHINIST
        )
        self.auditor = User.objects.create_user(
            username="au", password="x", role=User.Role.AUDITOR
        )
        self.tool_a = ensure_tool("TA")
        self.tool_b = ensure_tool("TB")

    def _pending(self, tool):
        return OffsetSubmission.objects.create(
            tool=tool, offset_um=3, status=OffsetSubmission.Status.PENDING
        )

    def test_paused_tool_skipped_others_claimed_then_resume_order(self):
        """核心时序：甲刀候审时暂停 → 乙刀先领 → 恢复甲刀 → 甲刀才被领。"""
        a1 = self._pending(self.tool_a)
        b1 = self._pending(self.tool_b)

        set_tool_paused("TA", paused=True, actor=self.machinist)

        claimed = claim_next_pending()
        self.assertEqual(claimed.pk, b1.pk)
        apply_verdict(claimed)

        # 甲刀仍候审：暂停期间无人认领。
        a1.refresh_from_db()
        self.assertEqual(a1.status, OffsetSubmission.Status.PENDING)
        self.assertIsNone(claim_next_pending())

        set_tool_paused("TA", paused=False, actor=self.machinist)
        claimed_a = claim_next_pending()
        self.assertEqual(claimed_a.pk, a1.pk)
        apply_verdict(claimed_a)
        a1.refresh_from_db()
        self.assertEqual(a1.status, OffsetSubmission.Status.DONE)

    def test_all_pending_rows_of_paused_tool_skipped(self):
        a1 = self._pending(self.tool_a)
        a2 = self._pending(self.tool_a)
        set_tool_paused("TA", paused=True, actor=self.machinist)
        self.assertIsNone(claim_next_pending())
        set_tool_paused("TA", paused=False, actor=self.machinist)
        first = claim_next_pending()
        self.assertIn(first.pk, {a1.pk, a2.pk})

    def test_event_log_append_only_and_ordered(self):
        set_tool_paused("TA", True, self.machinist)
        # 重复暂停不翻转、不留新痕。
        _, changed = set_tool_paused("TA", True, self.machinist)
        self.assertFalse(changed)
        set_tool_paused("TA", False, self.machinist)

        events = list(ToolPauseEvent.objects.all())
        self.assertEqual([e.action for e in events], ["resume", "pause"])
        self.assertEqual({e.tool_id for e in events}, {"TA"})
        self.assertTrue(all(e.actor_name == "op" for e in events))
        self.assertEqual(ToolPauseEvent.objects.count(), 2)

    def test_new_tool_created_by_submission_starts_claimable(self):
        ensure_tool("TC")
        row = self._pending(ToolPause.objects.get(tool_code="TC"))
        self.assertEqual(claim_next_pending().pk, row.pk)


class GateApiTests(TestCase):
    def setUp(self):
        self.client = TestClient(api)
        self.machinist = User.objects.create_user(
            username="op", password="x", role=User.Role.MACHINIST
        )
        self.auditor = User.objects.create_user(
            username="au", password="x", role=User.Role.AUDITOR
        )
        self.tool = ensure_tool("T1")
        self.sub = OffsetSubmission.objects.create(
            tool=self.tool,
            offset_um=20,
            status=OffsetSubmission.Status.PENDING,
            submitted_by=self.machinist,
        )

    def _auth(self, user):
        return {"Authorization": f"Bearer {create_access_token(user)}"}

    def test_gate_and_claim_share_single_source(self):
        r = self.client.post(
            "/tools/T1/pause", headers=self._auth(self.machinist)
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["tool"]["is_paused"])

        tools = self.client.get("/tools", headers=self._auth(self.machinist)).json()
        self.assertEqual(tools[0]["tool_code"], "T1")
        self.assertTrue(tools[0]["is_paused"])
        self.assertEqual(tools[0]["pending_count"], 1)

        # 总览列表的暂停徽标与暂停台、认领跳过同读 ToolPause.is_paused。
        subs = self.client.get("/submissions", headers=self._auth(self.machinist)).json()
        self.assertEqual(len(subs), 1)
        self.assertTrue(subs[0]["is_paused"])
        self.assertEqual(subs[0]["tool_code"], "T1")

        # 库内认领查询同样跳过。
        self.assertIsNone(claim_next_pending())

        r = self.client.post(
            "/tools/T1/resume", headers=self._auth(self.machinist)
        )
        self.assertFalse(r.json()["tool"]["is_paused"])
        subs = self.client.get("/submissions", headers=self._auth(self.machinist)).json()
        self.assertFalse(subs[0]["is_paused"])

    def test_auditor_read_only(self):
        h = self._auth(self.auditor)
        self.assertEqual(self.client.get("/tools", headers=h).status_code, 200)
        self.assertEqual(self.client.get("/tool-events", headers=h).status_code, 200)
        self.assertEqual(
            self.client.post("/tools/T1/pause", headers=h).status_code, 403
        )
        self.assertEqual(
            self.client.post("/tools/T1/resume", headers=h).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                "/submissions",
                json={"tool_code": "T9", "offset_um": 1},
                headers=h,
            ).status_code,
            403,
        )

    def test_pause_actions_are_recorded_in_log_for_everyone(self):
        self.client.post("/tools/T1/pause", headers=self._auth(self.machinist))
        events = self.client.get(
            "/tool-events", headers=self._auth(self.auditor)
        ).json()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "pause")
        self.assertEqual(events[0]["tool_code"], "T1")
        self.assertEqual(events[0]["actor_name"], "op")
