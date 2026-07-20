# Survey Platform — Backend MVP

Multi-tenant survey and data-collection platform (Nepal market). This is
the complete backend vertical slice: every API the future mobile field app
and web UI will consume, with the statistics layer (the differentiator over
free tools) implemented for real.

**In this MVP (all verified by tests, `make test` → 26 passed):**
- Token auth + tenant membership; every scoped endpoint binds Postgres
  row-level security to the caller's tenant. Isolation is enforced by the
  database and proven by tests, not by `.filter()` discipline.
- Versioned survey schemas (English/Nepali translation maps, choices,
  relevance logic, strict validation). Published versions are frozen by a
  **database trigger**: the schema a submission was collected against can
  never silently change.
- Offline-sync contract: batch submissions, client-minted UUIDs as
  idempotency keys, per-item outcomes (`created` / `duplicate` /
  `invalid`), append-only storage (UPDATE/DELETE rejected by trigger),
  paradata + design metadata (stratum, cluster, design weight) captured at
  collection time.
- Public self-response channel: anonymous, throttled, and every response
  is stamped `self_selected` at write time.
- **Estimation engine**: raking (iterative proportional fitting) to census
  margins starting from design weights, Kish effective-sample-size
  standard errors with DEFF reported, and an honesty policy in code:
  self-selected data is excluded from estimates by default and carries a
  `non_representative_warning` when forced in; no endpoint returns a bare
  percentage stripped of its epistemic status.

**Deliberately NOT in this MVP, and why:**
- Android field app: needs the mobile toolchain and devices; it will be
  built against `/api/sync/submissions`, which is stable and tested.
- eSewa/Khalti payments: requires merchant accounts and sandbox
  credentials only the business owner can obtain.
- Web authoring UI, RBAC enforcement per role, anomaly detection on
  paradata, MRP for self-response: next milestones, in that order.

---

## Setup from zero (Windows 11)

Development happens inside **WSL2**. Production is Linux containers;
developing on Linux eliminates parity bugs. You still use VSCode normally.

1. **WSL2** (once): PowerShell *as Administrator* → `wsl --install` → reboot,
   create your Ubuntu user.
2. **Docker Desktop** (once): install, Settings → General → "Use the WSL 2
   based engine"; Settings → Resources → WSL Integration → enable Ubuntu.
3. **VSCode WSL extension** (once): install "WSL" by Microsoft.
4. **Ubuntu prep** (once), in the Ubuntu terminal:
   ```bash
   sudo apt update && sudo apt install -y python3-venv python3-pip git make
   ```
5. **Code lives in Linux**, not `/mnt/c` (10x file-I/O difference):
   ```bash
   mkdir -p ~/code && cd ~/code   # unzip the repo here
   cd survey-platform && code .
   ```
6. **Run**:
   ```bash
   docker compose up -d                      # Postgres 16 + Redis
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r backend/requirements.txt
   make migrate && make test                 # 26 passed = it works on your machine
   make run                                  # API on http://127.0.0.1:8000
   ```

## API walkthrough

```bash
# 1. Bootstrap a tenant + user (one-time, in `make shell`):
#    Tenant.objects.create(name="Demo NGO"); user + TenantMembership + DRF Token
# 2. Then:
export T="Authorization: Token <your-token>"

curl -sX POST localhost:8000/api/surveys/ -H "$T" -H 'Content-Type: application/json' \
     -d '{"title": "Perception wave 1"}'
curl -sX POST localhost:8000/api/surveys/<survey_id>/versions/ -H "$T" \
     -H 'Content-Type: application/json' -d @schema.json
curl -sX POST localhost:8000/api/versions/<version_id>/publish -H "$T"

# Field sync (what the mobile app will call):
curl -sX POST localhost:8000/api/sync/submissions -H "$T" -H 'Content-Type: application/json' -d '
{"submissions": [{"id": "<client-uuid>", "survey_version": "<version_id>",
  "answers": {"q_age": "young", "q_vote": "a"},
  "paradata": {"started_at": "2026-07-04T09:00:00", "ended_at": "2026-07-04T09:07:30"},
  "stratum": "province1_urban", "cluster_id": "psu_017", "design_weight": 240.5}]}'

# Public link + anonymous response:
curl -sX POST localhost:8000/api/versions/<version_id>/links -H "$T"
curl -s localhost:8000/api/public/r/<token>/            # respondent-facing schema (Nepali default)
curl -sX POST localhost:8000/api/public/r/<token>/ -H 'Content-Type: application/json' \
     -d '{"answers": {"q_age": "old", "q_vote": "a"}}'

# The differentiator:
curl -sX POST localhost:8000/api/surveys/<survey_id>/calibrate -H "$T" \
     -H 'Content-Type: application/json' \
     -d '{"name": "census", "margins": {"q_age": {"young": 0.3, "old": 0.7}}}'
curl -s "localhost:8000/api/surveys/<survey_id>/estimates?question=q_vote&scheme=census" -H "$T"
# → proportions + SE + CI95 + n_eff + DEFF + flags{weighted, weight_source,
#   includes_self_selected, non_representative_warning, ...}
```

## Architecture notes worth reading before extending

- `backend/apps/tenants/scoping.py`: how DRF auth meets RLS, and why
  `ATOMIC_REQUESTS=True` is load-bearing.
- `backend/apps/surveys/migrations/0002_enable_rls.py`: the RLS template,
  including the two production bugs it encodes fixes for (owner bypass
  without FORCE; empty-string GUC revert on pooled connections).
- `backend/tests/test_rls_guardrail.py`: any new table with `tenant_id`
  must be RLS-forced or explicitly exempted **with a written reason**, or
  CI fails.
- `backend/apps/estimation/engine.py`: raking + Kish SEs, and the honest
  statement of what the variance method does and does not capture (full
  Taylor linearization over strata/clusters is the next step).

## Milestones from here
1. **RBAC enforcement** (roles exist; wire per-endpoint permissions).
2. **Paradata anomaly detection** (duration outliers, straight-lining,
   off-area GPS) feeding a supervisor review queue.
3. **Mobile field client** against `/api/sync/submissions` (Flutter or RN,
   SQLCipher, background sync).
4. **Authoring web UI**; **Taylor-linearized variance**; **MRP** for
   self-response.
