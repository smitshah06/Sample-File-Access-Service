from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status

from app.api.deps import SESSION_COOKIE, Users, get_current_user
from app.api.schemas import LoginIn, MeOut
from app.domain.models import Caller

router = APIRouter(prefix="/auth")

CurrentUser = Annotated[Caller, Depends(get_current_user)]


def me_out(caller: Caller) -> MeOut:
    return MeOut(id=caller.id, username=caller.username, role=caller.role.value)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, users: Users) -> MeOut:
    caller, token = users.login(body.username, body.password)
    settings = request.app.state.settings
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,  # not readable from JavaScript, so XSS can't steal it
        secure=settings.cookie_secure,
        samesite="strict",  # not sent on cross-site requests (first CSRF defence)
        path="/",
    )
    return me_out(caller)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    users: Users,
    session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> None:
    """Always succeeds and clears the cookie, even if the session has already expired."""
    if session:
        users.logout(session)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
def me(caller: CurrentUser) -> MeOut:
    return me_out(caller)
