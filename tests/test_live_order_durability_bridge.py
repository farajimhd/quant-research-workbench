import asyncio
from concurrent.futures import Future

import pytest

from src.trading_runtime.live_order_durability_bridge import LiveOrderDurabilityBridge


def test_broker_submission_waits_for_durable_receipt_without_blocking_offer():
    async def scenario():
        sent = []
        async def broker(command):
            sent.append(command)
            return f"broker:{command}"
        bridge = LiveOrderDurabilityBridge(broker, capacity=2)
        first, second = Future(), Future()
        first_result = bridge.offer("one", first)
        second_result = bridge.offer("two", second)
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
        bridge = LiveOrderDurabilityBridge(broker, capacity=2)
        failed, later = Future(), Future()
        first_result = bridge.offer("one", failed)
        later_result = bridge.offer("two", later)
        failed.set_exception(RuntimeError("ClickHouse commit failed"))
        later.set_result("commit-two")
        with pytest.raises(RuntimeError, match="ClickHouse commit failed"):
            await first_result
        with pytest.raises(RuntimeError, match="ClickHouse commit failed"):
            await later_result
        assert sent == []
        with pytest.raises(RuntimeError, match="closed or failed"):
            bridge.offer("three", Future())
        with pytest.raises(RuntimeError, match="reconcile durable commands"):
            await bridge.close()
    asyncio.run(scenario())


def test_queue_overflow_stops_new_admission_without_sending_uncommitted_orders():
    async def scenario():
        sent = []
        async def broker(command):
            sent.append(command)
        bridge = LiveOrderDurabilityBridge(broker, capacity=1)
        first, second, third = Future(), Future(), Future()
        first_result = bridge.offer("one", first)
        await asyncio.sleep(0)
        second_result = bridge.offer("two", second)
        with pytest.raises(RuntimeError, match="queue is full"):
            bridge.offer("three", third)
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
        bridge = LiveOrderDurabilityBridge(broker, capacity=2)
        first, second = Future(), Future()
        first.set_result("commit-one")
        second.set_result("commit-two")
        first_result = bridge.offer("one", first)
        second_result = bridge.offer("two", second)
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
        bridge = LiveOrderDurabilityBridge(broker, capacity=1)
        result = bridge.offer("one", Future())
        with pytest.raises(RuntimeError, match="drain timed out"):
            await bridge.close(timeout_seconds=0.01)
        with pytest.raises(RuntimeError, match="drain timed out"):
            await result
        assert sent == []
    asyncio.run(scenario())
