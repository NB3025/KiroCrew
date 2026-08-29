"""Crew-scoped MCP session reads — the ``crew=`` branches of the three read tools.

These branches had NO test module, which is why ``mcp_tools/sessions.py`` sat below
the per-file coverage floor: the handler side is covered by ``test_instances.py``
and the private-memory fence by ``test_member_memory_runtime.py``, but the
tool-side formatting, error and empty-result paths were unexercised.

Every test here drives the real helper against a fake ``mcp_core._get``. The fake
returns a ``dict``, which is what ``_get`` is annotated to return and what it
answers on failure too (``{"error": ...}``), so the seam models the real transport
rather than a convenient shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from kiro_crew import mcp_core
from kiro_crew.mcp_tools import sessions as s


class _Recorder:
    """A faithful stand-in for ``mcp_core._get``: records paths, returns a dict."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.paths: list[str] = []

    def __call__(self, path: str, *a: Any, **kw: Any) -> Any:
        self.paths.append(path)
        return self.reply


@pytest.fixture
def get(monkeypatch: pytest.MonkeyPatch):
    """Install a recorder for ``mcp_core._get`` and hand it back."""

    def _install(reply: Any) -> _Recorder:
        rec = _Recorder(reply)
        monkeypatch.setattr(mcp_core, "_get", rec)
        return rec

    return _install


# ── the query builder ────────────────────────────────────────────────────────


def test_empty_params_are_dropped_so_an_absent_optional_is_not_sent_as_blank():
    """An absent optional must vanish, not become ``key=``."""
    qs = s._crew_qs({"crew": "chick", "q": "", "limit": "10"})
    assert "q=" not in qs
    assert "crew=chick" in qs and "limit=10" in qs


def test_a_crew_name_with_spaces_is_url_encoded_rather_than_breaking_the_query():
    qs = s._crew_qs({"crew": "My Crew", "limit": "5"})
    assert "My+Crew" in qs or "My%20Crew" in qs


# ── the three literal paths (regression pin for the transport-resolution gate) ─


@pytest.mark.parametrize(
    "call, expected",
    [
        (lambda: s._crew_search_history("chick", "q", 10), "/api/crew-sessions/search?"),
        (lambda: s._crew_list_sessions("chick", 10), "/api/crew-sessions/list?"),
        (lambda: s._crew_get_session("chick", "dashboard:1", 50), "/api/crew-sessions/read?"),
    ],
)
def test_each_helper_spells_its_own_literal_path_with_only_the_query_dynamic(get, call, expected):
    """Pins the property ``test_every_transport_call_resolves_to_a_path`` needs.

    That guard truncates a path at the first ``?``, so the literal prefix must be
    visible at the call site. Routing all three through one ``_get(path=...)``
    made the resolved path start with an unknown and failed the guard; this test
    fails if anyone reintroduces that indirection.
    """
    rec = get({"sessions": [], "messages": []})
    call()
    assert rec.paths[0].startswith(expected)


# ── error propagation ────────────────────────────────────────────────────────


def test_a_gateway_error_is_reported_against_the_named_crew_and_verb(get):
    get({"error": "crew not connected"})
    out = s._crew_search_history("chick", "anything", 10)
    assert "chick" in out and "search" in out and "crew not connected" in out


def test_a_non_dict_reply_reports_unreachable_rather_than_raising(get):
    """The defensive arm of ``_crew_error``: anything not a dict has no ``error``."""
    get(None)
    assert "unreachable" in s._crew_list_sessions("chick", 10)


@pytest.mark.parametrize(
    "helper, verb",
    [
        (lambda: s._crew_list_sessions("chick", 10), "session list"),
        (lambda: s._crew_get_session("chick", "dashboard:1", 50), "session read"),
    ],
)
def test_each_helper_names_its_own_verb_in_a_failure(get, helper, verb):
    get({"error": "boom"})
    assert verb in helper()


# ── empty results ────────────────────────────────────────────────────────────


def test_no_matches_says_so_against_the_crew_instead_of_returning_an_empty_list(get):
    get({"sessions": []})
    assert "No matching conversations" in s._crew_search_history("chick", "zzz", 10)


def test_no_sessions_says_so_against_the_crew(get):
    get({"sessions": []})
    assert "No sessions found" in s._crew_list_sessions("chick", 10)


def test_no_readable_messages_names_the_key_and_the_crew(get):
    get({"messages": []})
    out = s._crew_get_session("chick", "dashboard:7", 50)
    assert "No readable messages" in out and "dashboard:7" in out


def test_a_missing_sessions_field_is_treated_as_empty_not_as_an_error(get):
    """``resp.get("sessions") or []`` — an absent field is empty, not a failure."""
    get({})
    assert "No sessions found" in s._crew_list_sessions("chick", 10)


