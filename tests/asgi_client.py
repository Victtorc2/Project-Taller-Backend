"""Cliente ASGI sin sockets para entornos donde Windows bloquea socketpair."""
import asyncio
import selectors
import time

import anyio.to_thread
import httpx


class EmptySelector(selectors.SelectSelector):
    def select(self, timeout=None):
        if not self.get_map():
            if timeout:
                time.sleep(min(timeout, 0.01))
            return []
        return super().select(timeout)


class LocalLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        super().__init__(selector=EmptySelector())

    def _make_self_pipe(self):
        self._ssock = self._csock = None

    def _write_to_self(self):
        pass

    def _close_self_pipe(self):
        pass


class LocalPolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self):
        return LocalLoop()


async def inline_run(func, *args, **kwargs):
    return func(*args)


class ASGIClient:
    __test__ = False
    def __init__(self, app):
        self.app = app

    def __enter__(self):
        self.original_policy = asyncio.get_event_loop_policy()
        self.original_run_sync = anyio.to_thread.run_sync
        asyncio.set_event_loop_policy(LocalPolicy())
        anyio.to_thread.run_sync = inline_run
        return self

    def __exit__(self, *_):
        anyio.to_thread.run_sync = self.original_run_sync
        asyncio.set_event_loop_policy(self.original_policy)

    def request(self, method, url, **kwargs):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver") as client:
                return await client.request(method, url, **kwargs)
        return asyncio.run(send())

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)
