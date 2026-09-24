"""
Step 3: Reusable dependency that reads the JWT token from a request
and returns the logged-in User, or rejects the request if invalid.

Any endpoint that needs to know "who is asking" imports get_current_user
from this file and adds it as a dependency.
"""

from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from .db import SessionLocal, User
from .auth_utils import decode_access_token


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    token = authorization.split(" ", 1)[1]
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")

    return user


def require_employee(current_user: User = Depends(get_current_user)) -> User:
    if getattr(current_user, "role", None) != "employee":
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Employee access required. Customers are not authorized for internal tools."
        )
    return current_user

