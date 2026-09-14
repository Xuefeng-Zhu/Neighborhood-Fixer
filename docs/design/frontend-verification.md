# Frontend verification and design fidelity

The implementation follows `docs/design/neighborhood-concept.png` (1536 × 1024) and `frontend-inventory.md`.

## Functional verification

- Vitest tests exact report authorization, independent community publication consent, clearing checked authorization when the material revision changes, structured error preservation and durable operation terminal detection.
- Playwright tests use new signed local sessions/isolated workspaces. They run the actual frontend, API, persisted worker and fictional portal; no network handlers are stubbed in these tests.
- Browser first visual inspection used Codex IAB. Playwright is additionally used because the user explicitly requires reproducible end-to-end tests and screenshot artifacts.
- AWS Cognito uses the official authorization-code flow with S256 PKCE, per-tab transaction state, ten-minute transaction expiry, exact configured callback, and expiring access tokens. No client secret or token query parameter. Cloud execution remains unverified without configured deployed resources.
- API request schemas and draft types derive from generated OpenAPI; run `npm run generate:api` after exporting `packages/contracts/openapi.json`.

## Fidelity ledger

| Comparison | Concept | Implementation / resolution |
| --- | --- | --- |
| Palette | Warm off-white, deep teal, restrained amber notice | Same palette tokens; no image overlays or gradients |
| Header | Overlapping-square mark, brand, three navigation items, resident picker | Preserved; functional notification control is an intentional addition |
| First viewport | Heading/tagline/CTA, left incident rows, right map | Preserved desktop composition; live seed initially has one distinct pothole rather than the concept's two invented case states |
| Type hierarchy | Large civic heading, compact metadata, strong incident titles | Same hierarchy with deliberately compact desktop sizes fitting normal laptops |
| Status semantics | Separate Agency and Resolution labels | Preserved; agency closure never directly reads as physical resolution |
| Map asset | Illustrative mapped blocks, streets, green park and markers | Original MapLibre GeoJSON replaces concept map raster; markers, panning and zoom are actual controls. Fictional street geometry and compact framing are intentional deviations |
| Evidence | Defined portrait evidence frames; no tint | Preserved frames; real uploaded sanitized API evidence. Empty state explicitly says no shared photo |
| Mobile | Required responsive continuation | Map above list, full-width case rail, compact steps, keyboard/focus and no horizontal page overflow |
| Below-fold flow | Required by product rather than concept image | Exact draft approval, separate publication choices, real timeline/receipt/tool activity, optional verification and protected scenario controls use the same components and palette |

A rendered-map defect from MapLibre 6's separate module worker URL under Vite was repaired with an explicitly bundled worker URL. An image-preview minimum-height overflow that blocked the publication checkbox was fixed by bounding upload review frames. Resident-switch cache invalidation was repaired so identity text and case permissions update together. Stale approval checkboxes now reset on material draft revision changes.

Above-fold copy intentionally includes only required product copy, actual persisted incident information and necessary local mode/privacy labels. No fabricated metrics, agency progress, model output, integration success, or live municipal claims are present.

## Provider references

- Amazon Location resource map API key descriptor: https://docs.aws.amazon.com/location/previous/developerguide/using-apikeys.html
- Cognito authorization endpoint (S256 PKCE): https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html
- Cognito token exchange endpoint: https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html
- Current package release metadata was checked from the official npm registry; exact versions are pinned in package manifests and lockfile. jsdom26.1.0 is intentionally retained because its Node>=18 engine supports the installed Node24.14.1, while the latest jsdom requires a newer Node24 minor.

## Final recorded result

Final local browser run: **2 Playwright journeys passed in 20.4 seconds**. Component and client suite: **11 tests passed**. TypeScript compilation passed. Production Vite build passed; the MapLibre bundle triggers a size advisory (no build failure).

Actual artifacts:
- `docs/verification/neighborhood-desktop.png`: native concept dimensions, 1536 × 1024; two explicitly shared cases and reviewed photos, coordinated map with both markers visible.
- `docs/verification/case-mobile.png`: 390 × 844 viewport after the shared resident-confirmed outcome.
- `docs/verification/case-desktop.png`: desktop full case with persisted timeline, agency receipt and shared outcome.

The concept and final neighborhood/mobile screenshots were inspected using `view_image` in the same QA pass. Palette, header/navigation, heading hierarchy, practical row/map composition, original evidence framing, separate statuses, icon family, and mobile spacing were inspected. Root-authorized compact density and original fictional GeoJSON map remain intentional deviations from the image concept. Overlapping markers caused by privacy-rounded coordinates were separated by a small symmetric screen offset so both cases remain keyboard and pointer selectable. The implementation was faithfully verified against the accepted visual system with these documented functional deviations; no clipped controls, image overflow or horizontal mobile overflow remains in the exercised paths.
