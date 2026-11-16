import base64
import hashlib
import hmac
import io
import os
import secrets
import time
import uuid
from pathlib import Path

import jwt
import pyotp
import qrcode
import qrcode.image.svg
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .config import settings

# ---------------------------------------------------------------- passwords

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-shape check: an unknown user costs the same as a wrong password."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (Argon2Error, InvalidHashError):
        return False


# ------------------------------------------------------------ opaque secrets


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def new_email_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ------------------------------------------------------ TOTP secrets at rest

_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest()))


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet.decrypt(value.encode()).decode()


# ---------------------------------------------------------------------- TOTP


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=settings.mfa_issuer)


def match_totp(secret: str, code: str, last_counter: int = 0) -> int | None:
    """Return the matching time-step if `code` is valid and not already used.

    Accepts one step (30 s) of clock drift either way. The caller stores the
    returned counter so the same code cannot be replayed.
    """
    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    totp = pyotp.TOTP(secret)
    current = int(time.time()) // 30
    for counter in (current, current - 1, current + 1):
        if counter > last_counter and constant_time_equal(totp.generate_otp(counter), code):
            return counter
    return None


def qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


# ----------------------------------------------------------------------- JWT


class KeyManager:
    """RS256 signing key. Loaded from disk, generated on first start."""

    def __init__(self, path: str):
        key_path = Path(path)
        if not key_path.exists():
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            key_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(pem)
        self.private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        self.public_key = self.private_key.public_key()
        der = self.public_key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.kid = hashlib.sha256(der).hexdigest()[:16]

    def jwks(self) -> dict:
        numbers = self.public_key.public_numbers()

        def b64(n: int) -> str:
            raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        return {
            "keys": [
                {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid, "n": b64(numbers.n), "e": b64(numbers.e)}
            ]
        }

    def sign(self, claims: dict) -> str:
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": self.kid})

    def verify(self, token: str) -> dict:
        """Raises jwt.PyJWTError on any problem (signature, expiry, issuer, audience)."""
        return jwt.decode(
            token,
            self.public_key,
            algorithms=["RS256"],
            audience=settings.audience,
            issuer=settings.issuer,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )


keys = KeyManager(settings.jwt_private_key_path)


def mint_access_token(*, sub: str, typ: str, client_id: str, ttl: int, extra: dict | None = None) -> str:
    now = int(time.time())
    claims = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": sub,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        "typ": typ,  # "user" | "service"
        "client_id": client_id,
        **(extra or {}),
    }
    return keys.sign(claims)
