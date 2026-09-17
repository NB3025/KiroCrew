"""``kirocrew decisions`` -- read the decision-preview shadow log.

Thin CLI layer: argument handling and output. The reading and the arithmetic
live in :mod:`kiro_crew.decisions.report`, so the same numbers are available to
anything else that wants them without going through argv.

One subcommand today. ``report`` answers the only question a shadow arm exists
to answer: over this window, how often did the oracle agree with the logic that
actually shipped, how confident was it when it did, and what did asking cost.
"""

from __future__ import annotations

import argparse
import json
import sys

from kiro_crew import cli_help
from kiro_crew.decisions import report as decisions_report

# Default window. A day is the unit a shadow run is judged in -- long enough to
# cover an overnight soak, short enough that the reader is looking at current
# behavior rather than an average over a config that has since changed.
DEFAULT_SINCE = "1d"


def _decisions_report(args: argparse.Namespace) -> int:
    try:
        since = decisions_report.parse_since(args.since)
    except decisions_report.SinceError as exc:
        print(f"kirocrew decisions report: {exc}", file=sys.stderr)
        return 2
    payload = decisions_report.report(since=since, point=args.point or None)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(decisions_report.render_text(payload))
    # An empty log is the normal state of a preview nobody enabled, so it exits
    # 0 with a friendly line -- a non-zero code here would make "not turned on"
    # indistinguishable from "the reader broke".
    return 0


def decisions_cmd(args: argparse.Namespace) -> int:
    """Dispatch a ``decisions`` subcommand."""
    if args.decisions_action == "report":
        return _decisions_report(args)
    print("Usage: kirocrew decisions report [--since 1d] [--point NAME] [--json]", file=sys.stderr)
    return 2


def register_decisions_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Wire ``kirocrew decisions`` into the top-level parser."""
    decisions_parser = cli_help.add_command(sub, "decisions")
    decisions_sub = decisions_parser.add_subparsers(dest="decisions_action")
    rep = decisions_sub.add_parser(
        "report",
        help="Summarise the decision-preview shadow log",
        description=(
            "Read the decision-preview log and report, per decision point and "
            "implementation, how often the oracle agreed with the shipped logic, "
            "how well its confidence was calibrated, its latency and its cost."
        ),
    )
    rep.add_argument(
        "--since",
        default=DEFAULT_SINCE,
        metavar="WINDOW",
        help=f"Window to read: 30m, 12h, 7d, 2w or an ISO timestamp (default: {DEFAULT_SINCE})",
    )
    rep.add_argument(
        "--point",
        default="",
        metavar="NAME",
        help="Only this decision point (e.g. skills.select)",
    )
    rep.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Emit the report as JSON instead of a table",
    )
