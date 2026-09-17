"""The shadow log: row shape, file permissions, day rotation, and ``iter_log``.

The permission assertions are the security-relevant ones: the row names no
credential, but it does reveal which points fire and with what verdicts, so the
directory is 0700 and a fresh file 0600 REGARDLESS of umask.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import date, datetime, timedelta, timezone

import pytest

from kiro_crew.decisions import log as log_mod
from kiro_crew.decisions.log import (
    agree,
    append,
    build_row,
    iter_log,
    log_path,
    session_digest,
)
from kiro_crew.decisions.types import Answer


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect the log directory into *tmp_path* and hand back its path."""
    directory = tmp_path / "decisions"
    monkeypatch.setattr(log_mod, "log_dir", lambda: directory)
    return directory


def _row(**kw):
    base = dict(
        point="skills.select",
        arm="shadow",
        impl="jev",
        session_key="sess-1",
        latency_ms=212,
    )
    base.update(kw)
    return build_row(**base)


# ---------------------------------------------------------------------------
# Row shape
# ---------------------------------------------------------------------------


class TestRowShape:
    def test_every_documented_key_is_present(self):
        row = _row()
        assert set(row) == {
            "ts",
            "point",
            "arm",
            "impl",
            "session",
            "latency_ms",
            "cost_usd",
            "in_tokens",
            "scrubbed",
            "answers",
            "baseline",
            "agree",
            "error",
        }

    def test_answers_are_flattened_to_value_p_confidence(self):
        answers = {
            "verdict": Answer(id="verdict", value="DUP", p=0.9, confidence=0.8),
            "urgency": Answer(id="urgency", value=0.42, p=0.42, confidence=None),
        }
        row = _row(answers=answers)
        assert row["answers"] == {
            "verdict": {"value": "DUP", "p": 0.9, "confidence": 0.8},
            "urgency": {"value": 0.42, "p": 0.42, "confidence": None},
        }

    def test_no_answers_is_null_not_an_empty_object(self):
        """A report distinguishes "no row" from "a row that answered nothing"."""
        assert _row()["answers"] is None

    def test_the_session_key_is_never_written_verbatim(self):
        row = _row(session_key="chat-154-1789627588")
        assert "chat-154" not in json.dumps(row)
        assert row["session"] == session_digest("chat-154-1789627588")
        assert len(row["session"]) == 12

    def test_the_digest_is_stable_and_keyless_calls_share_a_bucket(self):
        assert session_digest("a") == session_digest("a")
        assert session_digest(None) == session_digest("")
        assert session_digest("a") != session_digest("b")

    def test_ts_is_utc_and_parseable(self):
        moment = datetime.fromisoformat(_row()["ts"])
        assert moment.tzinfo is not None
        assert moment.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# agree
# ---------------------------------------------------------------------------


class TestAgree:
    def _answers(self, **values):
        return {k: Answer(id=k, value=v, p=1.0) for k, v in values.items()}

    def test_same_values_agree(self):
        assert agree(self._answers(v="DUP"), {"v": "DUP"}) is True

    def test_different_values_disagree(self):
        assert agree(self._answers(v="DUP"), {"v": "NONE"}) is False

    def test_all_ids_must_match_for_true(self):
        answers = self._answers(a="x", b="y")
        assert agree(answers, {"a": "x", "b": "y"}) is True
        assert agree(answers, {"a": "x", "b": "z"}) is False

    @pytest.mark.parametrize(
        "answers,baseline",
        [
            ({"v": Answer("v", "DUP", 1.0)}, None),
            ({"v": Answer("v", "DUP", 1.0)}, "not a dict"),
            ({"v": Answer("v", "DUP", 1.0)}, ["not a dict"]),
            ({}, {"v": "DUP"}),
            (None, {"v": "DUP"}),
            # Disjoint / partial id coverage is NOT COMPARABLE, not disagreement.
            ({"v": Answer("v", "DUP", 1.0)}, {"other": "DUP"}),
            ({"v": Answer("v", "DUP", 1.0)}, {"v": "DUP", "extra": 1}),
        ],
    )
    def test_incomparable_is_none_never_false(self, answers, baseline):
        """None, not False -- otherwise a missing baseline depresses the rate."""
        assert agree(answers, baseline) is None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


