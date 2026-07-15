import asyncio


class FakeLLM:
    def __init__(
        self,
        response: str,
        *,
        success: bool = True,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.success = success
        self.error = error
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error is not None:
            raise self.error
        return {"success": self.success, "response": self.response, "model": "fake"}


class FakeMessageProxy:
    def __init__(self, readable: str) -> None:
        self.readable = readable
        self.recent_calls = []
        self.readable_calls = []

    async def get_recent(self, chat_id: str, limit: int):
        self.recent_calls.append((chat_id, limit))
        return [{"message_id": "m1"}]

    async def build_readable(self, messages, **kwargs):
        self.readable_calls.append((messages, kwargs))
        return self.readable


class SequencedMessageProxy:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.recent_calls = []

    async def get_recent(self, chat_id: str, limit: int):
        self.recent_calls.append((chat_id, limit))
        response = self.responses.pop(0) if self.responses else []
        if isinstance(response, Exception):
            raise response
        return response


class ClockAdvancingMessageProxy:
    def __init__(self, fake_time, advance_seconds, records) -> None:
        self.fake_time = fake_time
        self.advance_seconds = advance_seconds
        self.records = records
        self.recent_calls = []

    async def get_recent(self, chat_id: str, limit: int):
        self.recent_calls.append((chat_id, limit))
        self.fake_time.now += self.advance_seconds
        return self.records


class NeverReturningMessageProxy:
    def __init__(self) -> None:
        self.recent_calls = []
        self.cancelled = False

    async def get_recent(self, chat_id: str, limit: int):
        self.recent_calls.append((chat_id, limit))
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
