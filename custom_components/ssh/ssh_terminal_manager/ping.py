from __future__ import annotations

import asyncio
from contextlib import suppress

import icmplib
from terminal_manager import OfflineError


class Ping:
    use_icmplib: bool | None = None

    def __init__(self, host: str, timeout: int) -> None:
        self._host = host
        self._timeout = timeout

    async def _async_test_icmplib(self) -> bool:
        try:
            await icmplib.async_ping("127.0.0.1", count=0, timeout=0, privileged=False)
        except icmplib.SocketPermissionError:
            return False
        return True

    async def _async_ping_icmplib(self) -> bool:
        result = await icmplib.async_ping(
            self._host,
            count=1,
            timeout=self._timeout,
            privileged=False,
        )
        return result.is_alive

    async def _async_ping_process(self) -> bool:
        process = await asyncio.create_subprocess_exec(
            "ping",
            "-q",
            "-c1",
            f"-W{self._timeout}",
            self._host,
            stdin=None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            close_fds=False,
        )
        try:
            async with asyncio.timeout(self._timeout + 1):
                await process.communicate()
        except TimeoutError:
            if process:
                with suppress(TypeError):
                    await process.kill()
            return False

        if process.returncode and process.returncode > 1:
            raise RuntimeError(f"Exit code: {process.returncode}")

        return process.returncode == 0

    async def async_ping(self) -> None:
        """Ping.

        Raises:
            `OfflineError`

        """
        try:
            if self.use_icmplib is None:
                self.use_icmplib = await self._async_test_icmplib()
            if self.use_icmplib:
                online = await self._async_ping_icmplib()
            else:
                online = await self._async_ping_process()
        except Exception as exc:
            raise OfflineError(self._host, str(exc)) from exc

        if not online:
            raise OfflineError(self._host)
