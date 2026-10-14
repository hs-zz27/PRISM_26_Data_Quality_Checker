# Frontend

Reserved for the user interface. Work starts in `../backend/`; no frontend framework or application has been created yet.

The planned interface needs plain text guideline entry, rule confirmation, dataset loading, review of all items, box/mask/label editing and reviewed-output export. Reviewers control final decisions.

## Backend endpoint templates

Start with [the API guide](../backend/api/README.md) and
[endpoint index](../backend/api/ENDPOINTS.md). The OpenAPI contract is
`../backend/api/openapi.json`; request/response fixtures and four review states
are in `../backend/api/examples.json`.

All HTTP routes are planned. Use the fixtures for frontend mocks until the
backend server exists, and keep its base URL configurable. Do not interpret
completed processing as reviewer acceptance. Finding review and final sample
decisions are separate actions.
