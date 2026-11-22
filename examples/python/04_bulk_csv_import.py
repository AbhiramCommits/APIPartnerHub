"""04 - upload a CSV and report per-row validation results."""

import asyncio

from _credentials import ROOT, base_url, partner
from apipartnerhub import APIPartnerHubClient


async def main() -> None:
    credentials = partner("standard")
    csv_path = ROOT / "examples" / "sample_campaigns.csv"
    async with APIPartnerHubClient(base_url(), api_key=credentials["api_key"]) as client:
        result = await client.bulk_upload_campaigns(csv_path)
        print(f"uploaded {csv_path.name}")
        print(f"accepted={result['accepted']} rejected={result['rejected']}")
        for error in result["errors"]:
            print(f"  row {error['row']} field={error['field']}: {error['message']}")
        print("created_ids:", result["created_ids"])
        print(f"attempts={result.attempts} request_id={result.request_id}")


if __name__ == "__main__":
    asyncio.run(main())
