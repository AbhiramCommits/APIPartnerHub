"""01 - authenticate with a long-lived API key."""

import asyncio

from _credentials import base_url, partner
from apipartnerhub import APIPartnerHubClient


async def main() -> None:
    credentials = partner("free")
    async with APIPartnerHubClient(base_url(), api_key=credentials["api_key"]) as client:
        me = await client.get_me()
        print(f"Authenticated via {me['auth_mode']} as {me['name']} (partner {me['id']})")
        print(f"tier={me['tier']} steady={me['rate_limit_rpm']} rpm burst={me['rate_limit_burst']}")
        print("scopes:", ", ".join(me["scopes"]))
        print(f"attempts={me.attempts} request_id={me.request_id}")


if __name__ == "__main__":
    asyncio.run(main())
