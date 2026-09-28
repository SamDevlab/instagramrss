from __future__ import annotations

import os
from threading import Event, Thread

from instagram.service import SourceService


class StoryScheduler:
    def __init__(self, service: SourceService, interval_minutes: int | None = None) -> None:
        self.service = service
        self.interval_minutes = interval_minutes or int(os.getenv("STORY_REFRESH_MINUTES", "15"))
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="story-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> list[dict]:
        results: list[dict] = []
        for source in self.service.list_sources():
            results.append(self.service.refresh_source(source["source_id"]))
        self.service.mark_stale_sources()
        return results

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(max(60, self.interval_minutes * 60))
