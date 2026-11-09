from __future__ import annotations

import asyncio

from app.browser.action_broker import ActionBroker


def test_duplicate_action_has_one_id_and_one_execution():
    broker = ActionBroker("memory")
    first = asyncio.run(
        broker.create_action("run-1", "search", {"site": "jd", "query": "x"})
    )
    second = asyncio.run(
        broker.create_action("run-1", "search", {"site": "jd", "query": "x"})
    )

    assert first.action_id == second.action_id
    pending = asyncio.run(broker.next_action("run-1"))
    assert pending is not None
    assert pending.action_id == first.action_id


def test_observation_is_idempotent_and_completes_action():
    broker = ActionBroker("memory")
    action = asyncio.run(
        broker.create_action("run-1", "search", {"site": "jd", "query": "x"})
    )

    first = asyncio.run(
        broker.submit_observation(
            action.action_id, {"status": "ok", "items": [1]}
        )
    )
    second = asyncio.run(
        broker.submit_observation(
            action.action_id, {"status": "blocked", "items": []}
        )
    )

    assert first == second
    assert first["status"] == "ok"
    completed = asyncio.run(broker.get_action(action.action_id))
    assert completed.status == "done"
    assert asyncio.run(broker.next_action("run-1")) is None


def test_different_actions_have_different_ids():
    broker = ActionBroker("memory")
    first = asyncio.run(
        broker.create_action("run-1", "search", {"site": "jd", "query": "a"})
    )
    second = asyncio.run(
        broker.create_action("run-1", "search", {"site": "jd", "query": "b"})
    )
    assert first.action_id != second.action_id
