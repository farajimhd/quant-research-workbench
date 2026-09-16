import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.run_strategy_222_refinement import request_stop


@pytest.mark.parametrize('status,requested',[('running',True),('stopped',False),('failed',False)])
def test_stop_is_not_repeated_while_worker_finishes_cleanup(status,requested):
    async def check():
        worker=asyncio.get_running_loop().create_future()
        controller=SimpleNamespace(_task=worker,status=status,_stop_requested=requested,command=AsyncMock())
        await request_stop(controller)
        controller.command.assert_not_awaited()
        assert not worker.done()  # The caller still needs to await cleanup.
        worker.set_result(None)
    asyncio.run(check())


@pytest.mark.parametrize('terminal_race',[True,False])
def test_only_terminal_race_is_accepted(terminal_race):
    async def check():
        worker=asyncio.get_running_loop().create_future()
        controller=SimpleNamespace(_task=worker,status='running',_stop_requested=False)
        async def command(value):
            assert value=='stop'
            if terminal_race:controller.status='stopped'
            raise ValueError('command failed')
        controller.command=command
        if terminal_race:await request_stop(controller)
        else:
            with pytest.raises(ValueError,match='command failed'):await request_stop(controller)
        worker.set_result(None)
    asyncio.run(check())
