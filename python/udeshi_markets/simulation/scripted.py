"""A participant that replays a fixed script of orders and cancels at given simulated times.

Used for scenario tests, demonstrations and replays. Order ids are only known once orders
reach the exchange, so a scripted cancel refers to an earlier scripted order by its position in
the script.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from udeshi_markets.simulation.events import OrderIntent
from udeshi_markets.simulation.participant import CancelReport, Fill, OrderReport, Participant

if TYPE_CHECKING:
    from udeshi_markets.simulation.kernel import SimulationContext


@dataclass(frozen=True, slots=True)
class ScriptedOrder:
    time: int
    intent: OrderIntent


@dataclass(frozen=True, slots=True)
class ScriptedCancel:
    time: int
    order_index: int  # Position in the script of the ScriptedOrder to cancel.


ScriptAction = ScriptedOrder | ScriptedCancel


class ScriptedParticipant(Participant):
    """Submits a predetermined sequence of actions and records everything it is told.

    Reports for this participant's orders arrive in submission order because every order has
    the same latency and the event queue is FIFO among equal timestamps, so each OrderReport is
    matched to the oldest unreported scripted order.
    """

    def __init__(
        self, participant_id: int, actions: Sequence[ScriptAction], name: str | None = None
    ) -> None:
        super().__init__(participant_id, name)
        for index, action in enumerate(actions):
            if action.time < 0:
                raise ValueError("script actions cannot be scheduled before time zero")
            if isinstance(action, ScriptedCancel) and not (
                0 <= action.order_index < index
                and isinstance(actions[action.order_index], ScriptedOrder)
            ):
                raise ValueError(f"action {index}: cancel must refer to an earlier scripted order")
        self._actions = tuple(actions)
        self._awaiting_report: deque[int] = deque()
        self.order_ids: dict[int, int] = {}  # Script index -> exchange order id (accepted only).
        self.reports: list[OrderReport] = []
        self.fills: list[Fill] = []
        self.cancels: list[CancelReport] = []

    def on_start(self, ctx: SimulationContext) -> None:
        for index, action in enumerate(self._actions):
            ctx.wakeup(max(0, action.time - ctx.now), str(index))

    def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
        index = int(tag)
        action = self._actions[index]
        if isinstance(action, ScriptedOrder):
            self._awaiting_report.append(index)
            ctx.submit(action.intent)
            return
        order_id = self.order_ids.get(action.order_index)
        if order_id is not None:
            target = self._actions[action.order_index]
            assert isinstance(target, ScriptedOrder)
            ctx.cancel(target.intent.instrument, order_id)

    def on_order_report(self, ctx: SimulationContext, report: OrderReport) -> None:
        index = self._awaiting_report.popleft()
        if report.order_id:
            self.order_ids[index] = report.order_id
        self.reports.append(report)

    def on_fill(self, ctx: SimulationContext, fill: Fill) -> None:
        self.fills.append(fill)

    def on_cancel(self, ctx: SimulationContext, report: CancelReport) -> None:
        self.cancels.append(report)
