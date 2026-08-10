"""Unit tests for the Google sign-in layer (src/gauth.py).

The allowlist and the claim checks are the entire security boundary in front of
a carrier credential that can port numbers away, so they are tested directly
rather than only through the gateway's happy path.
"""

import base64
import json
import time

import pytest

import gauth

AUD = "google-client-id.apps.googleusercontent.com"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _id_token(**claims) -> str:
    payload = {
        "iss": "https://accounts.google.com",
        "aud": AUD,
        "exp": time.time() + 600,
        "email": "rickw@phoneware.us",
        "email_verified": True,
    }
    payload.update(claims)
    return f"{_b64u(b'{}')}.{_b64u(json.dumps(payload).encode())}.{_b64u(b'sig')}"


def _claims(**overrides) -> dict:
    return gauth.decode_id_token(_id_token(**overrides))


# ── decode ──────────────────────────────────────────────────────────────────


def test_decode_reads_claims():
    assert _claims()["email"] == "rickw@phoneware.us"


@pytest.mark.parametrize("bad", ["", "not-a-jwt", "a.b", "a.!!!.c"])
def test_decode_returns_none_for_junk(bad):
    assert gauth.decode_id_token(bad) is None


# ── claim verification ──────────────────────────────────────────────────────


def test_verify_returns_the_normalized_email():
    assert gauth.verify_claims(_claims(email="RickW@Phoneware.US "), AUD) == (
        "rickw@phoneware.us"
    )


def test_verify_accepts_googles_schemeless_issuer():
    assert gauth.verify_claims(_claims(iss="accounts.google.com"), AUD)


def test_verify_rejects_a_foreign_issuer():
    with pytest.raises(gauth.GoogleAuthError):
        gauth.verify_claims(_claims(iss="https://evil.example"), AUD)


def test_verify_rejects_a_token_minted_for_another_client():
    """A token from some other Google app is not a sign-in to this one."""
    with pytest.raises(gauth.GoogleAuthError):
        gauth.verify_claims(_claims(aud="someone-else.apps.googleusercontent.com"), AUD)


def test_verify_accepts_the_list_form_of_aud():
    assert gauth.verify_claims(_claims(aud=["other", AUD]), AUD)


def test_verify_rejects_an_expired_token():
    with pytest.raises(gauth.GoogleAuthError):
        gauth.verify_claims(_claims(exp=time.time() - 3600), AUD)


def test_verify_tolerates_small_clock_skew():
    assert gauth.verify_claims(_claims(exp=time.time() - 10), AUD)


def test_verify_rejects_an_unverified_email():
    """Google will hand out an unverified address on some account types; it is
    not proof the person controls it, so it cannot open the carrier account."""
    with pytest.raises(gauth.GoogleAuthError):
        gauth.verify_claims(_claims(email_verified=False), AUD)


def test_verify_accepts_the_string_form_of_email_verified():
    assert gauth.verify_claims(_claims(email_verified="true"), AUD)


def test_verify_rejects_a_token_with_no_email():
    with pytest.raises(gauth.GoogleAuthError):
        gauth.verify_claims(_claims(email=""), AUD)


# ── the allowlist ───────────────────────────────────────────────────────────


def test_domain_match_allows_the_workspace():
    assert gauth.is_email_allowed("rickw@phoneware.us", ["phoneware.us"])


def test_domain_match_is_case_insensitive():
    assert gauth.is_email_allowed("RickW@PHONEWARE.US", ["Phoneware.us"])


def test_an_outside_domain_is_refused():
    assert not gauth.is_email_allowed("someone@gmail.com", ["phoneware.us"])


def test_an_explicit_address_wins_without_its_domain():
    assert gauth.is_email_allowed(
        "contractor@example.com", ["phoneware.us"], ["contractor@example.com"]
    )


def test_empty_allowlists_allow_nobody():
    """An unconfigured allowlist means nobody, never everybody. serve.py
    refuses to boot in this state; this is the belt to that braces."""
    assert not gauth.is_email_allowed("rickw@phoneware.us", [], [])


def test_a_lookalike_domain_is_refused():
    """Substring matching here would let notphoneware.us straight in."""
    assert not gauth.is_email_allowed("attacker@notphoneware.us", ["phoneware.us"])


def test_a_subdomain_is_not_the_domain():
    assert not gauth.is_email_allowed("attacker@evil.phoneware.us", ["phoneware.us"])


def test_an_address_with_no_at_sign_is_refused():
    assert not gauth.is_email_allowed("phoneware.us", ["phoneware.us"])


def test_only_the_last_at_counts_as_the_domain():
    """`a@b@c` parses to domain `c`, so a crafted local part cannot smuggle
    the allowed domain past the check."""
    assert not gauth.is_email_allowed("user@phoneware.us@evil.example", ["phoneware.us"])


def test_blank_entries_in_the_allowlist_match_nothing():
    assert not gauth.is_email_allowed("someone@gmail.com", ["", "  "], ["", "  "])


# ── the sign-in URL ─────────────────────────────────────────────────────────


def test_auth_url_carries_our_callback_and_state():
    url = gauth.auth_url(AUD, "https://mcp.gateway.test/auth/google/callback", "signed-state")
    assert url.startswith(gauth.GOOGLE_AUTH_URL + "?")
    assert "state=signed-state" in url
    assert "scope=openid+email+profile" in url
    assert (
        "redirect_uri=https%3A%2F%2Fmcp.gateway.test%2Fauth%2Fgoogle%2Fcallback" in url
    )
