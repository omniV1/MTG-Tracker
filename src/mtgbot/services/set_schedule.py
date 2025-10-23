"""Set schedule syncing and milestone evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, List, Sequence, Tuple

from mtgbot.models import MagicSet, SetAlert, SetMilestone
from mtgbot.storage.sets import SetRepository, SetState


@dataclass(slots=True)
class UpcomingSet:
    magic_set: MagicSet
    days_until_release: int


class SetScheduleService:
    def __init__(self, repository: SetRepository) -> None:
        self._repository = repository

    async def initialize(self) -> None:
        await self._repository.init()

    async def sync_sets(self, sets: Iterable[MagicSet]) -> None:
        for magic_set in sets:
            await self._repository.upsert_set(magic_set)

    async def pending_alerts(self, today: date | None = None) -> List[SetAlert]:
        today = today or date.today()
        states = await self._repository.list_sets()
        alerts: List[SetAlert] = []
        for state in states:
            alerts.extend(self._alerts_for_state(state, today))
        return alerts

    async def mark_alert_sent(self, alert: SetAlert) -> None:
        await self._repository.mark_notified(
            alert.magic_set.set_id, alert.milestone
        )

    async def upcoming_sets(
        self, *, within_days: int = 60, today: date | None = None
    ) -> List[UpcomingSet]:
        today = today or date.today()
        states = await self._repository.list_sets()
        upcoming: List[UpcomingSet] = []
        for state in states:
            release = state.magic_set.released_at
            if release is None:
                continue
            delta = (release - today).days
            if delta < 0 or delta > within_days:
                continue
            upcoming.append(
                UpcomingSet(
                    magic_set=state.magic_set,
                    days_until_release=delta,
                )
            )
        upcoming.sort(key=lambda item: (item.days_until_release, item.magic_set.name))
        return upcoming

    def _alerts_for_state(self, state: SetState, today: date) -> List[SetAlert]:
        alerts: List[SetAlert] = []
        magic_set = state.magic_set
        release_at = magic_set.released_at

        is_future_or_unknown = (
            release_at is None or release_at >= today
        )
        if not state.notified_announcement and is_future_or_unknown:
            alerts.append(
                SetAlert(
                    magic_set=magic_set,
                    milestone=SetMilestone.ANNOUNCEMENT,
                    scheduled_for=today,
                    message=f"{magic_set.name} ({magic_set.code.upper()}) announced.",
                )
            )

        if release_at is None:
            return alerts

        days_until_release = (release_at - today).days
        milestone_pairs: Sequence[Tuple[int, SetMilestone, bool, str]] = [
            (30, SetMilestone.T_MINUS_30, state.notified_t_minus_30, "30 days"),
            (14, SetMilestone.T_MINUS_14, state.notified_t_minus_14, "two weeks"),
            (7, SetMilestone.T_MINUS_7, state.notified_t_minus_7, "one week"),
            (1, SetMilestone.T_MINUS_1, state.notified_t_minus_1, "tomorrow"),
        ]

        for threshold, milestone, already_sent, label in milestone_pairs:
            if already_sent:
                continue
            if days_until_release == threshold:
                alerts.append(
                    SetAlert(
                        magic_set=magic_set,
                        milestone=milestone,
                        scheduled_for=today,
                        message=(
                            f"{magic_set.name} releases in {label}! "
                            f"({release_at.isoformat()})"
                        ),
                    )
                )

        if not state.notified_release_day and days_until_release == 0:
            alerts.append(
                SetAlert(
                    magic_set=magic_set,
                    milestone=SetMilestone.RELEASE_DAY,
                    scheduled_for=today,
                    message=(
                        f"{magic_set.name} releases today!"
                    ),
                )
            )

        return alerts
