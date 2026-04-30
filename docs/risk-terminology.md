# Risk Terminology

Lancelot uses one risk model with different labels at different boundaries.
The labels below are intentionally mapped instead of forced into one string
because each layer has a different job.

| Layer | Low / inspect | Mutating / reversible | Destructive / irreversible |
|-------|---------------|-----------------------|----------------------------|
| Governance policy | T0 inert or T1 reversible | T1 reversible or T2 controlled | T3 irreversible |
| Tool Fabric | low | medium | high |
| UAB daemon | safe | moderate | destructive |

Rules of use:

- Governance tiers are authoritative for policy decisions.
- Tool Fabric risk levels describe work-package risk before provider routing.
- UAB daemon risk levels describe local desktop action safety before execution.
- When terms cross a layer boundary, code should translate explicitly using the
  table above rather than comparing raw strings from another layer.
