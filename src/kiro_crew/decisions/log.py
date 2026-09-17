"""The shadow log: one JSONL line per decision the gate actually attempted.

``~/.kiro/crew/decisions/decisions-YYYYMMDD.jsonl``, mode 0600 in a 0700
directory. Day-rotated by filename so a retention sweep is ``unlink`` on whole
files rather than a rewrite, and so a reader can bound its work by date without
parsing.

What is NOT logged
------------------
A row exists only where a decision was ATTEMPTED. The three cheap refusals --
``preview`` off, ``arm: off``, outside the bucket -- write nothing, which is
what makes "``preview=false`` leaves the log directory empty" a checkable claim
rather than a hope. Logging them would also invert the cost: the whole point of
the ``preview`` gate is that a disabled seam touches no disk.

A scrub hit and a provider error DO get a row, because both are findings. A
silent scrub is the worst outcome available here: the operator would see a
missing row and conclude the seam is not firing, when in fact it fires and
refuses every time.

Why the state is never in the row
---------------------------------
``state`` leaves the machine when the seam is armed; it does not also get
written to disk. The row carries what the decision COST and what it CONCLUDED
-- enough to measure agreement, latency and spend -- and nothing that would make
this file a second copy of the conversation. ``session`` is a truncated SHA-256
of the session key for the same reason: it groups rows without naming a chat.

Why a plain synchronous append on the event loop
-----------------------------------------------
One ``O_APPEND`` write of a few hundred bytes to a local file, no lock and no
read-modify-write. That is microseconds, and ``O_APPEND`` gives atomicity for
writes under ``PIPE_BUF`` so two concurrent points cannot interleave a line.
``atomic_write`` is the wrong tool here in the other direction: it replaces the
whole file, so appending line N would rewrite N-1 lines and turn a constant-cost
write into a linear one.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from kiro_crew.config.paths import config_dir
from kiro_crew.decisions.types import Answers

logger = logging.getLogger(__name__)

#: Directory mode. 0700 so the log is unreadable by other local accounts even
#: before the per-file mode applies -- a decision row names no secret, but it
#: does reveal which points fire, how often, and with what verdicts.
_DIR_MODE = 0o700

#: File mode for a fresh log file. Applied via ``os.open``'s mode argument,
#: which the umask can only NARROW, so a permissive umask cannot widen it.
_FILE_MODE = 0o600

#: Characters of hex kept from the session-key digest. 12 hex = 48 bits: enough
#: that two live sessions colliding is not a practical concern, short enough
#: that the value is obviously an opaque grouping key and not a handle.
_SESSION_HEX = 12

#: Filename stem. The date suffix and ``.jsonl`` are appended.
_STEM = "decisions-"


def log_dir() -> Path:
    """``~/.kiro/crew/decisions`` -- not created by this call."""
    return config_dir() / "decisions"


def log_path(when: date | None = None) -> Path:
    """The log file for *when* (default: today, UTC).

    UTC, not local time, so a row's filename and its own ``ts`` never disagree
    about which day it belongs to -- a reader that bounds work by filename would
    otherwise miss rows either side of a timezone offset.
    """
    day = when or datetime.now(timezone.utc).date()
    return log_dir() / f"{_STEM}{day:%Y%m%d}.jsonl"


def session_digest(session_key: str | None) -> str:
    """Truncated SHA-256 of *session_key*, for grouping rows without naming one.

    ``None`` and ``""`` both hash the empty string, so a row from a keyless
    call is grouped with the other keyless rows rather than carrying a
    distinguishable ``null``. This is the SAME digest the bucket decision reads,
    so a row's ``session`` value is enough to re-derive why it was sampled.
    """
    return sha256((session_key or "").encode("utf-8")).hexdigest()[:_SESSION_HEX]


def _answers_json(answers: Answers | None) -> dict[str, dict[str, Any]] | None:
    """``{id: {value, p, confidence}}``, or ``None`` when there are no answers.

    ``value`` goes through ``json.dumps``' own type check at write time; an
    implementation that produced something unserialisable is a bug in that
    implementation, and letting the write raise there (caught below) is louder
    than coercing it to a string here and logging a plausible-looking lie.
    """
    if not answers:
        return None
    return {
        qid: {"value": a.value, "p": a.p, "confidence": a.confidence} for qid, a in answers.items()
    }


#: A point's own agreement rule. ``None`` keeps its documented meaning of NOT
#: COMPARABLE, so a rule may abstain exactly like the generic one.
AgreeFn = Callable[[Answers | None, "dict | None"], "bool | None"]


def agree(answers: Answers | None, baseline: dict | None) -> bool | None:
    """Whether *answers* and *baseline* said the same thing -- or ``None``.

    ``None`` means NOT COMPARABLE, and is returned rather than ``False``
    whenever a comparison would be meaningless: no baseline was supplied, the
    baseline is not a mapping, there are no answers, or the two do not cover the
    same question ids. Collapsing those onto ``False`` would let a missing
    baseline depress the measured agreement rate -- the one number this whole
    log exists to produce.

    The comparison is over ``value`` only. ``p`` and ``confidence`` describe how
    sure the model was, and the existing logic a baseline comes from has no
    counterpart for either, so including them would make agreement impossible by
    construction.
    """
    if baseline is None or not isinstance(baseline, dict) or not answers:
        return None
    if set(baseline) != set(answers):
        return None
    return all(answers[qid].value == baseline[qid] for qid in baseline)


def _resolved_agree(
    answers: Answers | None,
    baseline: dict | None,
    agree_fn: AgreeFn | None,
) -> bool | None:
    """*agree_fn*'s verdict when a point supplied one, else :func:`agree`.

    Two of the three shipped points cannot be judged by value equality over a
    shared id set. ``skills.select`` asks for ONE skill against a baseline that
    is a LIST -- set membership, not equality -- and asks a second question the
    baseline has no counterpart for; ``cron.novelty`` answers with a probability
    against a boolean. Under the generic rule both would log ``agree: null``
    forever and drop out of the one rate this log exists to produce, so a point
    hands in its own rule and the generic one stays the default.
    """
    if agree_fn is None:
        return agree(answers, baseline)
    return agree_fn(answers, baseline)


def build_row(
    *,
    point: str,
    arm: str,
    impl: str,
    session_key: str | None,
    latency_ms: int,
    answers: Answers | None = None,
    baseline: dict | None = None,
    in_tokens: int = 0,
    cost_usd: float = 0.0,
    scrubbed: bool = False,
    agree_fn: AgreeFn | None = None,
    error: str | None = None,
    ts: datetime | None = None,
) -> dict[str, Any]:
    """The row :func:`append` writes, built without touching the filesystem.

    Split out from the write so a test (and the report side) can assert the
    SHAPE without a temp home, and so the gate can build a row it then decides
    not to write.
    """
    moment = ts or datetime.now(timezone.utc)
    return {
        "ts": moment.isoformat(),
        "point": point,
        "arm": arm,
        "impl": impl,
        "session": session_digest(session_key),
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
        "in_tokens": in_tokens,
        "scrubbed": scrubbed,
        "answers": _answers_json(answers),
        "baseline": baseline,
        "agree": _resolved_agree(answers, baseline, agree_fn),
        "error": error,
    }


def append(row: dict[str, Any]) -> None:
    """Append *row* as one JSON line. Never raises.

    Best-effort by contract: the seam is an observation, so a read-only home, a
    full disk or a directory someone chmod-ed must not turn into a failed turn
    at the call site. The failure is logged at WARNING (not DEBUG) precisely
    because a silently missing log looks identical to a seam that is switched
    off, and an operator reading an empty report deserves to find out which.
    """
    try:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
        # O_APPEND: the kernel places each write at the then-current end of file,
        # so two points writing at once cannot interleave. The mode argument
        # applies only when this call CREATES the file; an existing file keeps
        # whatever mode it has, which is why day-rotation is the moment the
        # 0600 is actually established.
        fd = os.open(str(log_path()), os.O_WRONLY | os.O_CREAT | os.O_APPEND, _FILE_MODE)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception as exc:  # pragma: no cover - defensive, exercised by tests
        logger.warning("decisions: could not append log row: %s", exc)


def iter_log(since: datetime | None = None) -> Iterator[dict[str, Any]]:
    """Yield rows from newest-file-last, oldest row first, skipping junk.

    *since* filters on the row's own ``ts``, not on the filename, because a
    caller asking for "the last hour" means rows and a file boundary is an
    implementation detail. Files whose date is entirely before *since* are
    skipped without being opened, so a long history costs a ``glob`` rather than
    a full read.

    A line that will not parse is SKIPPED, not raised on: the writer appends and
    a reader may run mid-write, so the final line of today's file can legitimately
    be a partial one. Refusing the whole file for that would make the report
    unreadable at exactly the moment it is most wanted.
    """
    directory = log_dir()
    try:
        files = sorted(directory.glob(f"{_STEM}*.jsonl"))
    except OSError as exc:
        logger.warning("decisions: could not list %s: %s", directory, exc)
        return
    cutoff_day = since.astimezone(timezone.utc).date() if since is not None else None
    for path in files:
        if cutoff_day is not None:
            stamp = path.stem[len(_STEM) :]
            try:
                day = datetime.strptime(stamp, "%Y%m%d").date()
            except ValueError:
                # An unparseable stem is not evidence the file is irrelevant, so
                # read it rather than drop it -- the per-row ``ts`` filter below
                # still applies.
                day = None
            if day is not None and day < cutoff_day:
                continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("decisions: could not read %s: %s", path, exc)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if since is not None and not _at_or_after(row.get("ts"), since):
                continue
            yield row


def _at_or_after(raw_ts: Any, since: datetime) -> bool:
    """Whether *raw_ts* parses to a moment at or after *since*.

    An unparseable or absent ``ts`` returns True -- it is KEPT. A row that
    cannot date itself is still a row that happened, and dropping it would make
    a windowed report quietly smaller than the same report with no window.
    """
    if not isinstance(raw_ts, str):
        return True
    try:
        moment = datetime.fromisoformat(raw_ts)
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = since if since.tzinfo is not None else since.replace(tzinfo=timezone.utc)
    return moment >= cutoff
