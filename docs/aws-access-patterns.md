# AWS domain adapter and current limits

The local worker and AWS Lambda worker both call `services.api.domain.Domain`. AWS storage implements the same `atomic(workspace_id)` / `get` / `put` / `delete` / bounded `list` contract. AgentCore handles short reasoning calls only; Step Functions owns durable waits and callback registration.

| Access | DynamoDB key path | Consistency |
|---|---|---|
| Incident, observation, approval, exact draft, submission attempt | `pk=W#workspace`, `sk=kind#id` | Strong GetItem |
| Case events, subscriptions, operations, notifications, user cases | Same workspace partition, per-incident/per-user kind prefixes and keyset cursor | Bounded strong Query |
| Pending status work | `incident_jobs:incident-id` prefix → exact job records | Strong targeted Query/GetItem |
| Authenticated demo workspace | Reserved `W#auth`, `membership#cognito-sub` → explicit target workspace | Strong GetItem |
| Submission reservation and all domain writes | One TransactWriteItems with workspace `!revision` condition and staged record writes | Atomic conditional transaction |
| Nearby candidates | Nine neighboring `geo:latitude-cell:longitude-cell` kind-prefix queries, then strong incident reads and category/time/distance filtering | Strong geographic candidate reads |
| Fictional portal ticket/grant/attempt | Separate portal table, exact deterministic key | Conditional portal record operations |

There is no DynamoDB Scan in normal application paths and no eventually consistent index used for authorization or submission locking. The workspace revision guard prevents write skew and query phantoms across domain commands. On conflict, commands fail with a retryable conflict response; an external browser write is never blindly replayed. At most 99 application records can be written per transaction, reserving one DynamoDB action for the workspace guard.

The conservative guard serializes writes within a demo workspace. Kind and geographic-cell queries cap at 500 records and may also stop at DynamoDB's response-size boundary. The shared domain maintains geographic-cell keys transactionally and checks actual distance after retrieval. Large public neighborhoods still need cell pagination and broader public workspace projections before enabling unrestricted discovery. Per-principal cloud workspaces are isolated; no unrestricted shared cloud account or fixture identity switch exists. The administrative provision script writes explicit membership records in the reserved auth partition. API Gateway-verified Cognito subjects are mapped with strongly consistent server-side reads; arbitrary workspace headers are ignored.

The nearby semantic matching layer cannot guarantee detection of every independently reported duplicate. Resident confirmation and canonical submission locking reduce repeat reports but cannot make remote website writes atomic. An accepted write with a lost response must remain `OUTCOME_UNKNOWN` until receipt reconciliation or human review.
