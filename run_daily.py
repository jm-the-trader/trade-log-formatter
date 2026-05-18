"""Orchestrator: fetch new IB reports from Gmail, run formatter, summarize.

Usage:
    python run_daily.py            # fetch + format + summarize (default)
    python run_daily.py --fetch    # fetch only
    python run_daily.py --format   # format only
"""

import argparse
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from email_fetcher import fetch_new_reports, load_config, load_state
from notifier import report_failure
from summarize import append_summary, report_dates_from_paths

SCRIPT_DIR = Path(__file__).resolve().parent
FORMATTER = SCRIPT_DIR / "trade-log-formatter.py"
MASTER_XLSX = SCRIPT_DIR / "master-trades.xlsx"


def run_formatter(month: str, apply: bool = True) -> int:
    """Run the formatter, piping the target MM.YYYY month + y/N confirmation.

    The formatter prompts twice via input():
        1. the month folder (we always feed `month`)
        2. 'Apply these changes? (y/N):' — feed 'y' to write, 'n' to dry-run.

    Pre-feeding both lines before stdin closes prevents the EOFError that
    used to bubble up as 'Unexpected error processing folder' whenever this
    script ran from automation.
    """
    answer = "y" if apply else "n"
    logging.info("Running formatter for %s (apply=%s): %s", month, apply, FORMATTER)
    result = subprocess.run(
        [sys.executable, str(FORMATTER)],
        cwd=SCRIPT_DIR,
        input=f"{month}\n{answer}\n",
        text=True,
    )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true", help="Only fetch emails")
    parser.add_argument("--format", dest="do_format", action="store_true", help="Only run formatter")
    parser.add_argument(
        "--month",
        default=datetime.now().strftime("%m.%Y"),
        help="Month folder to format (MM.YYYY). Defaults to current month.",
    )
    parser.add_argument(
        "--no-apply",
        dest="apply",
        action="store_false",
        help="Run formatter in preview mode (won't write to Trades.xlsx).",
    )
    parser.set_defaults(apply=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    do_fetch = args.fetch or not args.do_format
    do_format = args.do_format or not args.fetch

    config = load_config()
    saved: list[Path] = []

    if do_fetch:
        try:
            state = load_state()
            saved = fetch_new_reports(config, state)
            logging.info("Fetched %d new report(s).", len(saved))
        except Exception as e:
            report_failure(config, "email_fetch", e)
            logging.error("Email fetch failed: %s", e)
            return 1

    if do_format:
        try:
            rc = run_formatter(args.month, apply=args.apply)
            if rc != 0:
                raise RuntimeError(f"formatter exited with code {rc}")
        except Exception as e:
            report_failure(config, "formatter", e)
            logging.error("Formatter failed: %s", e)
            return 1

        # Summarize newly fetched report dates after formatter has updated master.
        try:
            dates = report_dates_from_paths(saved)
            lines = append_summary(MASTER_XLSX, dates)
            if lines:
                print()
                print("Daily summary appended to daily_summary.md:")
                for line in lines:
                    print(f"  {line}")
        except Exception as e:
            # Summary failure is non-fatal — log it but don't error out.
            report_failure(config, "summarize", e)
            logging.warning("Summary generation failed (non-fatal): %s", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
