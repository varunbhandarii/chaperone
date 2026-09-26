"""Agent Ed25519 key. The private key stays in keys/ and the vault. The JWK is public."""

from __future__ import annotations

import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from jwcrypto.jwk import JWK

from common.config import JWKS_PATH, KEY_ID, KEYS_DIR, PRIVATE_KEY_PATH


def load_or_create_private_key() -> Ed25519PrivateKey:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    if PRIVATE_KEY_PATH.exists():
        key = serialization.load_pem_private_key(PRIVATE_KEY_PATH.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError("agent key is not Ed25519")
        return key
    if JWKS_PATH.exists():
        raise FileNotFoundError(
            f"Missing {PRIVATE_KEY_PATH}. Copy the agent private key from the vault. "
            "Refusing to generate a new key over the published JWKS."
        )
    key = Ed25519PrivateKey.generate()
    PRIVATE_KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        PRIVATE_KEY_PATH.chmod(0o600)
    except OSError:
        pass
    return key


def public_jwk(private_key: Ed25519PrivateKey | None = None) -> dict:
    private_key = private_key or load_or_create_private_key()
    exported = JWK.from_pyca(private_key.public_key()).export_public(as_dict=True)
    exported["kid"] = KEY_ID
    exported["alg"] = "EdDSA"
    exported["use"] = "sig"
    return exported


def write_jwks(private_key: Ed25519PrivateKey | None = None) -> dict:
    document = {"keys": [public_jwk(private_key)]}
    JWKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    JWKS_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document


def load_jwks() -> dict:
    if not JWKS_PATH.exists():
        return write_jwks()
    return json.loads(JWKS_PATH.read_text(encoding="utf-8"))


def public_key_from_jwk(data: dict) -> Ed25519PublicKey:
    fields = {name: data[name] for name in ("kty", "crv", "x") if name in data}
    key = JWK.from_json(json.dumps(fields))
    return key.get_op_key("verify")


def jwks_lookup(key_id: str, document: dict | None = None) -> Ed25519PublicKey:
    document = document or load_jwks()
    for jwk in document.get("keys", []):
        if jwk.get("kid") == key_id:
            return public_key_from_jwk(jwk)
    raise KeyError(key_id)


if __name__ == "__main__":
    private = load_or_create_private_key()
    document = write_jwks(private)
    print(json.dumps(document, indent=2))
    print(f"private key: {PRIVATE_KEY_PATH} (put this in the vault, not in git)")
