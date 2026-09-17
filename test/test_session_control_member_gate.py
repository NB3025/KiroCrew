"""The private-member gate in front of the session-control HTTP routes.

The bug: every ``memory_version: 2`` crew-member DM slot got a blanket 403
``member_scope_denied`` from ALL FIVE session-control routes, because
``_require_internal`` refused any verified V2 caller before the request ever
reached ``session_control.py``. That made the member operating model — a DM
thread dispatching workers it creates and patrols — unreachable, even though
``session_control.py`` already has the full member fence (``_member_bypass``,
``authorize_target``'s ``not_creator``, ``create_session``'s workspace check).

These tests pin the NEW seam, ``_private_caller_refusal``:

* a crew-member DM slot (``member-*`` session key) under ``member_dispatch`` is
  admitted (the gate returns ``None`` and the real handler runs);
* every OTHER verified V2 caller keeps the ``member_scope_denied`` refusal — an
  ordinary private member, and a member while the operator ceiling is off;
* an owner / Global-V1 caller (no private scope) falls through as before;
* an unverifiable caller keeps the ``member_session_unverified`` refusal.

The gate keys on ``sc.member_dispatch_enabled()``, the same ceiling
``session_control.py`` reads, so it can never open wider than the switch it
stands in front of. ``internal_memory_scope`` is stubbed here to isolate the
gate's decision from the member-proof plumbing that is exercised elsewhere.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from kiro_crew.dashboard import session_control as sc
from kiro_crew.dashboard.handlers import session_control as handler

MEMBER_SESSION = "dashboard:member-radar"
ORDINARY_PRIVATE_SESSION = "dashboard:alice"


def _internal_request(session_key: str) -> web.Request:
    """A strict-internal request carrying *session_key*, as the middleware marks it."""
    app = web.Application()
    request = make_mocked_request(
        "POST", "/api/session-control/create", app=app, headers={"X-Session-Key": session_key}
    )
    request["internal_auth"] = True
    return request


def _stub_scope(monkeypatch, *, scope, refusal=None):
    """Pin ``internal_memory_scope`` (as imported into the handler module)."""

    async def _scope(_request, _operation, **_kwargs):
        return scope, refusal

    monkeypatch.setattr(handler, "internal_memory_scope", _scope)


class TestPrivateCallerGate:
    @pytest.mark.asyncio
    async def test_member_under_ceiling_is_admitted(self, monkeypatch):
        _stub_scope(monkeypatch, scope="member-radar")
        monkeypatch.setattr(sc, "member_dispatch_enabled", lambda: True)
        monkeypatch.setattr(sc, "session_control_enabled", lambda: False)
        refusal = await handler._private_caller_refusal(_internal_request(MEMBER_SESSION))
        assert refusal is None

    @pytest.mark.asyncio
    async def test_member_admitted_under_switch_when_bypass_is_off(self, monkeypatch):
        # member_dispatch off does NOT eject a member from the surface: its own
        # bypass is withdrawn, so it falls back UNDER the global switch, and the
        # switch being on admits it exactly like any ordinary caller. Refusing it
        # here would put a member outside a surface the operator left open to all.
        _stub_scope(monkeypatch, scope="member-radar")
        monkeypatch.setattr(sc, "member_dispatch_enabled", lambda: False)
        monkeypatch.setattr(sc, "session_control_enabled", lambda: True)
        refusal = await handler._private_caller_refusal(_internal_request(MEMBER_SESSION))
        assert refusal is None

    @pytest.mark.asyncio
    async def test_member_is_refused_when_both_switches_are_off(self, monkeypatch):
        # The gate never opens wider than the two switches behind it: bypass off
        # AND the global switch off => the surface is closed for the member too.
        _stub_scope(monkeypatch, scope="member-radar")
        monkeypatch.setattr(sc, "member_dispatch_enabled", lambda: False)
        monkeypatch.setattr(sc, "session_control_enabled", lambda: False)
        refusal = await handler._private_caller_refusal(_internal_request(MEMBER_SESSION))
        assert refusal is not None
        assert refusal.status == 403
        assert json.loads(refusal.text)["code"] == "member_scope_denied"

    @pytest.mark.asyncio
    async def test_ordinary_private_caller_is_still_refused(self, monkeypatch):
        # A private V2 caller that is NOT a member DM slot keeps the refusal even
        # with both switches on — the admission is for members only.
        _stub_scope(monkeypatch, scope="member-alice")
        monkeypatch.setattr(sc, "member_dispatch_enabled", lambda: True)
        monkeypatch.setattr(sc, "session_control_enabled", lambda: True)
        refusal = await handler._private_caller_refusal(_internal_request(ORDINARY_PRIVATE_SESSION))
        assert refusal is not None
        assert json.loads(refusal.text)["code"] == "member_scope_denied"

    @pytest.mark.asyncio
    async def test_owner_or_global_caller_falls_through(self, monkeypatch):
        # No private scope => not a private surface => nothing to refuse, exactly
        # as the route behaved before member dispatch existed.
        _stub_scope(monkeypatch, scope=None)
        monkeypatch.setattr(sc, "member_dispatch_enabled", lambda: True)
        monkeypatch.setattr(sc, "session_control_enabled", lambda: True)
        refusal = await handler._private_caller_refusal(_internal_request("dashboard:owner"))
        assert refusal is None

    @pytest.mark.asyncio
    async def test_unverified_caller_keeps_its_refusal(self, monkeypatch):
        # A verification failure is returned verbatim, member key or not.
        denial = web.json_response(
            {"error": "unverified", "code": "member_session_unverified"}, status=403
        )
        _stub_scope(monkeypatch, scope=None, refusal=denial)
        monkeypatch.setattr(sc, "member_dispatch_enabled", lambda: True)
        monkeypatch.setattr(sc, "session_control_enabled", lambda: True)
        refusal = await handler._private_caller_refusal(_internal_request(MEMBER_SESSION))
        assert refusal is denial


class TestRequireInternalWiring:
    """``_require_internal`` delegates the internal-auth branch to the gate, and
    still refuses a request with no internal secret."""

    @pytest.mark.asyncio
    async def test_internal_auth_branch_uses_the_private_gate(self, monkeypatch):
        _stub_scope(monkeypatch, scope="member-radar")
        monkeypatch.setattr(sc, "member_dispatch_enabled", lambda: True)
        monkeypatch.setattr(sc, "session_control_enabled", lambda: False)
        assert await handler._require_internal(_internal_request(MEMBER_SESSION)) is None

    @pytest.mark.asyncio
    async def test_missing_internal_secret_is_still_refused(self, monkeypatch):
        # The non-internal branch is untouched by the fix.
        monkeypatch.setattr(
            handler, "sel", lambda: type("S", (), {"log_api_access": lambda *a, **k: None})()
        )
        app = web.Application()
        request = make_mocked_request("POST", "/api/session-control/create", app=app)
        refusal = await handler._require_internal(request)
        assert refusal is not None
        assert refusal.status == 403
        assert json.loads(refusal.text)["code"] == "internal_secret_required"
