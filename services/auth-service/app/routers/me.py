"""Self-service: profile, password, and second-factor management."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import audit, current_user, user_principal
from ..models import User
from ..schemas import ChangePassword, MeOut, OtpIn, PasswordConfirm, TotpSetupOut
from ..security import decrypt, encrypt, hash_password, match_totp, new_totp_secret, qr_svg, totp_uri, verify_password
from .oauth import revoke_all_sessions

router = APIRouter(prefix="/api/v1/me", tags=["My account"])


@router.get("", response_model=MeOut, summary="Current user, roles and effective permissions")
def me(user: User = Depends(current_user)):
    return user


@router.post("/password", status_code=204, summary="Change my password")
def change_password(body: ChangePassword, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    """Signs out every session, including this one."""
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    revoke_all_sessions(db, user)
    audit.emit(db, "user.password.changed", request=request, actor=user_principal(user),
               resource_type="user", resource_id=user.id)
    db.commit()


@router.post("/mfa/totp/setup", response_model=TotpSetupOut, summary="Start authenticator-app enrolment")
def totp_setup(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Returns a secret and QR code for Google Authenticator (or any TOTP app).

    Nothing changes until the code is confirmed with `/mfa/totp/activate`.
    """
    secret = new_totp_secret()
    user.totp_pending_secret_enc = encrypt(secret)
    uri = totp_uri(secret, user.email)
    audit.emit(db, "user.mfa.totp.setup_started", request=request, actor=user_principal(user),
               resource_type="user", resource_id=user.id)
    db.commit()
    return TotpSetupOut(secret=secret, otpauth_uri=uri, qr_svg=qr_svg(uri))


@router.post("/mfa/totp/activate", response_model=MeOut, summary="Confirm enrolment and switch to authenticator app")
def totp_activate(body: OtpIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.totp_pending_secret_enc:
        raise HTTPException(400, "Start setup first")
    secret = decrypt(user.totp_pending_secret_enc)
    counter = match_totp(secret, body.otp)
    if counter is None:
        raise HTTPException(400, "Invalid verification code")
    user.totp_secret_enc = encrypt(secret)
    user.totp_pending_secret_enc = None
    user.totp_last_counter = counter
    user.mfa_method = "totp"
    audit.emit(db, "user.mfa.method_changed", request=request, actor=user_principal(user),
               resource_type="user", resource_id=user.id, details={"method": "totp"})
    db.commit()
    return user


@router.post("/mfa/disable", response_model=MeOut, summary="Turn off my second factor")
def disable_mfa(body: PasswordConfirm, request: Request, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    """Only allowed when an administrator has made MFA optional for this account. Requires the password."""
    if user.mfa_required:
        raise HTTPException(403, "A second factor is required for your account")
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(400, "Password is incorrect")
    user.mfa_method = "none"
    user.totp_secret_enc = None
    user.totp_pending_secret_enc = None
    audit.emit(db, "user.mfa.method_changed", request=request, actor=user_principal(user),
               resource_type="user", resource_id=user.id, details={"method": "none"})
    db.commit()
    return user


@router.post("/mfa/email", response_model=MeOut, summary="Use email codes as my second factor")
def use_email_mfa(body: PasswordConfirm, request: Request, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    """Requires the password, because this replaces any authenticator app."""
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(400, "Password is incorrect")
    user.mfa_method = "email"
    user.totp_secret_enc = None
    user.totp_pending_secret_enc = None
    audit.emit(db, "user.mfa.method_changed", request=request, actor=user_principal(user),
               resource_type="user", resource_id=user.id, details={"method": "email"})
    db.commit()
    return user
