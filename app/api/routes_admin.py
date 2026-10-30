"""Admin-only routes. The services enforce the admin role; these handlers only translate HTTP."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Users, get_access_service, get_current_user
from app.api.routes_samples import summary_out
from app.api.schemas import AuditEventOut, SampleSummaryOut, UserCreate, UserOut, UserUpdate
from app.domain.models import Caller, Role, User
from app.domain.service import AccessService

router = APIRouter(prefix="/admin")

CurrentUser = Annotated[Caller, Depends(get_current_user)]
Service = Annotated[AccessService, Depends(get_access_service)]


def user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        username=u.username,
        role=u.role.value,
        is_active=u.is_active,
        created_at=u.created_at,
    )


@router.get("/users")
def list_users(caller: CurrentUser, users: Users) -> list[UserOut]:
    return [user_out(u) for u in users.list_users(caller)]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, caller: CurrentUser, users: Users) -> UserOut:
    return user_out(users.create_user(caller, body.username, body.password, Role(body.role)))


@router.patch("/users/{user_id}")
def update_user(user_id: UUID, body: UserUpdate, caller: CurrentUser, users: Users) -> UserOut:
    role = Role(body.role) if body.role else None
    return user_out(users.update_user(caller, str(user_id), role, body.is_active))


@router.get("/samples")
def list_all_samples(caller: CurrentUser, service: Service) -> list[SampleSummaryOut]:
    return [summary_out(s) for s in service.list_all_samples(caller)]


@router.get("/audit")
def list_audit_events(
    caller: CurrentUser,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    event: Annotated[str | None, Query(max_length=64)] = None,
) -> list[AuditEventOut]:
    return [AuditEventOut(**e.__dict__) for e in service.list_audit_events(caller, limit, event)]
