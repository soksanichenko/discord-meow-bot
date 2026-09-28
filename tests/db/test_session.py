"""Session factory lifecycle tests."""

import asyncio
import gc
import weakref

from sources.lib.db import AsyncSession


async def test_session_does_not_keep_finished_task_alive() -> None:
    # Every Discord event runs in its own task; if the session registry keeps a
    # reference to each one, memory grows with message volume until restart.
    async def use_session() -> None:
        async with AsyncSession():
            pass

    task = asyncio.create_task(use_session())
    await task
    task_ref = weakref.ref(task)
    del task
    # Let the loop drop its own done-callback references to the task.
    await asyncio.sleep(0)
    gc.collect()

    assert task_ref() is None
