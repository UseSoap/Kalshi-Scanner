"""python -m ev_betting run | notify-test | status"""

from __future__ import annotations

import argparse
import sys

from . import notify, paper
from .config import Settings
from .run import run


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m ev_betting")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="settle, scan, paper trade, notify")
    sub.add_parser("notify-test", help="send a test phone notification")
    sub.add_parser("status", help="print paper-trading summary")
    args = ap.parse_args(argv)
    cfg = Settings.from_env()

    if args.cmd == "run":
        out = run(cfg)
        # A missing/invalid key or quota problem should show as a failed run in the Actions tab.
        return 1 if out["error"] and "credits left" not in out["error"] else 0
    if args.cmd == "notify-test":
        ok = notify.send(cfg.ntfy_topic, "EV scanner test", "Notifications are working. You'll get one of these "
                         "for every +EV paper trade, plus a summary when trades settle.", tags="white_check_mark")
        print("sent" if ok else "NOT sent (is the NTFY_TOPIC secret set?)")
        return 0 if ok else 1
    if args.cmd == "status":
        print(paper.summary(paper.load(cfg.data_dir), cfg.bankroll))
        return 0


if __name__ == "__main__":
    sys.exit(main())
