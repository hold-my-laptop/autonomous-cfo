from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Request, status

from api.commands import plan_closure
from api.deps import AgentDep, RepositoryDep, SettingsDep
from api.domain.models import RunStatus
from api.idempotency import IdempotencyKeyDep, check_financial_version, request_hash, run_idempotent
from api.schemas import EventIn, RunAccepted

router = APIRouter(prefix="/events", tags=["runs"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def post_event(
    body: EventIn,
    key: IdempotencyKeyDep,
    repository: RepositoryDep,
    settings: SettingsDep,
    agent: AgentDep,
    request: Request,
    background: BackgroundTasks,
) -> RunAccepted:
    """Tell Vero something changed. Starts a run; poll GET /runs/{run_id}.

    409 RUN_IN_PROGRESS while the agent is still working on an earlier run."""
    started = []
    with repository.write() as state:

        def apply() -> RunAccepted:
            check_financial_version(state, body.expected_financial_version)
            run = plan_closure(
                state,
                body.payload.date_from,
                body.payload.date_to,
                body.payload.variable_costs_stop,
                body.payload.note,
                datetime.now(UTC),
                settings.credit_line_available_until,
                settings.credit_line_rounding,
                agent=settings.agent_enabled,
            )
            if run.status is RunStatus.RUNNING:
                started.append(run.id)
            return RunAccepted(run_id=run.id, status=run.status)

        accepted = run_idempotent(state, key, request_hash("POST", request.url.path, body), apply)
    for run_id in started:
        background.add_task(agent.start, run_id)
    return accepted
