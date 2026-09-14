# Visual verification ledger

The design concept is `neighborhood-concept.png`, generated specifically from the user's civic-product brief. The implementation is a usable application, not the concept raster embedded as UI. Root and frontend reviewers inspected actual browser renders with the image viewer. Screen-specific evidence lives in `docs/verification`.

| Comparison | Concept | Implementation / decision |
|---|---|---|
| Information architecture | Neighborhood, Report an issue, My cases; resident selector | Same navigation; compact mobile row retains all destinations |
| Palette | Warm off-white, readable dark ink, deep teal, restrained amber notice | Same palette roles and persistent simulation banner; no neon/gradients |
| Layout | Coordinated incident list and map | Same desktop split; map above list on mobile; actual filters and selected markers |
| Typography and spacing | Readable civic tool with practical whitespace | Smaller desktop headings and denser rows intentionally accommodate real case content and extra privacy copy |
| Assets | Curb-ramp and pothole photography-style fixtures | Original generated synthetic illustrations with attribution; actual uploaded, sanitized derivatives are displayed |
| Map | Illustrative streets, garden, incident markers | Code-native GeoJSON/MapLibre geometry, explicitly fictional; AWS map separately configured and never silently substituted |
| Data | Example two-case composition | Actual persisted workspace records; counts/statuses follow backend instead of copying concept data |
| Detail and approval | Shared visual vocabulary | Signature case summary/evidence/timeline plus exact report in an adjacent rail; rail moves first on mobile |
| Publication consent | High-level concept did not specify every checkbox | Real functional controls separately authorize summary publication, reviewed photos, and exact agency submission |
| Verification | Application continuation beyond primary-screen concept | Separate agency/resolution indicators, optional after-photo, and resident-confirmed outcome |

Fixed during browser QA: MapLibre module worker URL prevented geometry rendering; upload image min-height escaped its preview and intercepted a checkbox; resident switch cached a stale selected identity. Coincident approximate public map markers are separated by a small screen offset to keep both selectable, without asserting different real coordinates. The offset is a visualization of overlapping approximate locations.

This ledger records inspected design fit and intentional functional deviations. It does not claim an independent design-agency certification or an assistive-technology accessibility audit.
