# LeadFlow: AI-ready lead qualification workflow (portfolio demonstration)

**Status:** working local demonstration, not a deployed client implementation. **No AI model or CRM is connected.** This deterministic baseline demonstrates the workflow pattern that an AI-assisted implementation can extend safely.

## Portfolio preview

![LeadFlow portfolio overview showing the local workflow, implemented capabilities, and demo status](docs/images/leadflow-portfolio-page-1.png)

[View the full two-page portfolio (PDF)](docs/LeadFlow_Upwork_Portfolio.pdf)

<details>
<summary>Engineering notes (page 2)</summary>

![LeadFlow engineering notes showing example output, quality and safety boundaries, and the extension roadmap](docs/images/leadflow-portfolio-page-2.png)

</details>

## What it demonstrates
- Validates incoming JSON enquiries; handles unknown budget rather than inventing it.
- Produces transparent qualification tiers and reasons.
- Deduplicates by normalized email in SQLite.
- Drafts follow-up text **without sending any email**.
- Tracks a human approval flag for follow-up; CSV export includes pending and approved leads.
- Exports lead summaries as a CSV for manual CRM import.
- Includes tests for missing data, scoring, duplicate handling, and approval state.

## Quick start (Python 3.10+; standard library only)

```bash
python leadflow.py --db demo.sqlite3 submit examples/lead_high.json
python leadflow.py --db demo.sqlite3 submit examples/lead_missing_details.json
python leadflow.py --db demo.sqlite3 list
python leadflow.py --db demo.sqlite3 export results.csv
```

To approve a specific lead, copy its ID from `list` and run:

```bash
python leadflow.py --db demo.sqlite3 approve LEAD_ID
```

Tests: `python -m pytest -q` (pytest is used only for tests).

## Production extension plan
1. Replace static scoring with a structured-output LLM step that validates schema and confidence; retain safe fallback to manual review.
2. Integrate a specific form and CRM using authenticated webhooks/API clients.
3. Use robust idempotency keys combining lead source and event ID (demo email-based deduplication is intentionally simplified).
4. Add retries, a dead-letter queue, privacy controls, encrypted secrets, monitoring and data retention policies.
5. Test with client-authorized sample data before any production CRM write.

## Boundaries
This demo does not connect to HubSpot, Zapier, Claude, OpenAI, or a real email provider. There is no real-world lead volume, production uptime, security audit, or client case study to claim. No messages are sent automatically.
