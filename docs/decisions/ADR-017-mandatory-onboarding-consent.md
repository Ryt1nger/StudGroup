# ADR-017: Mandatory legal acceptance at first-run completion

Status: accepted
Date: 2026-11-09

## Context

The first-run API previously conditioned legal checks on `legal_onboarding_required`, allowing an incomplete membership to finish when that switch was off. The legal texts are drafts until operator details and approval are configured. Existing completed memberships must continue to open the app.

## Decision

Every incomplete membership must have current, separately recorded terms acceptance and personal-data processing consent before `/onboarding/complete` succeeds. The server checks the stored version and document digest. The SDO credential consent remains separate and optional. Unpublished documents cannot be read or accepted through public API routes. A completed membership remains complete without replaying registration; any later consent update needs a separate flow.

## Consequences

New members pause at the legal step until operator details and documents are approved. Cached clients cannot bypass completion by sending a flag or skipping the legal screen. Existing members and historic receipts are not modified. Deployment must verify the operator's publication configuration before claiming that new registration is available.
