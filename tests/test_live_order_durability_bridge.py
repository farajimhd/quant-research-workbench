import asyncio
from concurrent.futures import Future

import pytest

from src.trading_runtime.live_order_durability_bridge import LiveOrderDurabilityBridge


class _CurrentLease:
    def assert_current(self):
        return None


def test_broker_submission_waits_for_durable_receipt_without_blocking_offer():
    async def scenario():
        sent = []
        async def broker(command):
            sent.append(command)
            return f"broker:{command}"
        bridge = LiveOrderDurabilityBridge(broker, keeper_lease=_CurrentLease(), capacity=2)
        first, second = Future(), Future()
        first_result = bridge.offer("one", first, expected_commit_id="commit-one")
        second_result = bridge.offer("two", second, expected_commit_id="commit-two")
        await asyncio.sleep(0)
        assert sent == []
        second.set_result("commit-two")
        await asyncio.sleep(0)
        assert sent == []  # FIFO: later durability cannot overtake an earlier command.
        first.set_result("commit-one")
        assert await first_result == "broker:one"
        assert await second_result == "broker:two"
        assert sent == ["one", "two"]
        await bridge.close()
    asyncio.run(scenario())


def test_failed_receipt_never_submits_a_broker_order_and_stops_lane():
    async def scenario():
        sent = []
        async def broker(command):
            sent.append(command)
        bridge = LiveOrderDurabilityBridge(broker, keeper_lease=_CurrentLease(), capacity=2)
        failed, later = Future(), Future()
        first_result = bridge.offer("one", failed, expected_commit_id="commit-one")
        later_result = bridge.offer("two", later, expected_commit_id="commit-two")
        failed.set_exception(RuntimeError("ClickHouse commit failed"))
        later.set_result("commit-two")
        with pytest.raises(RuntimeError, match="ClickHouse commit failed"):
            await first_result
        with pytest.raises(RuntimeError, match="ClickHouse commit failed"):
            await later_result
        assert sent == []
        with pytest.raises(RuntimeError, match="closed or failed"):
            bridge.offer("three", Future(), expected_commit_id="commit-three")
        with pytest.raises(RuntimeError, match="reconcile durable commands"):
            await bridge.close()
    asyncio.run(scenario())


def test_unrelated_successful_receipt_never_submits_broker_order():
    async def scenario():
        sent = []
        async def broker(command):
            sent.append(command)
        bridge = LiveOrderDurabilityBridge(broker, keeper_lease=_CurrentLease(), capacity=1)
        receipt = Future()
        result = bridge.offer("one", receipt, expected_commit_id="command-batch")
        receipt.set_result("unrelated-batch")
        with pytest.raises(RuntimeError, match="differs from its normalized commit"):
            await result
        assert sent == []
        with pytest.raises(RuntimeError, match="reconcile durable commands"):
            await bridge.close()
    asyncio.run(scenario())


def test_queue_overflow_stops_new_admission_without_sending_uncommitted_orders():
    async def scenario():
        sent = []
        async def broker(command):
            sent.append(command)
        bridge = LiveOrderDurabilityBridge(broker, keeper_lease=_CurrentLease(), capacity=1)
        first, second, third = Future(), Future(), Future()
        first_result = bridge.offer("one", first, expected_commit_id="commit-one")
        await asyncio.sleep(0)
        second_result = bridge.offer("two", second, expected_commit_id="commit-two")
        with pytest.raises(RuntimeError, match="queue is full"):
            bridge.offer("three", third, expected_commit_id="commit-three")
        first.set_result("commit-one")
        second.set_result("commit-two")
        with pytest.raises(RuntimeError, match="queue is full"):
            await first_result
        with pytest.raises(RuntimeError, match="queue is full"):
            await second_result
        assert sent == []
        with pytest.raises(RuntimeError, match="reconcile durable commands"):
            await bridge.close()
    asyncio.run(scenario())


def test_uncertain_broker_result_stops_following_orders_for_recovery():
    async def scenario():
        sent = []
        async def broker(command):
            sent.append(command)
            raise TimeoutError("broker response lost")
        bridge = LiveOrderDurabilityBridge(broker, keeper_lease=_CurrentLease(), capacity=2)
        first, second = Future(), Future()
        first.set_result("commit-one")
        second.set_result("commit-two")
        first_result = bridge.offer("one", first, expected_commit_id="commit-one")
        second_result = bridge.offer("two", second, expected_commit_id="commit-two")
        with pytest.raises(TimeoutError, match="broker response lost"):
            await first_result
        with pytest.raises(TimeoutError, match="broker response lost"):
            await second_result
        assert sent == ["one"]
        with pytest.raises(RuntimeError, match="reconcile durable commands"):
            await bridge.close()
    asyncio.run(scenario())


def test_control_plane_close_is_bounded_when_receipt_never_arrives():
    async def scenario():
        sent = []
        async def broker(command):
            sent.append(command)
        bridge = LiveOrderDurabilityBridge(broker, keeper_lease=_CurrentLease(), capacity=1)
        result = bridge.offer("one", Future(), expected_commit_id="commit-one")
        with pytest.raises(RuntimeError, match="drain timed out"):
            await bridge.close(timeout_seconds=0.01)
        with pytest.raises(RuntimeError, match="drain timed out"):
            await result
        assert sent == []
    asyncio.run(scenario())


def test_lost_keeper_owner_never_dispatches_committed_or_queued_commands():
    class Lease:
        current = True

        def assert_current(self):
            if not self.current:
                raise RuntimeError("Keeper owner lost")

    async def scenario():
        sent = []
        async def broker(command):
            sent.append(command)
        lease = Lease()
        bridge = LiveOrderDurabilityBridge(broker, capacity=2, keeper_lease=lease)
        first, second = Future(), Future()
        first_result = bridge.offer("one", first, expected_commit_id="commit-one")
        second_result = bridge.offer("two", second, expected_commit_id="commit-two")
        await asyncio.sleep(0.01)
        lease.current = False
        first.set_result("commit-one")
        second.set_result("commit-two")
        with pytest.raises(RuntimeError, match="Keeper owner lost"):
            await first_result
        with pytest.raises(RuntimeError, match="Keeper owner lost"):
            await second_result
        assert sent == []
        with pytest.raises(RuntimeError, match="reconcile durable commands"):
            await bridge.close()
    asyncio.run(scenario())
