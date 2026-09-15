# Neighborhood Fixer — submission draft

**Track:** Good Neighbor Agents

**Tagline:** Report once. Follow through together.

## What it does

Neighborhood Fixer is a case manager for everyday public-space maintenance, starting with damaged sidewalks and curb ramps, potholes, and objects blocking a walkway. It helps residents describe evidence, confirm a location, review possible duplicate cases, prepare a report for a supported agency, and authorize the exact report before it is sent. Neighbors can add observations to one shared case and follow the same ticket. Agency closure and resident verification remain separate.

## Inspiration and impact

A broken curb ramp can interrupt a trip to the bus stop, school, or grocery store. People using wheelchairs or strollers often bear the extra effort of documenting a problem, figuring out who receives the request, and chasing an update. Our central demonstration follows two fictional neighbors who contribute evidence to one incident, produce one agency request, and check whether the repair actually happened.

## How it is built

A React/TypeScript application uses a shared FastAPI domain layer with explicit observations, incidents, immutable submission revisions, approvals, attempts, tickets and append-only case events. Local mode uses persisted SQLite jobs and an actual browser-driven fictional portal. Three restricted Strands agents use Amazon Bedrock for real analysis, routing and coordination. The AWS deployment includes AgentCore Runtime and Browser, Step Functions Standard, DynamoDB, private S3, Amplify Hosting and Amazon Location. Clerk provides session-bound public signup with exact API claims; a server-selected shared Demo Borough uses daily quotas and generation-fenced admission. Live acceptance verified signup, maps, privacy, quotas, two residents' photo-to-shared-draft journey, and a clean public one-sample reset. CloudFormation contains zero Cognito resources. The journey stopped before approval because AgentCore Browser navigation remains blocked; see the current test report.

## What the demonstration establishes

The local mode visibly labels deterministic AI fixtures. It is a working persisted application and fictional receiving portal, with actual local Playwright submission. Its purpose is to prove the consent and case-management workflow without sending requests to a real municipality. Real AWS/model evaluation is separately gated. Do not replace this paragraph with a cloud-success claim unless an authorized live run and its evidence exist.

## What was challenging

Approval must remain bound to the exact recipient, fields, contacts, and attachment hashes even when neighbors add observations. A lost connection after the submit click is not a safe reason to submit again. An agency closing a ticket as a duplicate must not turn into a claim that a sidewalk is fixed. These distinctions shaped the domain model, transactional reservation, reconciliation path, and separate status indicators.

## Next steps

Authorized deployment and model evaluation; accessibility testing with residents; independently reviewed public-photo redaction; carefully verified agency partnerships; expanded jurisdiction and asset registries. Memory, voice, vector search, email, payments, social feeds and unapproved municipal submissions are deliberately outside the core scope.

## Participant completion fields

- Repository URL: `https://github.com/Xuefeng-Zhu/Neighborhood-Fixer` (public, with a detected MIT license).
- Public YouTube demo video: `https://youtu.be/vaL5yUI5OX4` (3:28).
- AWS Builder ID: `frankzhu94@gmail.com`.
- Live demo URL: `https://main.d12il66dljooo6.amplifyapp.com/`.
- Architecture: source `docs/architecture.mmd`; rendered PNG attached to the Devpost entry as `neighbor_architecture.png`.
- Actual validation: `docs/TEST-REPORT.md`.
- License: MIT.
- Disclosure: `docs/DISCLOSURE.md`, `fixtures/ATTRIBUTION.md`.

The project is publicly available at `https://devpost.com/software/neighborhood-fixer`. Devpost records it as entered in the Agents for Humans Hackathon with all five form steps complete. The official rules remain the source of truth for eligibility.
