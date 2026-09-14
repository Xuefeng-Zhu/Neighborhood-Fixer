# Frontend design inventory

Reference: `neighborhood-concept.png`, generated 1536 × 1024 civic application concept. Functional downstream states extend the same tokens and component families.

- Warm off-white background #f7f7f3; light neutral surfaces #ffffff; deep teal #075e58; headings #083d3a; body #203b45; muted #61717a; border #d7dddd; amber #fff0c9. Color locked to concept, no gradients or image overlays.
- Typography: system sans, 15px body, 14px controls, 12px metadata; heading 38px/1.12 at desktop, 30px mobile. Heading weight 750; body 400–500. Deliberately compact relative to concept so the live product fits ordinary laptop viewports.
- Header: outlined overlapping-square mark, Neighborhood Fixer, essential navigation Neighborhood / Report an issue / My cases; local resident selector. Persistent exact local-mode notice beneath header.
- First viewport: Neighborhood heading, tagline "Report once. Follow through together.", teal Report an issue CTA. Three filters, incident rows left, coordinated map right. Footer Demo Borough and product name.
- Incident rows: stable evidence frame, category, observation count, title, approximate location, agency status separately from resolution status; selected teal border, map marker selection. Seeded counts come from persisted records, never concept fiction.
- Map: MapLibre original GeoJSON streets, blocks and park; no external tiles or tracking in local mode. Fictional geography is an explicit design deviation authorized by the root; label as illustrative, not a navigation map.
- Media: public-safe API evidence images, no visual tint; generated fixture evidence explicitly illustrative. Empty media gets a purposeful category icon and explanatory text.
- Buttons: 6–8px radii, deep teal primary, white bordered secondary, icon stroke 1.8–2px. Lucide icons for functional affordances; original square brand motif.
- Container model: open page heading, two-column list/map, bordered incident rows. Detail extends to an open evidence/story column and a single approval/action rail. Forms use one main panel, linear step navigation, one primary next action.
- Motion: focus and selection state transitions only, reduced-motion respected. Mobile navigation remains text, detail rail stacks; report steps compact and keyboard accessible.
- Required downstream controls intentionally extend reference: resumable report steps, agent evidence distinction, duplicate confirmation, exact report revision approval, publication consent separate from agency send, verification choices, actual receipt/timeline, protected local controls, integration status, errors and empty states.
- Above-fold allowed copy: Neighborhood Fixer; Neighborhood; Report an issue; My cases; Alex/Sam; Local demo — simulated AI and fictional agency.; Report once. Follow through together.; All categories; All statuses; Following; case data; Demo Borough; Illustrative neighborhood · not a navigation map. Local control labels needed for functional demo are intentional additions.
