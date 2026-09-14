# AWS domain adapter and current limits

The local worker and AWS Lambda worker both call `services.api.domain.Domain`. AWS storage implements the same `atomic(workspace_id)` / `get` / `put` / `delete` / bounded `list` contract. AgentCore handles short reasoning calls only; Step Functions owns durable waits and callback registration.

| Access | DynamoDB key path | Consistency |
|---|---|---|
| Incident, observation, approval, exact draft, submission attempt | `pk=W#workspace`, `sk=kind#id` | Strong GetItem |
| Case events, subscriptions, operations, notifications, user cases | Same workspace partition, per-incident/per-user kind prefixes and keyset cursor | Bounded strong Query |
| Pending status work | `incident_jobs:incident-id` prefix → exact job records | Strong targeted Query/GetItem |
| Resident identity mapping | Reserved W#auth, identity#SHA256(issuer,subject) → opaque resident ID | Strong GetItem and conditional write |
| Shared workspace admission | W#configured-workspace, workspace#configured-workspace → generation, admission, allowed validation subjects | Strong GetItem |
| Daily quotas and idempotency | Configured workspace quota#resident:UTC-date and quota#workspace:UTC-date plus scoped request identity | One conditional transaction |
| Submission reservation and all domain writes | One TransactWriteItems with workspace `!revision` condition and staged record writes | Atomic conditional transaction |
| Nearby candidates | Nine neighboring `geo:latitude-cell:longitude-cell` kind-prefix queries, then strong incident reads and category/time/distance filtering | Strong geographic candidate reads |
| Fictional portal ticket/grant/attempt | Separate portal table, exact deterministic key | Conditional portal record operations |

There is no DynamoDB Scan in normal application paths and no eventually consistent index used for authorization or submission locking. The workspace revision guard prevents write skew and query phantoms across domain commands. On conflict, commands fail with a retryable conflict response; an external browser write is never blindly replayed. At most 99 application records can be written per transaction, reserving one DynamoDB action for the workspace guard.

The conservative guard serializes writes within a demo workspace. Kind queries are bounded and may stop at DynamoDB's response-size boundary. Geographic-cell keys are maintained transactionally, followed by actual distance checks. Dense neighborhoods require further pagination and load testing. The configured shared Demo Borough is server-assigned after exact Clerk claim validation and admission checks; arbitrary workspace headers are ignored. Internal residents derive from issuer and subject, and private photo/draft/approval ownership remains per resident. The administrator CLI initializes one inert sample, closes admission, inventories every table/object-version page and resets only reviewed generation-scoped data. Normal API routes expose no membership writer or reset.

The nearby semantic matching layer cannot guarantee detection of every independently reported duplicate. Resident confirmation and canonical submission locking reduce repeat reports but cannot make remote website writes atomic. An accepted write with a lost response must remain `OUTCOME_UNKNOWN` until receipt reconciliation or human review.
