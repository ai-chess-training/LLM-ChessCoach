# Engineering Guide for LLM‑ChessCoach

This document defines how we build, review, and operate this project.

## Core Principles
- Test‑Driven Development: write failing tests first; make them pass; refactor.
- Mobile‑first API: keep responses concise, structured, and fast; SSE for live.
- Safety by default: secure secrets, minimal permissions, strict input/output validation.
- Observability: consistent logs, request ids, timings, and simple metrics.
- Performance: cache engine and LLM results; bound concurrency; avoid stalls.

## LLM Usage
- Default model: `gpt-5-nano` or better. Reject older models unless explicitly configured.
- Always require strict JSON outputs for machine‑consumption; validate before use.
- Cost control: use rule‑based coaching for trivial cases; batch prompts; cache by structured input hash.
- Guardrails: ground advice in engine PV; never contradict best line without justification.

## Engine (Stockfish)
- MultiPV = 5 in production; use env `MULTIPV` for overrides in tests.
- Nodes per PV ≈ 1,000,000 in production; use env `NODES_PER_PV` to tune in tests.
- Compute cp‑loss from the mover’s perspective; unify thresholds for severity.
- Capture and persist engine options with results for reproducibility when needed.

## API Contracts
- Canonical response shapes defined in `schemas.py`.
- Per‑move includes: basic (≤15 words), multipv, severity.
- SSE stream emits `basic` then `extended` for live moves.
- Bearer auth required for all `/v1/*` endpoints; enforce quotas at the gateway (future).

## Testing
- E2E tests must cover: session lifecycle, SSE, and batch analysis with real engine.
- Unit tests should validate: cp‑loss math, severity thresholds, PV parsing, PGN parsing.
- Use env overrides (`MULTIPV`, `NODES_PER_PV`) to keep CI fast.
- Avoid network reliance in CI: LLM calls should gracefully fallback to rules.

## Security & Secrets
- Store secrets in environment vars; never commit real tokens/keys.
- Provide `.env.example`; use `.env` for local only; configure runtime secrets via platform.
- Validate file paths and sanitize inputs; avoid path traversal.

## Code Style & Process
- Small, focused PRs; meaningful commit messages.
- Keep changes minimal and consistent with codebase style; prefer refactors over hacks.
- Document new endpoints/flags in README and OpenAPI.

## Performance & Reliability
- Use shared engine processes with bounded queues; pool where appropriate.
- Cache engine/LLM results; add simple store (SQLite/LMDB) when stable.
- Log timings and cache hit rates; watch latency budgets (basic ≤ 300ms).

