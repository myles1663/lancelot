# War Room Visual Walkthrough

The War Room Visual Walkthrough is a standalone React guide for reviewing
Lancelot's operator console without signing in to a live War Room instance. It
uses full-page screenshot assets from `docs/images/` and adds operator-focused
notes for each screen.

It is intentionally separate from the War Room application:

- no backend calls
- no credentials or sessions
- no live controls
- no access to local receipts, logs, or connector state

The guide is designed for release reviews, sales or operator walkthroughs, and
screenshot refresh planning.

## Run Locally

```bash
cd docs/war-room-visual-walkthrough
npm ci
npm run dev
```

Open the local Vite URL printed by the command.

To build the static artifact:

```bash
cd docs/war-room-visual-walkthrough
npm run build
```

The build output is written to `docs/war-room-visual-walkthrough/dist/`.

## What It Covers

The guide maps the current War Room navigation surfaces and important subviews:

- Command Center and command run completion state
- Health, overview, setup, recovery, scheduler, cost, and kill switches
- Governance, approvals, Trust Ledger, and Approval Pattern Learning
- Soul Constitution Viewer, YAML Editor, Template Library, behavior evaluator, and behavior contracts
- Skills proposal review, installed-skill inspection, and runtime toggles
- Receipts, memory, Governed Memory Manager, context efficiency diagnostics, connectors, Tool Fabric, and UAB
- HIVE, incidents, Federation, Fleet Dashboard, graph, audit, and A2A protocol surfaces
- Compliance export and Time-Travel Debugger surfaces

Each screen explains:

- what the operator is inspecting
- what governance behavior applies
- what receipt evidence should exist
- what degraded state means
- what the operator should inspect next

## Screenshot Policy

Screenshots used by this guide should avoid sensitive operational material:

- no real credentials, tokens, or vault material
- no private operator names or customer data
- no local filesystem paths unless intentionally documented
- no internal-only strategy notes or process artifacts
- no live receipt payloads that disclose private work

If a live instance is used for capture, prefer demo state that represents the
operator workflow clearly.

Capture route-level screens as clean live War Room screenshots with enough
height to show the operator surface without duplicating fixed headers or
sidebars.

For long pages, capture the unique operator or configuration surfaces rather
than every repeated row. Repetitive list continuation can be summarized, but
configuration panels, approval controls, destructive controls, and diagnostic
result sections should not be hidden below the fold. For the Governed Memory
Manager, use a top-section sample plus a bottom/history sample instead of
documenting every repeated queued item.

## Refresh Queue

The app marks any non-current state as a refresh candidate. Most route-level
screens use fresh captures from the current local War Room.
State-specific screens, such as a completed command run, should be refreshed
when a completed demo run is available.
