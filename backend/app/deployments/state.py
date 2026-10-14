"""Ordered, durable deployment state changes."""


from app.persistence.models import DeploymentEvent, DeploymentRun

ACTIVE_STATES = {
    "queued", "validating", "retry_wait", "waiting_for_executor",
    "checking", "applying", "monitoring", "checking_health", "reconciling",
}


def transition(db, run: DeploymentRun, status: str, *, error_code: str | None = None) -> None:
    if run.status == status and run.error_code == error_code:
        return
    run.status = status
    run.error_code = error_code
    run.event_sequence += 1
    db.add(DeploymentEvent(
        deployment_run_id=run.id,
        sequence=run.event_sequence,
        kind=f"deployment.{status}",
        payload_json={"status": status, "error_code": error_code},
    ))
