"""03 - walk every campaign, following opaque cursors automatically."""

import asyncio

from _credentials import base_url, partner
from apipartnerhub import APIPartnerHubClient


async def main() -> None:
    credentials = partner("free")
    async with APIPartnerHubClient(base_url(), api_key=credentials["api_key"]) as client:
        count = 0
        active = 0
        async for campaign in client.list_campaigns(limit=4):
            count += 1
            if campaign["status"] == "active":
                active += 1
            print(
                f"{count:02d} id={campaign['id']:<3} status={campaign['status']:<6} "
                f"{campaign['name']}"
            )
        print(f"walked {count} campaigns ({active} active) across pages of 4")


if __name__ == "__main__":
    asyncio.run(main())
