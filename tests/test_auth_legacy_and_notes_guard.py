"""Fail-closed authentication on the Vercel origin.

Two fixes, one theme — a credential that cannot be verified is refused, never
waved through:

  F01  api.index._handle_auth_legacy used to fall through to an unconditional
       grant, so ANY non-empty password returned a real `authenticated`-role
       Supabase JWT. Configuring a hash did not help: a NON-MATCHING hash fell
       through to the same grant.
  R-01 api.handlers_admin._handle_admin_notes_queue had no auth guard at all
       and returned every pending note row, including `uploader_phone` and the
       private `storage_path`.

The assertion that matters throughout is that _mint_supabase_jwt is NEVER
reached on a refusal — the JWT is the thing of value, not the 200.

See docs/reviews/2026-09-17-four-role-architecture-review.md S3.1 and R-01.
"""
import hashlib
import json

import pytest

import api.index as api_mod
import api.handlers_admin as ha  # noqa: E402  (api.index first: the two are circular)


class _H:
    """Minimal stand-in for the BaseHTTPRequestHandler the handlers receive."""

    def __init__(self, headers=None, path="/auth"):
        self.status = None
        self.payload = None
        self.headers = headers or {}
        self.path = path


@pytest.fixture
def auth(monkeypatch):
    """Capture responses; make a minted JWT loudly detectable."""
    minted = []

    def _mint():
        minted.append(1)
        return "JWT-SHOULD-NOT-BE-ISSUED"

    monkeypatch.setattr(api_mod, "_mint_supabase_jwt", _mint)
    monkeypatch.setattr(
        api_mod, "_json_response",
        lambda h, s, p: (setattr(h, "status", s), setattr(h, "payload", p)),
    )
    for var in ("STAFF_TOKEN_HASH", "STORE_SHA256_HASH", "SUPERADMIN_SHA256_HASH",
                "MIS_SHA256_HASH", "ADMIN_PBKDF2_HASH", "ADMIN_PBKDF2_SALT"):
        monkeypatch.delenv(var, raising=False)
    return minted


def _post(body: dict) -> _H:
    h = _H()
    api_mod._handle_auth_legacy(h, json.dumps(body).encode())
    return h


def _denied(h, minted):
    assert h.status == 401, f"expected 401, got {h.status}: {h.payload}"
    assert h.payload.get("ok") is False
    assert h.payload.get("supabase_jwt") is None
    assert minted == [], "a refusal must never mint a Supabase JWT"


# ── F01 ──────────────────────────────────────────────────────────────────────

class TestAuthLegacyFailsClosed:

    def test_arbitrary_password_is_refused(self, auth):
        """The bypass itself: no hash configured, junk password."""
        _denied(_post({"type": "store", "password": "literally anything"}), auth)

    def test_non_matching_hash_is_refused(self, auth, monkeypatch):
        """The subtle half of F01 — a CONFIGURED but non-matching hash used to
        fall through to the grant on the next line."""
        monkeypatch.setenv("STORE_SHA256_HASH", hashlib.sha256(b"correct").hexdigest())
        _denied(_post({"type": "store", "password": "wrong"}), auth)

    def test_missing_type_is_refused(self, auth, monkeypatch):
        """A password with no type cannot be checked against anything."""
        monkeypatch.setenv("STORE_SHA256_HASH", hashlib.sha256(b"correct").hexdigest())
        _denied(_post({"password": "correct"}), auth)

    def test_unknown_type_is_refused(self, auth, monkeypatch):
        monkeypatch.setenv("STORE_SHA256_HASH", hashlib.sha256(b"correct").hexdigest())
        _denied(_post({"type": "root", "password": "correct"}), auth)

    def test_empty_password_is_refused(self, auth):
        _denied(_post({"type": "store", "password": ""}), auth)
        _denied(_post({}), auth)

    def test_type_cannot_borrow_another_types_hash(self, auth, monkeypatch):
        """A valid STORE password must not authenticate as mis/superadmin.
        The old code checked one shared hash for every non-numeric password."""
        monkeypatch.setenv("STORE_SHA256_HASH", hashlib.sha256(b"storepw").hexdigest())
        _denied(_post({"type": "mis", "password": "storepw"}), auth)
        _denied(_post({"type": "superadmin", "password": "storepw"}), auth)

    @pytest.mark.parametrize("cred_type,env_key", [
        ("store",      "STORE_SHA256_HASH"),
        ("mis",        "MIS_SHA256_HASH"),
        ("superadmin", "SUPERADMIN_SHA256_HASH"),
        ("staff",      "STAFF_TOKEN_HASH"),
    ])
    def test_matching_hash_still_grants(self, auth, monkeypatch, cred_type, env_key):
        """The fix must not lock out a correctly configured console."""
        monkeypatch.setenv(env_key, hashlib.sha256(b"s3cret").hexdigest())
        h = _post({"type": cred_type, "password": "s3cret"})
        assert h.status == 200
        assert h.payload["ok"] is True
        assert h.payload["supabase_jwt"] == "JWT-SHOULD-NOT-BE-ISSUED"
        assert auth == [1]

    def test_type_map_matches_the_netlify_function(self):
        """netlify/functions/auth.js is the other half of this contract; the
        two maps drifting apart is how one origin starts refusing logins the
        other accepts."""
        assert api_mod._AUTH_TYPE_ENV == {
            "superadmin": "SUPERADMIN_SHA256_HASH",
            "store":      "STORE_SHA256_HASH",
            "mis":        "MIS_SHA256_HASH",
            "staff":      "STAFF_TOKEN_HASH",
        }
        assert api_mod._ADMIN_PBKDF2_ITER == 600_000


