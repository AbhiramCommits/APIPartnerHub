"""Retry policy with Retry-After support, exponential backoff and full jitter."""

from __future__ import annotations

import asyncio
import email.utils
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


@dataclass(slots=True)
class RetryPolicy:
    """Controls retry behaviour for one SDK client."""

    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    retry_statuses: frozenset[int] = field(default_factory=lambda: RETRYABLE_STATUSES)
    respect_retry_after: bool = True

    def delay_for_attempt(self, attempt_index: int, rng: random.Random | None = None) -> float:
        """Exponential backoff with full jitter: uniform(0, base * 2**attempt)."""

        generator = rng or random
        ceiling = min(self.max_delay, self.base_delay * (2**attempt_index))
        return generator.uniform(0, ceiling)

    def retry_after_seconds(self, response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            seconds = float(raw)
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                return None
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            delta = (when - datetime.now(UTC)).total_seconds()
            return max(0.0, delta)
        return max(0.0, seconds)

    def delay_for_response(
        self, response: httpx.Response, attempt_index: int, rng: random.Random | None = None
    ) -> float:
        if self.respect_retry_after:
            retry_after = self.retry_after_seconds(response)
            if retry_after is not None:
                return retry_after
        return self.delay_for_attempt(attempt_index, rng)


@dataclass(slots=True)
class RetryOutcome:
    response: httpx.Response
    attempts: int
    total_retry_delay: float


SleepFunc = Callable[[float], Awaitable[None]]


async def request_with_retries(
    send: Callable[[], Awaitable[httpx.Response]],
    policy: RetryPolicy,
    *,
    sleep: SleepFunc | None = None,
    rng: random.Random | None = None,
) -> RetryOutcome:
    """Execute ``send`` until it succeeds, a non-retryable status arrives, or attempts run out."""

    sleep_fn = sleep or asyncio.sleep
    total_delay = 0.0
    attempts = 0
    while True:
        attempts += 1
        try:
            response = await send()
        except httpx.TransportError:
            if attempts > policy.max_retries:
                raise
            delay = policy.delay_for_attempt(attempts - 1, rng)
            await sleep_fn(delay)
            total_delay += delay
            continue

        if response.status_code in policy.retry_statuses and attempts <= policy.max_retries:
            delay = policy.delay_for_response(response, attempts - 1, rng)
            await sleep_fn(delay)
            total_delay += delay
            continue
        return RetryOutcome(
            response=response,
            attempts=attempts,
            total_retry_delay=round(total_delay, 3),
        )
