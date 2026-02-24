# Project Documentation

This repository maintains living documentation alongside code. Use the following folders:

- docs/requirements — Product-level requirements and specs (PRD/SRS), acceptance criteria, and traceability
- docs/architecture — Architecture Decision Records (ADRs), diagrams, and system design notes
- docs/stories — User stories, use cases, and acceptance tests
- docs/workflows — Agentic workflows, runbooks, and operational playbooks

Each folder contains a README with guidance and one or more template markdown files to standardize contributions.

General rules
- One artifact per file, small and focused. Link liberally between related docs.
- Prefer incremental ADRs to capture design rationale over time.
- Do not commit secrets. Use placeholders in examples and store real credentials in your local .env only.
- Keep documentation close to changes (update docs in the same PR as code where applicable).
