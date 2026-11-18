"""Report-by-exception: decide which values are worth publishing."""

from __future__ import annotations

from .tags import Tag, Value


class ChangeDetector:
    """Keeps the last *published* value of every tag.

    Comparing against the last published value (not the last read) means a slow drift
    still gets reported once it accumulates past the deadband.
    """

    def __init__(self, tags: list[Tag]) -> None:
        self._deadband = {t.name: t.deadband for t in tags}
        self._published: dict[str, Value] = {}

    def changes(self, values: dict[str, Value]) -> dict[str, Value]:
        changed = {name: v for name, v in values.items() if self._is_change(name, v)}
        self._published.update(changed)
        return changed

    def snapshot(self, values: dict[str, Value]) -> dict[str, Value]:
        """Mark every value as published (used for heartbeats and after reconnecting)."""
        self._published.update(values)
        return dict(values)

    def reset(self) -> None:
        self._published.clear()

    def _is_change(self, name: str, value: Value) -> bool:
        if name not in self._published:
            return True
        last = self._published[name]
        deadband = self._deadband.get(name, 0.0)
        if deadband and isinstance(value, float) and isinstance(last, (int, float)):
            return abs(value - last) >= deadband
        return value != last
