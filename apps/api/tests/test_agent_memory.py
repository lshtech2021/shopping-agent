from __future__ import annotations

from pathlib import Path

from app.agent.memory import Memory


class MappingMemory:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.saved: list[tuple[str, dict]] = []

    async def load(self, run_id: str) -> dict:
        return dict(self.rows.get(run_id, {}))

    async def save(self, run_id: str, patch: dict) -> None:
        self.saved.append((run_id, patch))
        self.rows.setdefault(run_id, {}).update(patch)

    async def query(self, query: str, *, limit: int) -> list[dict]:
        return [
            row
            for row in self.rows.values()
            if query.lower() in str(row).lower()
        ][:limit]

    async def summarize(self, run_id: str) -> str:
        return str(self.rows.get(run_id, {}))


def test_generic_memory_protocol_works_with_a_mapping_adapter():
    import asyncio

    memory: Memory = MappingMemory()
    asyncio.run(memory.save("run-1", {"step": 2}))
    asyncio.run(memory.save("run-1", {"note": "ready"}))
    loaded = asyncio.run(memory.load("run-1"))

    assert loaded == {"step": 2, "note": "ready"}
    assert asyncio.run(memory.query("ready", limit=1)) == [loaded]
    assert asyncio.run(memory.summarize("run-1"))


def test_generic_memory_module_has_no_business_symbols():
    path = Path(__file__).resolve().parents[1] / "app" / "agent" / "memory.py"
    source = path.read_text(encoding="utf-8").lower()
    for symbol in ("requirements", "inventory", "product", "shopping"):
        assert symbol not in source