# ── search formatting ────────────────────────────────────────────────────────


def test_search_renders_title_key_date_and_snippet_for_each_row(get):
    get(
        {
            "sessions": [
                {
                    "key": "dashboard:1",
                    "title": "Deploy plan",
                    "date": "2026-09-16",
                    "snippet": "we agreed to ship",
                }
            ]
        }
    )
    out = s._crew_search_history("chick", "ship", 10)
    assert "Deploy plan" in out
    assert "dashboard:1" in out
    assert "2026-09-16" in out
    assert "we agreed to ship" in out


def test_search_falls_back_to_the_key_when_a_row_carries_no_title(get):
    get({"sessions": [{"key": "dashboard:2"}]})
    assert "dashboard:2" in s._crew_search_history("chick", "x", 10)


def test_search_omits_the_optional_date_and_snippet_lines_when_absent(get):
    get({"sessions": [{"key": "dashboard:3", "title": "Bare"}]})
    out = s._crew_search_history("chick", "x", 10)
    assert "Bare" in out
    assert "_None_" not in out and "None" not in out.replace("dashboard:3", "")


def test_search_points_the_reader_at_get_chat_session_for_the_full_thread(get):
    get({"sessions": [{"key": "dashboard:1", "title": "T"}]})
    out = s._crew_search_history("chick", "x", 10)
    assert "get_chat_session" in out and "snippets only" in out


# ── list formatting ──────────────────────────────────────────────────────────


def test_list_renders_every_metadata_bit_when_a_row_carries_them_all(get):
    get(
        {
            "sessions": [
                {
                    "key": "dashboard:1",
                    "title": "Rollout",
                    "agent": "kirocrew",
                    "messages": 12,
                    "created": "2026-09-16T10:00:00+00:00",
                    "preview": "first line",
                }
            ]
        }
    )
    out = s._crew_list_sessions("chick", 10)
    assert "Rollout" in out
    assert "agent=kirocrew" in out
    assert "~12 msgs" in out
    assert "2026-09-16T10:00" in out
    assert "first line" in out


def test_list_truncates_the_created_stamp_to_minutes(get):
    get({"sessions": [{"key": "k", "created": "2026-09-16T10:00:00.123456+00:00"}]})
    out = s._crew_list_sessions("chick", 10)
    assert "2026-09-16T10:00" in out
    assert "123456" not in out


def test_list_renders_a_zero_message_count_rather_than_dropping_it(get):
    """``is not None``, not truthiness — a real empty session reports ~0 msgs."""
    get({"sessions": [{"key": "k", "messages": 0}]})
    assert "~0 msgs" in s._crew_list_sessions("chick", 10)


def test_list_emits_no_metadata_line_when_a_row_has_none_of_the_optional_fields(get):
    get({"sessions": [{"key": "bare-key"}]})
    out = s._crew_list_sessions("chick", 10)
    assert "bare-key" in out
    assert "agent=" not in out and "msgs" not in out


def test_list_reports_the_row_count_in_the_header(get):
    get({"sessions": [{"key": "a"}, {"key": "b"}, {"key": "c"}]})
    assert "(3, newest first)" in s._crew_list_sessions("chick", 10)


# ── transcript formatting ────────────────────────────────────────────────────


def test_read_titles_each_role_and_keeps_message_order(get):
    get(
        {
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
            ]
        }
    )
    out = s._crew_get_session("chick", "dashboard:1", 50)
    assert "**User:** first" in out
    assert "**Assistant:** second" in out
    assert out.index("first") < out.index("second")


def test_read_falls_back_to_a_placeholder_role_and_empty_content(get):
    get({"messages": [{}]})
    out = s._crew_get_session("chick", "dashboard:1", 50)
    assert "**?:**" in out


def test_read_names_the_key_and_crew_in_the_header(get):
    get({"messages": [{"role": "user", "content": "hi"}]})
    out = s._crew_get_session("chick", "dashboard:9", 50)
    assert "dashboard:9" in out and "chick" in out


# ── the private-memory fence ─────────────────────────────────────────────────


def test_a_session_fenced_to_a_private_store_is_refused_a_crew_read():
    """A non-empty scope is a Crew Member bound to a private V2 store."""
    refusal = s._crew_scope_refusal("member-store-x")
    assert refusal
    assert "Access denied" in refusal


def test_an_ordinary_session_is_not_refused():
    """``mcp_memory_scope`` answers ``""`` for an ordinary session."""
    assert s._crew_scope_refusal("") == ""
    assert s._crew_scope_refusal(None) == ""
