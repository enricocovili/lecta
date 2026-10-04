"""Passkeys: registering one in the settings and signing in with it (a software authenticator stands in for the browser)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app.security.auth import login_global_limiter, login_limiter

from .conftest import make_client

ORIGIN = "http://testserver"
RP_ID = "testserver"
HEADERS = {"origin": ORIGIN}


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Authenticator:
    """Just enough of a WebAuthn authenticator: ES256, no attestation, user presence and verification."""

    def __init__(self, backed_up: bool = False):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.count = 0
        self.backed_up = backed_up

    def _cose(self) -> bytes:
        n = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def create(self, options: dict, origin: str = ORIGIN, rp_id: str = RP_ID) -> dict:
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": origin, "crossOrigin": False}).encode()
        flags = 0x01 | 0x04 | 0x40 | (0x18 if self.backed_up else 0)
        data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", 0) + bytes(16) + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose()
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": data})
        return {
            "id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
            "response": {"clientDataJSON": b64u(client), "attestationObject": b64u(attestation), "transports": ["internal"]},
            "clientExtensionResults": {},
        }

    def get(self, options: dict, origin: str = ORIGIN, rp_id: str = RP_ID, verified: bool = True) -> dict:
        client = json.dumps({"type": "webauthn.get", "challenge": options["challenge"], "origin": origin, "crossOrigin": False}).encode()
        self.count += 1
        data = hashlib.sha256(rp_id.encode()).digest() + bytes([0x01 | (0x04 if verified else 0)]) + struct.pack(">I", self.count)
        sig = self.key.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
            "response": {"clientDataJSON": b64u(client), "authenticatorData": b64u(data), "signature": b64u(sig), "userHandle": b64u(b"1")},
            "clientExtensionResults": {},
        }


@pytest.fixture(autouse=True)
async def _clean():
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models import Passkey

    async def wipe() -> None:
        async with SessionLocal() as db:
            await db.execute(delete(Passkey))
            await db.commit()

    login_limiter.hits.clear()
    login_global_limiter.hits.clear()
    await wipe()
    yield
    await wipe()


async def register(admin, key: Authenticator, name: str = "Bitwarden") -> dict:
    start = await admin.post("/api/account/passkeys/options", headers=HEADERS)
    assert start.status_code == 200, start.text
    body = start.json()
    opts = body["options"]
    # A discoverable credential with user verification, for this site, without attestation.
    assert opts["rp"]["id"] == RP_ID and opts["authenticatorSelection"]["residentKey"] == "required"
    assert opts["authenticatorSelection"]["userVerification"] == "required" and opts["attestation"] == "none"
    r = await admin.post("/api/account/passkeys", headers=HEADERS, json={"state": body["state"], "credential": key.create(opts), "name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def sign_in(app, key: Authenticator, **kw):
    """A new browser signs in with the passkey: (its client, the login response). The caller closes the client."""
    c = make_client(app)
    await c.get("/api/auth/csrf")
    start = await c.post("/api/auth/passkey/options", headers=HEADERS)
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["options"]["rpId"] == RP_ID and body["options"]["userVerification"] == "required" and not body["options"].get("allowCredentials")
    r = await c.post("/api/auth/passkey/login", headers=HEADERS, json={"state": body["state"], "credential": key.get(body["options"], **kw)})
    return c, r


async def test_a_passkey_signs_in_without_password_or_code(app, admin):
    key = Authenticator(backed_up=True)
    pk = await register(admin, key)
    assert pk["name"] == "Bitwarden" and pk["synced"] is True
    assert [p["name"] for p in (await admin.get("/api/account/passkeys")).json()] == ["Bitwarden"]

    c, r = await sign_in(app, key)
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert (await c.get("/api/auth/me")).json()["authenticated"] is True
    await c.aclose()
    # Using it is recorded.
    listed = (await admin.get("/api/account/passkeys")).json()
    assert listed[0]["last_used_at"] is not None
    # A second sign-in works too (the counter moves on).
    c2, r2 = await sign_in(app, key)
    assert r2.status_code == 200
    await c2.aclose()

    # Renaming and removing; afterwards the passkey is no longer accepted.
    assert (await admin.patch(f"/api/account/passkeys/{pk['id']}", json={"name": "Telefono"})).json()["name"] == "Telefono"
    assert (await admin.delete(f"/api/account/passkeys/{pk['id']}")).status_code == 200
    c3, r3 = await sign_in(app, key)
    assert r3.status_code == 401
    await c3.aclose()


async def test_a_passkey_is_refused_when_it_does_not_fit(app, admin):
    key = Authenticator()
    await register(admin, key)

    # Another site, no user verification, an authenticator the server doesn't know, a copied (replayed) challenge.
    _, r = await sign_in(app, key, origin="http://evil.example")
    assert r.status_code == 401
    _, r = await sign_in(app, key, verified=False)
    assert r.status_code == 401
    _, r = await sign_in(app, Authenticator())
    assert r.status_code == 401
    async with make_client(app) as c:
        await c.get("/api/auth/csrf")
        start = (await c.post("/api/auth/passkey/options", headers=HEADERS)).json()
        answer = {"state": start["state"], "credential": key.get(start["options"])}
        assert (await c.post("/api/auth/passkey/login", headers=HEADERS, json=answer)).status_code == 200
        assert (await c.post("/api/auth/passkey/login", headers=HEADERS, json=answer)).status_code == 400  # one answer per challenge
        # An origin that is not the site's own can't even start.
        assert (await c.post("/api/auth/passkey/options", headers={"origin": "http://evil.example"})).status_code == 403
        assert (await c.post("/api/auth/passkey/options")).status_code == 400  # a ceremony needs the browser's origin
        # Signing in needs the double-submit CSRF token like the password does.
        assert (await c.post("/api/auth/passkey/options", headers={**HEADERS, "x-csrf-token": "x"})).status_code == 403


async def test_registering_a_passkey_is_for_the_signed_in_admin(app, admin, anon):
    assert (await anon.post("/api/account/passkeys/options", headers=HEADERS)).status_code == 404
    assert (await anon.get("/api/account/passkeys")).status_code == 404
    key = Authenticator()
    start = (await admin.post("/api/account/passkeys/options", headers=HEADERS)).json()
    good = key.create(start["options"])
    # A wrong challenge or a wrong site is not registered; the state is single use.
    other = (await admin.post("/api/account/passkeys/options", headers=HEADERS)).json()
    bad = await admin.post("/api/account/passkeys", headers=HEADERS, json={"state": other["state"], "credential": good})
    assert bad.status_code == 400
    start = (await admin.post("/api/account/passkeys/options", headers=HEADERS)).json()
    assert (await admin.post("/api/account/passkeys", headers=HEADERS, json={"state": start["state"], "credential": key.create(start["options"], origin="http://evil.example")})).status_code == 400
    start = (await admin.post("/api/account/passkeys/options", headers=HEADERS)).json()
    assert (await admin.post("/api/account/passkeys", headers=HEADERS, json={"state": start["state"], "credential": key.create(start["options"])})).status_code == 201
    # The same authenticator is not offered again (and not accepted twice).
    start = (await admin.post("/api/account/passkeys/options", headers=HEADERS)).json()
    assert start["options"]["excludeCredentials"][0]["id"] == b64u(key.cred_id)
    assert (await admin.post("/api/account/passkeys", headers=HEADERS, json={"state": start["state"], "credential": key.create(start["options"])})).status_code == 409
