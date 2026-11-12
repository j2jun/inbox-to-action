"""In-memory task store with the same interface as NotionStore (used by eval, tests and local dev)."""
import itertools
from dataclasses import replace

from .models import OPEN, Task


class MemoryStore:
    def __init__(self):
        self.pages: dict[str, Task] = {}
        self._ids = itertools.count(1)

    def find(self, key: str) -> Task | None:
        return next((replace(t) for t in self.pages.values() if t.key == key), None)

    def has_prefix(self, prefix: str) -> bool:
        return any(t.key.startswith(prefix) for t in self.pages.values())

    def create(self, task: Task) -> Task:
        task = replace(task, page_id=f"mem-{next(self._ids)}")
        self.pages[task.page_id] = task
        return replace(task)

    def update(self, page_id: str, **fields) -> Task:
        if page_id not in self.pages:
            raise KeyError(page_id)
        self.pages[page_id] = replace(self.pages[page_id], **fields)
        return replace(self.pages[page_id])

    def list_open(self) -> list[Task]:
        def sort_key(t):
            d = t.due
            return (d is None, str(d.date() if hasattr(d, "date") else d) if d else "", str(d) if d else "")
        return sorted((replace(t) for t in self.pages.values() if t.status in OPEN), key=sort_key)
