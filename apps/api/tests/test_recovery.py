from datetime import datetime, timedelta, timezone

from app.agent.supervisor import RunSupervisor
from app.store import store


def test_startup_turns_running_sessions_into_interrupted():
    session = store.create("recovery", ["jd"])
    session.status = "running"
    session.lease_owner = "dead-worker"
    store.touch(session)

    changed = RunSupervisor(store).reconcile_startup()

    assert changed == 1
    reloaded = store.get(session.id)
    assert reloaded.status == "interrupted"
    assert reloaded.interrupt_reason == "server_restart"
    assert reloaded.lease_owner is None


def test_expired_lease_is_swept_without_touching_healthy_leases():
    expired = store.create("expired", ["jd"])
    expired.status = "running"
    expired.lease_owner = "dead-worker"
    expired.lease_expires_at = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat()
    store.touch(expired)

    healthy = store.create("healthy", ["jd"])
    healthy.status = "running"
    healthy.lease_owner = "live-worker"
    healthy.lease_expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=5)
    ).isoformat()
    store.touch(healthy)

    changed = RunSupervisor(store).sweep_expired()

    assert changed == 1
    assert store.get(expired.id).status == "interrupted"
    assert store.get(expired.id).interrupt_reason == "lease_expired"
    assert store.get(healthy.id).status == "running"


def test_lease_acquire_heartbeat_and_release():
    session = store.create("lease", ["jd"])
    supervisor = RunSupervisor(store)

    owner = supervisor.acquire(session.id, ttl_seconds=60)
    assert owner
    assert supervisor.heartbeat(session.id, owner)
    supervisor.release(session.id, owner)
    assert store.get(session.id).lease_owner is None
