# ERPShop Hashy Bridge

`addons/ai/erpshop_hashy_bridge` is the company-scoped gateway used by future
Hashy OS and AI coworker integrations. It follows the HMX separation between
the AI application and `hashy_bridge`, while using ERP GSSHOP's Django
permissions and organization scope.

## Security boundary

- The gateway is disabled unless `ERPGSSHOP_HASHY_BRIDGE_ENABLED=true`.
- HS256 tokens use an environment-only secret of at least 32 bytes and expire
  within 15 minutes.
- Tokens require issuer `hashy-os`, the configured instance audience, an exact
  active user, and explicit `company_id` plus `branch_id` claims.
- Every entity read uses the same permission and company/branch scoping as the
  ERP UI.
- The module exposes read operations only. Writes and deletes remain closed.
- Invalid authentication is not persisted to the database, preventing an
  unauthenticated audit-log storage attack. Authenticated calls are audited by
  digest without storing request payloads or bearer tokens.

## Endpoints

The public `GET /hashy/v1/ping` endpoint reports module liveness. All other
endpoints require `POST`, `application/json`, and `Authorization: Bearer ...`:

- `/hashy/v1/health`
- `/hashy/v1/instance_info`
- `/hashy/v1/schema`
- `/hashy/v1/search_read`
- `/hashy/v1/read_group`
- `/hashy/v1/batch`

`search_read` uses primary-key cursor pagination with a maximum of 100 rows.
Filters are limited to exact, membership, range, and null operators over fields
already exposed by the ERP entity configuration. `batch` accepts at most 20
read operations. Aggregates run in the database with a five-second PostgreSQL
statement timeout and paginated group results. Responses are capped at 2 MB;
clients reduce `page_size` or requested fields when that limit is reached.

## Environment

```text
ERPGSSHOP_HASHY_BRIDGE_ENABLED=false
ERPGSSHOP_HASHY_BRIDGE_SECRET=<at least 32 random bytes>
ERPGSSHOP_HASHY_BRIDGE_INSTANCE_CODE=<unique instance audience>
ERPGSSHOP_PUBLIC_BASE_URL=https://erp.example.com
```

Do not commit real secrets. Provisioning systems mint short-lived tokens; the
ERP does not provide a public token-minting endpoint.
