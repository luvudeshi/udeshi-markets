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

    A cancel whose time comes before its order has been reported (at the same instant, or
    within the order latency) is held and sent as soon as the report arrives. A cancel of an
    order that was rejected is not sent, since there is no order to cancel.
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
        self._held_cancels: dict[int, int] = {}  # Order index -> number of cancels waiting.
        self.order_ids: dict[int, int] = {}  # Script index -> exchange order id (accepted only).
        self._reported: set[int] = set()  # Script indices whose order report has arrived.
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
        if action.order_index in self._reported:
            self._send_cancel(ctx, action.order_index)
        else:
            self._held_cancels[action.order_index] = (
                self._held_cancels.get(action.order_index, 0) + 1
            )

    def on_order_report(self, ctx: SimulationContext, report: OrderReport) -> None:
        index = self._awaiting_report.popleft()
        self._reported.add(index)
        if report.order_id:
            self.order_ids[index] = report.order_id
        self.reports.append(report)
        for _ in range(self._held_cancels.pop(index, 0)):
            self._send_cancel(ctx, index)

    def _send_cancel(self, ctx: SimulationContext, order_index: int) -> None:
        order_id = self.order_ids.get(order_index)
        if order_id is None:  # The order was rejected: nothing to cancel.
            return
        target = self._actions[order_index]
        assert isinstance(target, ScriptedOrder)
        ctx.cancel(target.intent.instrument, order_id)

    def on_fill(self, ctx: SimulationContext, fill: Fill) -> None:
        self.fills.append(fill)

    def on_cancel(self, ctx: SimulationContext, report: CancelReport) -> None:
        self.cancels.append(report)
