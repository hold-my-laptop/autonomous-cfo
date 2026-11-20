from datetime import UTC, datetime

from fastapi import APIRouter, Request, status

from api.commands import activate_mandate, create_mandate_draft, set_paused
from api.deps import RepositoryDep
from api.idempotency import (
    IdempotencyKeyDep,
    check_mandate_version,
    request_hash,
    run_idempotent,
)
from api.schemas import Mandate, MandateActivateIn, MandateIn

router = APIRouter(prefix="/mandate", tags=["mandate"])


@router.get("")
def get_mandate(repository: RepositoryDep) -> Mandate:
    with repository.read() as state:
        return Mandate.from_domain(state.active_mandate)


@router.get("/versions")
def list_mandate_versions(repository: RepositoryDep) -> list[Mandate]:
    """All versions, newest first."""
    with repository.read() as state:
        return [
            Mandate.from_domain(m)
            for m in sorted(state.mandates, key=lambda m: m.version, reverse=True)
        ]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_mandate(
    body: MandateIn, key: IdempotencyKeyDep, repository: RepositoryDep, request: Request
) -> Mandate:
    """Save new rules as a DRAFT version. Nothing changes until it is activated."""
    with repository.write() as state:
        return run_idempotent(
            state,
            key,
            request_hash("POST", request.url.path, body),
            lambda: Mandate.from_domain(
                create_mandate_draft(
                    state,
                    cash_floor=body.cash_floor,
                    act_alone_enabled=body.act_alone_enabled,
                    max_delay_days=body.max_delay_days,
                    max_amount_per_action=body.max_amount_per_action,
                    max_amount_per_day=body.max_amount_per_day,
                    protected_categories=frozenset(body.protected_categories),
                    credit_enabled=body.credit_enabled,
                    max_credit_amount=body.max_credit_amount,
                    note=body.note,
                    now=datetime.now(UTC),
                )
            ),
        )


@router.post("/{version}/activate")
def activate_mandate_version(
    version: int,
    body: MandateActivateIn,
    key: IdempotencyKeyDep,
    repository: RepositoryDep,
    request: Request,
) -> Mandate:
    with repository.write() as state:

        def apply() -> Mandate:
            check_mandate_version(state, body.expected_active_version)
            return Mandate.from_domain(activate_mandate(state, version, datetime.now(UTC)))

        return run_idempotent(state, key, request_hash("POST", request.url.path, body), apply)


@router.post("/pause")
def pause_mandate(key: IdempotencyKeyDep, repository: RepositoryDep, request: Request) -> Mandate:
    """While paused, everything Vero proposes needs the owner's approval."""
    with repository.write() as state:
        return run_idempotent(
            state,
            key,
            request_hash("POST", request.url.path, None),
            lambda: Mandate.from_domain(set_paused(state, True)),
        )


@router.post("/resume")
def resume_mandate(key: IdempotencyKeyDep, repository: RepositoryDep, request: Request) -> Mandate:
    with repository.write() as state:
        return run_idempotent(
            state,
            key,
            request_hash("POST", request.url.path, None),
            lambda: Mandate.from_domain(set_paused(state, False)),
        )
