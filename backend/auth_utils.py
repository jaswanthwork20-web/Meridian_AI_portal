"""
Step 2a: Authentication utilities - password hashing and JWT tokens.

Install:
    python -m pip install "passlib[bcrypt]" python-jose[cryptography]

These are just helper functions - Step 2b (user_api.py) wires them
into actual signup/login endpoints.
"""

from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta

# ------------------ CONFIG ------------------
# In a real deployment, load this from an environment variable, never hardcode it.
SECRET_KEY = "change-this-to-a-long-random-string-before-any-real-use"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day, fine for a POC
# ---------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str):
    """Returns the payload dict if valid, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
