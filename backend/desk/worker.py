"""Background worker: claim pending rows with SKIP LOCKED and apply verdict."""

import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import django


def setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()


def claim_one_pending():
    from desk.services import apply_verdict, claim_next_pending_submission

    # 认领与跳过暂停刀在同一服务函数内完成，跳过逻辑与库内暂停态同源。
    submission = claim_next_pending_submission()
    if submission is None:
        return False

    apply_verdict(submission)
    return True


def run_loop(poll_seconds: float = 0.5) -> None:
    setup_django()
    print("cnc-offset worker started", flush=True)
    while True:
        claimed = claim_one_pending()
        if not claimed:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    setup_django()
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        claim_one_pending()
    else:
        run_loop()
