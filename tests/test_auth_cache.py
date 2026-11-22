"""Short-lived API-key verification cache behaviour."""

from __future__ import annotations

import time

from app import auth as auth_module


def test_cache_round_trip_and_expiry() -> None:
    prefix = "cachetest"
    digest = auth_module._digest("some-key")
    assert not auth_module._was_verified(prefix, digest)
    auth_module._remember_verified(prefix, digest)
    assert auth_module._was_verified(prefix, digest)
    assert not auth_module._was_verified(prefix, auth_module._digest("different-key"))

    auth_module._VERIFIED_CACHE[prefix] = (digest, time.monotonic() - 1)
    assert not auth_module._was_verified(prefix, digest)
    assert prefix not in auth_module._VERIFIED_CACHE


def test_cache_is_bounded() -> None:
    auth_module._VERIFIED_CACHE.clear()
    for index in range(auth_module._MAX_CACHED_KEYS + 5):
        auth_module._remember_verified(f"prefix-{index}", f"digest-{index}")
    assert len(auth_module._VERIFIED_CACHE) <= auth_module._MAX_CACHED_KEYS + 1
    auth_module._VERIFIED_CACHE.clear()
    auth_module._remember_verified("fresh", "digest")
    assert len(auth_module._VERIFIED_CACHE) == 1
    assert "fresh" in auth_module._VERIFIED_CACHE
    auth_module._VERIFIED_CACHE.clear()
