"""02 - mint an OAuth 2.0 client-credentials token and use it."""

import asyncio

import httpx
from _credentials import base_url, partner
from apipartnerhub import APIPartnerHubClient


async def main() -> None:
    credentials = partner("enterprise")

    async with httpx.AsyncClient(base_url=base_url()) as http:
        response = await http.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
                "scope": "campaigns:read reports:read",
            },
        )
        response.raise_for_status()
        token = response.json()
        print(
            f"token_type={token['token_type']} expires_in={token['expires_in']}s "
            f"scope={token['scope']}"
        )
        print(f"access_token={token['access_token'][:32]}...")

    # The SDK performs the same grant itself and refreshes before expiry.
    async with APIPartnerHubClient(
        base_url(),
        client_id=credentials["client_id"],
        client_secret=credentials["client_secret"],
    ) as client:
        me = await client.get_me()
        print(f"SDK reports auth_mode={me['auth_mode']} partner={me['name']} tier={me['tier']}")
        print(f"request_id={me.request_id}")


if __name__ == "__main__":
    asyncio.run(main())
