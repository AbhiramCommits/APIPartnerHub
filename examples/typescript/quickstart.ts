import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { APIPartnerHubClient } from "../../sdks/typescript/src/index.js";

const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
const creds = JSON.parse(await readFile(join(repoRoot, ".seed_credentials.json"), "utf8"));
const partner = creds.partners.find((entry: { tier: string }) => entry.tier === "enterprise");
if (!partner) throw new Error("Run `python -m scripts.seed` first.");

const baseUrl = process.env.BASE_URL ?? "http://localhost:8000";
const client = new APIPartnerHubClient({ baseUrl, apiKey: partner.api_key });

const me = await client.getMe();
console.log(`Authenticated as ${me.name} (${me.tier}, ${me.rate_limit_rpm} rpm, ${me.auth_mode})`);

let total = 0;
for await (const campaign of client.listCampaigns({ limit: 5 })) {
  total += 1;
  if (total <= 5) console.log(`  ${campaign.id} ${campaign.status} ${campaign.name}`);
}
console.log(`Walked ${total} campaigns with automatic cursor pagination`);

const created = await client.createCampaign(
  {
    name: "TS Quickstart Campaign",
    status: "active",
    daily_budget_cents: 4242,
    objective: "conversions",
  },
  "ts-quickstart-demo",
);
console.log(`createCampaign id=${created.id} attempts=${client.lastAttempts}`);

const end = new Date();
const start = new Date(end.getTime() - 6 * 86_400_000);
const metrics = await client.getMetrics({
  startDate: start.toISOString().slice(0, 10),
  endDate: end.toISOString().slice(0, 10),
  groupBy: "day",
});
if (typeof metrics !== "string") {
  console.log(`metrics rows=${metrics.row_count} first=${JSON.stringify(metrics.data[0])}`);
}

const csv = new Blob([await readFile(join(repoRoot, "examples", "sample_campaigns.csv"), "utf8")], {
  type: "text/csv",
});
const bulk = await client.bulkUploadCampaigns(csv, "sample_campaigns.csv");
console.log(`bulk accepted=${bulk.accepted} rejected=${bulk.rejected} errors=${bulk.errors.length}`);