class TestAdminPassword:

    @staticmethod
    def _configure(monkeypatch, password: str) -> None:
        salt = b"\x01\x02\x03\x04"
        monkeypatch.setenv("ADMIN_PBKDF2_SALT", salt.hex())
        monkeypatch.setenv("ADMIN_PBKDF2_HASH", hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, 600_000, 32).hex())

    def test_correct_admin_password_grants(self, auth, monkeypatch):
        self._configure(monkeypatch, "hunter2")
        h = _post({"type": "admin", "password": "hunter2"})
        assert h.status == 200 and h.payload["ok"] is True
        assert auth == [1]

    def test_wrong_admin_password_is_refused(self, auth, monkeypatch):
        self._configure(monkeypatch, "hunter2")
        _denied(_post({"type": "admin", "password": "hunter3"}), auth)

    def test_unconfigured_admin_is_refused(self, auth):
        """No ADMIN_PBKDF2_* on this deployment → refuse, don't wave through."""
        _denied(_post({"type": "admin", "password": "anything"}), auth)

    def test_malformed_salt_is_refused(self, auth, monkeypatch):
        monkeypatch.setenv("ADMIN_PBKDF2_HASH", "ab" * 32)
        monkeypatch.setenv("ADMIN_PBKDF2_SALT", "not-hex")
        _denied(_post({"type": "admin", "password": "anything"}), auth)


class TestStaffPin:

    def test_valid_pin_grants(self, auth, monkeypatch):
        import db_cloud
        row = {"id": "S1", "name": "Anu", "pin_hash": "h", "pin_salt": None}
        monkeypatch.setattr(db_cloud, "_client", lambda: _FakeStaffTable([row]))
        monkeypatch.setattr(api_mod, "_verify_pin", lambda pin, hsh, salt: pin == "1234")
        h = _post({"pin": "1234"})
        assert h.status == 200
        assert h.payload["staff_id"] == "S1" and h.payload["name"] == "Anu"

    def test_wrong_pin_is_refused(self, auth, monkeypatch):
        import db_cloud
        row = {"id": "S1", "name": "Anu", "pin_hash": "h", "pin_salt": None}
        monkeypatch.setattr(db_cloud, "_client", lambda: _FakeStaffTable([row]))
        monkeypatch.setattr(api_mod, "_verify_pin", lambda pin, hsh, salt: pin == "1234")
        _denied(_post({"pin": "9999"}), auth)

    def test_lookup_failure_is_refused_not_granted(self, auth, monkeypatch):
        """A staff lookup that cannot run is not a lookup that passed. The old
        code logged the error and fell through to the unconditional grant."""
        import db_cloud

        def _boom():
            raise RuntimeError("supabase unreachable")

        monkeypatch.setattr(db_cloud, "_client", _boom)
        _denied(_post({"pin": "1234"}), auth)


class _FakeStaffTable:
    """Chainable stub for _client().table(...).select(...).eq(...).execute()."""

    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return self

    def select(self, *_a, **_kw):
        return self

    def eq(self, *_a, **_kw):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


# ── R-01 ─────────────────────────────────────────────────────────────────────

class TestNotesQueueGuard:

    def test_unauthenticated_request_is_refused_without_touching_the_db(self, monkeypatch):
        called = []
        import db_cloud
        monkeypatch.setattr(db_cloud, "pending_notes_queue",
                            lambda limit=100: called.append(1) or [])
        monkeypatch.setattr(ha, "_auth_admin_pw", lambda pw: False)
        monkeypatch.setattr(ha, "_json_response",
                            lambda h, s, p: (setattr(h, "status", s), setattr(h, "payload", p)))

        h = _H(path="/admin/notes-queue")
        ha._handle_admin_notes_queue(h)

        assert h.status == 403
        assert "notes" not in (h.payload or {})
        assert called == [], "the query must not run for an unauthenticated caller"

    def test_authenticated_request_still_lists_notes(self, monkeypatch):
        import db_cloud
        rows = [{"note_code": "NOTE-1", "uploader_phone": "919", "status": "pending"}]
        monkeypatch.setattr(db_cloud, "pending_notes_queue", lambda limit=100: rows)
        monkeypatch.setattr(ha, "_auth_admin_pw", lambda pw: True)
        monkeypatch.setattr(ha, "_admin_pw_from_request", lambda h: "pw")
        monkeypatch.setattr(ha, "_json_response",
                            lambda h, s, p: (setattr(h, "status", s), setattr(h, "payload", p)))

        h = _H(path="/admin/notes-queue")
        ha._handle_admin_notes_queue(h)

        assert h.status == 200
        assert h.payload["count"] == 1
        assert h.payload["notes"] == rows
