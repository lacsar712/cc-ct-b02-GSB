"""真实并发验证：暂停与认领同一把刀同时发生时只允许一种结局。

依赖 PostgreSQL 的 FOR UPDATE / SKIP LOCKED 行锁；SQLite 无行锁语义，整模块跳过。
运行：配置好 POSTGRES_* 环境变量后执行 manage.py test desk.tests.test_concurrency。
"""

import threading

from django.db import connection, transaction
from django.test import TransactionTestCase

from desk.models import OffsetSubmission, ToolPause, User
from desk.services import claim_next_pending, ensure_tool, set_tool_paused


class PauseClaimConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            cls._skip = True
        else:
            cls._skip = False

    def setUp(self):
        if self._skip:
            self.skipTest("并发行锁测试仅在 PostgreSQL 上运行")
        self.op = User.objects.create_user(
            username="op", password="x", role=User.Role.MACHINIST
        )
        ensure_tool("TA")
        ensure_tool("TB")

    def _make_pending(self, code):
        return OffsetSubmission.objects.create(
            tool_id=code, offset_um=1, status=OffsetSubmission.Status.PENDING
        )

    def test_pause_commit_and_claim_interleave_single_outcome(self):
        """暂停事务持有 TA 刀闸行锁期间，认领必须 SKIP LOCKED 去领 TB；
        暂停提交后 TA 仍是暂停态，下一轮认领继续跳过 TA。"""
        a = self._make_pending("TA")
        b = self._make_pending("TB")

        pause_entered = threading.Event()
        pause_release = threading.Event()
        errors = []

        def pause_worker():
            try:
                with transaction.atomic():
                    set_tool_paused("TA", paused=True, actor=self.op)
                    pause_entered.set()
                    pause_release.wait(timeout=10)
            except Exception as exc:  # noqa: BLE001 - 断言在主线程做
                errors.append(exc)

        def claim_worker(result):
            try:
                # 等暂停事务已持锁后再认领。
                pause_entered.wait(timeout=10)
                result["row"] = claim_next_pending()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t_pause = threading.Thread(target=pause_worker)
        claim_result = {}
        t_claim = threading.Thread(target=claim_worker, args=(claim_result,))
        t_pause.start()
        t_claim.start()
        self.assertTrue(pause_entered.wait(timeout=10))
        # 认领拿到的必须是 TB（TA 的行被暂停事务锁住，SKIP LOCKED 越过）。
        t_claim.join(timeout=10)
        self.assertFalse(t_claim.is_alive())

        # 暂停尚未提交：TA 仍处于被锁状态；认领绝不能返回 TA。
        self.assertIsNotNone(claim_result.get("row"))
        self.assertEqual(claim_result["row"].tool_id, "TB")

        pause_release.set()
        t_pause.join(timeout=10)
        self.assertEqual(errors, [])

        # 暂停提交后 TA 为暂停态，且其候审单仍 pending；继续认领只会返回 None。
        self.assertTrue(ToolPause.objects.get(tool_code="TA").is_paused)
        a.refresh_from_db()
        self.assertEqual(a.status, OffsetSubmission.Status.PENDING)
        b.refresh_from_db()
        self.assertEqual(b.status, OffsetSubmission.Status.PROCESSING)
        self.assertIsNone(claim_next_pending())

    def test_claim_first_then_pause_waits_single_outcome(self):
        """反向时序：认领先持有 TA 行锁并提交“复核中”，暂停随后只影响闸门，
        不会把已认领记录改回候审，也不存在“显示已暂停却仍被领”的矛盾。"""
        a = self._make_pending("TA")
        claimed = claim_next_pending()
        self.assertEqual(claimed.pk, a.pk)

        tool, changed = set_tool_paused("TA", paused=True, actor=self.op)
        self.assertTrue(changed)
        self.assertTrue(tool.is_paused)
        a.refresh_from_db()
        self.assertEqual(a.status, OffsetSubmission.Status.PROCESSING)
        # 暂停之后新到的候审单立即被跳过。
        a2 = self._make_pending("TA")
        self.assertIsNone(claim_next_pending())
        a2.refresh_from_db()
        self.assertEqual(a2.status, OffsetSubmission.Status.PENDING)

    def test_resume_then_claim_returns_queued_row(self):
        a = self._make_pending("TA")
        set_tool_paused("TA", True, self.op)
        self.assertIsNone(claim_next_pending())
        set_tool_paused("TA", False, self.op)
        row = claim_next_pending()
        self.assertEqual(row.pk, a.pk)