class TestAppend:
    def test_a_row_lands_as_one_json_line(self, home):
        append(_row())
        text = log_path().read_text()
        assert text.endswith("\n")
        assert len(text.strip().splitlines()) == 1
        assert json.loads(text)["point"] == "skills.select"

    def test_rows_accumulate_rather_than_replace(self, home):
        for i in range(5):
            append(_row(latency_ms=i))
        lines = log_path().read_text().strip().splitlines()
        assert [json.loads(ln)["latency_ms"] for ln in lines] == [0, 1, 2, 3, 4]

    def test_the_directory_is_owner_only(self, home):
        append(_row())
        mode = stat.S_IMODE(os.stat(home).st_mode)
        assert mode == 0o700, oct(mode)

    def test_a_fresh_file_is_owner_only_despite_a_permissive_umask(self, home):
        """0600 must come from the open mode, not from inherited umask luck."""
        previous = os.umask(0o000)
        try:
            append(_row())
        finally:
            os.umask(previous)
        mode = stat.S_IMODE(os.stat(log_path()).st_mode)
        assert mode == 0o600, oct(mode)

    def test_the_filename_carries_the_utc_day(self, home):
        append(_row())
        today = datetime.now(timezone.utc).date()
        assert log_path().name == f"decisions-{today:%Y%m%d}.jsonl"

    def test_an_unwritable_home_is_survived_not_raised(self, tmp_path, monkeypatch):
        """Best-effort by contract: an observation must not fail a turn."""
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file, not a directory")
        monkeypatch.setattr(log_mod, "log_dir", lambda: blocked / "decisions")
        append(_row())  # must not raise

    def test_an_unserialisable_value_is_survived(self, home):
        """``default=str`` keeps an odd value from losing the whole row."""

        class _Odd:
            def __str__(self):
                return "odd-value"

        append(_row(answers={"v": Answer("v", _Odd(), 1.0)}))
        assert json.loads(log_path().read_text())["answers"]["v"]["value"] == "odd-value"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class TestIterLog:
    def _write_day(self, home, day: date, rows: list[dict]) -> None:
        home.mkdir(parents=True, exist_ok=True)
        path = home / f"decisions-{day:%Y%m%d}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_no_directory_yields_nothing(self, home):
        assert list(iter_log()) == []

    def test_rows_come_back_oldest_file_first(self, home):
        today = datetime.now(timezone.utc).date()
        self._write_day(home, today - timedelta(days=2), [{"point": "old"}])
        self._write_day(home, today, [{"point": "new"}])
        assert [r["point"] for r in iter_log()] == ["old", "new"]

    def test_a_partial_final_line_is_skipped_not_fatal(self, home):
        """A reader may run mid-write; that must not make the report unreadable."""
        home.mkdir(parents=True)
        today = datetime.now(timezone.utc).date()
        path = home / f"decisions-{today:%Y%m%d}.jsonl"
        path.write_text(json.dumps({"point": "good"}) + '\n{"point": "trunc')
        assert [r["point"] for r in iter_log()] == ["good"]

    def test_blank_lines_and_non_objects_are_skipped(self, home):
        home.mkdir(parents=True)
        today = datetime.now(timezone.utc).date()
        path = home / f"decisions-{today:%Y%m%d}.jsonl"
        path.write_text('\n\n[1,2,3]\n{"point": "good"}\n\n')
        assert [r["point"] for r in iter_log()] == ["good"]

    def test_since_filters_on_the_rows_own_ts(self, home):
        today = datetime.now(timezone.utc)
        rows = [
            {"point": "early", "ts": (today - timedelta(hours=5)).isoformat()},
            {"point": "late", "ts": (today - timedelta(minutes=5)).isoformat()},
        ]
        self._write_day(home, today.date(), rows)
        cutoff = today - timedelta(hours=1)
        assert [r["point"] for r in iter_log(since=cutoff)] == ["late"]

    def test_since_skips_whole_older_files_without_reading_them(self, home):
        today = datetime.now(timezone.utc)
        # No ``ts`` at all: if this file were opened, the row would be KEPT (an
        # undateable row is kept), so its absence proves the file was skipped by
        # filename.
        self._write_day(home, today.date() - timedelta(days=3), [{"point": "ancient"}])
        self._write_day(home, today.date(), [{"point": "today"}])
        got = [r["point"] for r in iter_log(since=today - timedelta(hours=2))]
        assert got == ["today"]

    def test_a_row_with_no_ts_is_kept(self, home):
        """A row that cannot date itself still happened."""
        today = datetime.now(timezone.utc)
        self._write_day(home, today.date(), [{"point": "undated"}])
        assert [r["point"] for r in iter_log(since=today - timedelta(hours=1))] == ["undated"]

    def test_a_naive_ts_is_read_as_utc(self, home):
        today = datetime.now(timezone.utc)
        naive = (today - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
        self._write_day(home, today.date(), [{"point": "naive", "ts": naive}])
        assert [r["point"] for r in iter_log(since=today - timedelta(hours=1))] == ["naive"]

    def test_a_written_row_round_trips_through_iter_log(self, home):
        answers = {"verdict": Answer("verdict", "DUP", 0.9, 0.8)}
        append(_row(answers=answers, baseline={"verdict": "DUP"}))
        rows = list(iter_log())
        assert len(rows) == 1
        assert rows[0]["agree"] is True
        assert rows[0]["answers"]["verdict"]["value"] == "DUP"
