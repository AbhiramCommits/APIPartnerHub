# @apipartnerhub/sdk

TypeScript SDK for the APIPartnerHub partner API. Node 20+, native `fetch`, zero runtime
dependencies.

## Build

```bash
cd sdks/typescript
npm install
npm run build       # tsc -> dist/
npm test            # vitest
```

## Quickstart

```ts
import { APIPartnerHubClient } from "@apipartnerhub/sdk";

const client = new APIPartnerHubClient({
  baseUrl: "http://localhost:8000",
  apiKey: process.env.API_KEY!,
});

const me = await client.getMe();
console.log(me.name, me.tier, me.rate_limit_rpm);

for await (const campaign of client.listCampaigns({ limit: 25 })) {
  console.log(campaign.id, campaign.name);
}
```

OAuth is supported by passing `clientId` / `clientSecret`; tokens are cached and refreshed 60
seconds before expiry. Requests return parsed JSON; `client.lastAttempts` holds the number of
HTTP attempts used by the most recent call (including retries).

## Retry behaviour

`fetchWithRetry` retries 429, 502, 503 and 504 plus network failures with exponential backoff
and full jitter. A 429's `Retry-After` header (seconds or HTTP date) is honored exactly.
`maxRetries` defaults to 3. Every typed error exposes `requestId`, `code`, `type`, `details`
and, for 429, `retryAfter`.

```ts
import { RateLimitedError } from "@apipartnerhub/sdk";

try {
  await client.getMe();
} catch (error) {
  if (error instanceof RateLimitedError) {
    console.log(`retry in ${error.retryAfter}s (request ${error.requestId})`);
  }
}
```

See `examples/typescript/quickstart.ts` and `docs/integration-guide.md`.
