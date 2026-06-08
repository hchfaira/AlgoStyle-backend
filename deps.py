"""
FastAPI dependency — authenticated current user.

Usage in any route file:
    from deps import get_current_user, CurrentUser

    @router.get("/items")
    async def list_items(current_user: CurrentUser):
        user_id = current_user["user_id"]
        ...

The dependency reads the Bearer token from the Authorization header,
validates it against the DB (expiry included), and returns the user dict.
A 401 is raised automatically for missing/invalid/expired tokens.
"""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services import auth_service

_bearer = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> dict:
    """
    Validate the Bearer token and return the authenticated user dict.

    Raises HTTP 401 when the token is missing, invalid, or expired.
    """
    token = credentials.credentials
    try:
        user = auth_service.get_user_by_token(token)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# Convenience type alias — use this in route signatures
CurrentUser = Annotated[dict, Depends(get_current_user)]
