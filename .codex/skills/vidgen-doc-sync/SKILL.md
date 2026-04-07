---
name: vidgen-doc-sync
description: Use when updating the VidGen codebase in ways that change system architecture, APIs, user flows, dashboard capabilities, agent workflow, auth, publishing, or delivery behavior. After code changes, update SYSTEM_COMPARISON.zh-CN.md and, when the change affects setup or user-facing usage, also update README.zh-CN.md so documentation stays aligned with implemented behavior.
---

# VidGen Doc Sync

After changing VidGen code, keep the Chinese system docs in sync.

## When this skill applies

Use it when a change affects any of these:

- Router/API surface
- Agent workflow or pipeline stages
- Auth, account isolation, personal center, templates
- Auto mode or manual mode UI/behavior
- Model/provider configuration
- Result delivery, publishing, repository saving, or preview behavior
- Setup, environment variables, or startup flow

## Required files

- `SYSTEM_COMPARISON.zh-CN.md`
- `README.zh-CN.md` when the change is user-facing or setup-related

## Workflow

1. Inspect the actual code changes first. Do not document planned-but-unimplemented ideas.
2. Update the date in `SYSTEM_COMPARISON.zh-CN.md` when the document materially changes.
3. Patch only the affected sections instead of rewriting the whole document.
4. Keep wording implementation-accurate:
   - mention real router names, model names, env vars, and UI entry points
   - avoid claiming integrations are production-ready if they still require local config or provider credentials
5. If the change introduces new user actions, make sure `README.zh-CN.md` reflects:
   - where the user finds the feature
   - any required env vars or setup
   - important caveats

## Content rules

- Prefer concise bullets over broad marketing language.
- Separate "implemented now" from "reserved or optional".
- For third-party publishing or external providers, explicitly mention config prerequisites.
- If a feature only works in mock mode or with local assumptions, say so.

## Quick checklist

- `SYSTEM_COMPARISON.zh-CN.md` updated
- `README.zh-CN.md` updated if needed
- New env vars documented if introduced
- New routes/features described with the current behavior, not intended future behavior
