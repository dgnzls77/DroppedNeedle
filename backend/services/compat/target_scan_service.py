"""Post-cutover Subsonic scan projection staged without runtime registration."""

from __future__ import annotations

import asyncio

from models.library_work import ScanRequest, ScanScope
from services.native.library_policy_resolver import LibraryPolicyResolver
from services.native.library_scan_coordinator import LibraryScanCoordinator


class TargetCompatScanService:
    def __init__(
        self,
        coordinator: LibraryScanCoordinator,
        resolver_getter,
    ) -> None:
        self._coordinator = coordinator
        self._resolver_getter = resolver_getter

    async def _request_scan(self, *, trigger: str):
        resolver: LibraryPolicyResolver = self._resolver_getter()
        return await self._coordinator.request_run(
            ScanRequest(
                kind="incremental",
                trigger=trigger,
                policy_revision=resolver.policy_revision,
                scopes=[
                    ScanScope(
                        root_id=root.id,
                        relative_path=".",
                        effective_policy=root.policy,
                        policy_revision=resolver.policy_revision,
                    )
                    for root in resolver.settings.library_roots
                ],
            )
        )

    async def start_scan(self) -> None:
        await self._request_scan(trigger="subsonic")

    async def scan(self, _library_paths) -> None:  # noqa: ANN001
        """Refresh the target catalog and wait until the accepted run is terminal.

        A request covered by an already-active run is not sufficient for files that
        landed after that run's discovery phase. Wait for that run, then request one
        fresh pass so Tidarr completion is verified against the catalog the target UI
        and download orchestrator actually read.
        """
        terminal = {
            "completed",
            "cancelled",
            "superseded_policy_changed",
            "failed",
        }
        while True:
            result = await self._request_scan(trigger="automatic")
            if result.disposition == "conflict":
                while await self._coordinator.current():
                    await asyncio.sleep(1.0)
                continue

            snapshot = await self._coordinator.snapshot(result.run_id)
            covered_by_active = (
                result.disposition == "coalesced"
                and snapshot.run.state != "queued"
            )
            while snapshot.run.state not in terminal:
                await asyncio.sleep(1.0)
                snapshot = await self._coordinator.snapshot(result.run_id)

            if snapshot.run.state != "completed":
                raise RuntimeError(
                    f"Target library scan ended in state {snapshot.run.state}"
                )
            if covered_by_active:
                continue
            return

    async def is_running(self) -> bool:
        return bool(await self._coordinator.current())

    async def start(self) -> None:
        await self.start_scan()

    async def get_status(self) -> tuple[bool, int]:
        runs = await self._coordinator.current()
        active = next((run for run in runs if run.state != "queued"), None)
        if active is None:
            return False, 0
        snapshot = await self._coordinator.snapshot(active.id)
        return True, snapshot.counters.get("inspected_count", 0)

    async def status(self) -> tuple[bool, int]:
        return await self.get_status()
