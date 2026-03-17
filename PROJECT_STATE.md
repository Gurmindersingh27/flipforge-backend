# FlipForge — Project State
*Update this file at the end of every session. Upload alongside CLAUDE.md at the start of every session.*

---

## Last Updated
2026-03-17 (end of auth + deals persistence session)

---

## 1. Current Phase & Progress

**Current phase:** Auth + saved deals persistence complete. All three deals endpoints live. Next: Open Deal (frontend loads saved deal back into analyzer).

### Done
- [x] Backend Day 1 complete — DraftDeal, DataPoint/Confidence models built
- [x] `/api/draft-from-url` working
- [x] `/api/finalize-and-analyze` working (stress tests, breakpoints, rehab_reality, narratives)
- [x] NarrativeGenerator fixed — accepts base metrics
- [x] CLAUDE.md added to both repos
- [x] PROJECT_STATE.md added to both repos
- [x] Backend audited — requirements.txt clean, all routes present, start command correct
- [x] Render backend deployed successfully
- [x] Live backend URL confirmed: https://flipforge-backend.onrender.com
- [x] GET /api/health confirmed live
- [x] POST /api/analyze confirmed working in prod
- [x] POST /api/export/lender-report confirmed returning application/pdf in prod
- [x] Full frontend → backend → PDF pipeline validated end-to-end
- [x] Clerk JWT verification via JWKS implemented (app/auth.py)
- [x] `get_current_user_id` FastAPI dependency — verifies Bearer token, returns Clerk user_id
- [x] `preload_jwks()` made startup-safe — lazy-load fallback if Clerk unreachable at boot
- [x] SavedDeal DB model added (app/db/models/saved_deal.py)
- [x] POST /api/deals/save — authenticated, persists deal with user_id
- [x] GET /api/deals — authenticated, returns all deals for user newest first
- [x] GET /api/deals/{id} — authenticated, returns single deal (404 if not owned by user)
- [x] init_db() called at startup to ensure tables exist

### Not Done
- [ ] Tighten CORS from * to https://flipforge-frontend.vercel.app
- [ ] Move production DB from SQLite to Postgres (ephemeral on Render today)
- [ ] Delete deal endpoint
- [ ] Edit deal endpoint

### Next Session Goal
No backend work needed for the next task (Open Deal is frontend-only).
If backend work is needed: see Section 12 (Next Task).

---

## 2. Repos

| Repo | GitHub | Deployed |
|------|--------|----------|
| Frontend | Gurmindersingh27/flipforge-frontend | Vercel |
| Backend | Gurmindersingh27/flipforge-backend | Render.com |

Active dev branch (both repos): `claude/flipforge-dev-setup-FhRuA`
Never push to `main` or `master` directly.

---

## 3. What This App Does

FlipForge is a risk-first real estate deal underwriting tool for serious investors. The investor enters (or pastes a listing URL for) a property and gets:
- Net profit, ROI, profit margin
- Flip / BRRRR / Wholesale scores and verdicts (BUY / CONDITIONAL / PASS)
- Max Safe Offer (MAO)
- Rehab Reality classification (LIGHT / MEDIUM / HEAVY / EXTREME)
- Stress test scenarios (ARV -5%, ARV -10%, Rehab +15%, Hold +2mo)
- Risk flags with severity levels
- Breakpoints (first stress scenario that kills the deal)
- Confidence score (0-100)
- Lender report PDF export

---

## 4. Backend (Python / FastAPI)

**Stack:** FastAPI 0.115 / Uvicorn / Pydantic v2 / httpx / BeautifulSoup4 / ReportLab
**Entry point:** `app/main.py` (NOT root `main.py` — that is an older v1 setup)
**Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
**Deploy:** Render.com (`render.yaml` present in repo)
**Live URL:** https://flipforge-backend.onrender.com

### Dependencies (requirements.txt)
```
fastapi==0.115.0
uvicorn==0.32.0
pydantic==2.10.0
httpx==0.28.0
beautifulsoup4==4.12.3
reportlab==4.2.5
```

### File Structure
```
app/
  main.py                      ← FastAPI app + ALL active routes (use this)
  models.py                    ← ALL Pydantic models (canonical — sync with types.ts)
  analysis_engine.py           ← Core deal math (ACTIVE engine — do not rewrite)
  core/
    analysis_engine.py         ← Duplicate/older — verify which is imported before editing
    config.py                  ← Dead code — not imported by app/main.py, do not activate
    scoring.py
  services/
    url_service.py             ← Scrapes listing URLs → DraftDeal
    pdf_service.py             ← Generates lender report PDF (ReportLab)
    analyze_service.py
    deal_service.py
    scenario_service.py
  schemas/                     ← analysis, deal, investor_profile, scenario
  api/
    deals.py
    v1/analyze.py, deals.py, profile.py, scenarios.py
  db/                          ← SQLite models (deal, user, analysis, scenario, investor_profile)
main.py                        ← Root entry — older v1 router setup, NOT active
render.yaml
requirements.txt
```

### Active API Endpoints
```
GET  /api/health                   ← ✅ confirmed live in prod
POST /api/analyze                  ← AnalyzeRequest → AnalyzeResponse (SCHEMA FROZEN)
POST /api/draft-from-url           ← { url } → DraftFromUrlResponse
POST /api/finalize-and-analyze     ← DraftDeal → AnalyzeResponse (422 if fields missing)
POST /api/export/lender-report     ← LenderReportRequest → PDF bytes
POST /api/deals/save               ← SaveDealRequest → SavedDealResponse (🔐 Clerk JWT required)
GET  /api/deals                    ← [] SavedDealResponse for current user (🔐 Clerk JWT required)
GET  /api/deals/{id}               ← SavedDealResponse by id (🔐 Clerk JWT required, 404 if not owned)
```

### Auth (app/auth.py)
- `get_current_user_id(token)` — FastAPI dependency, verifies Clerk JWT via JWKS
- `preload_jwks()` — called at startup; lazy-load fallback if Clerk unreachable at boot
- Public routes (`/api/analyze`, `/api/draft-from-url`, `/api/finalize-and-analyze`, `/api/export/lender-report`) remain unauthenticated
- Clerk env var required: `CLERK_JWKS_URL` (set on Render)

### Analysis Engine Logic (analysis_engine.py)
- `compute_base_metrics()` — all core financials
- `compute_max_safe_offer()` — binary search for max purchase price at required margin
- `compute_flip/brrrr/wholesale_score()` — scoring per strategy
- `build_stress_tests()` — 5 scenarios: Base, ARV-5%, ARV-10%, Rehab+15%, Hold+2mo
- `compute_rehab_reality()` — ratio thresholds: <20% LIGHT, 20-40% MEDIUM, 40-60% HEAVY, >=60% EXTREME
- `compute_breakpoints()` — finds first stress scenario that fails
- `compute_confidence_score()` — weighted: margin strength (45%), stress robustness (30%), risk penalty (25%)
- Verdict thresholds: score >= 75 = BUY, >= 55 = CONDITIONAL, else PASS

### URL Scraping (url_service.py)
- httpx fetch with browser User-Agent
- Extraction priority: OG price tags → JSON-LD structured data → regex on body text
- Returns SOURCE_BLOCKED on 403/429 (Zillow/Redfin block this — known, not a bug)
- ARV and rehab_budget are ALWAYS missing — investor must fill manually
- Only purchase_price can realistically be scraped

### PDF Export (pdf_service.py)
- Uses ReportLab (pure Python, no system deps)
- Sections: Header, Property Summary, Deal Overview, Financial Assumptions, Analysis Output, Rehab Reality, Risk Notes, Exit Strategy
- Color coded verdicts: BUY=green, CONDITIONAL=amber, PASS=red
- ⚠️ Production risk: must use in-memory bytes (StreamingResponse), no disk writes

---

## 5. Frontend (React / TypeScript / Vite)

**Stack:** React 19 / TypeScript ~5.9 / Vite 7 / No UI library / Vanilla CSS

### File Structure
```
src/
  main.tsx                    ← App entry point
  App.tsx                     ← Root component
  App.css / index.css         ← Global styles
  config.ts                   ← API_BASE_URL (keep separate from api.ts — do not merge)
  shield.ts                   ← Shield logic
  AnalysisResult.tsx          ← Deal analysis results display
  components/
    ShieldHeader.tsx          ← Header component
  lib/
    api.ts                    ← ALL fetch calls to backend
    types.ts                  ← ALL TypeScript types (canonical contract)
  assets/
    react.svg
```

### API Config
- Reads env var: `VITE_API_BASE_URL`
- Fallback (dev): `http://127.0.0.1:8000`
- `config.ts` and `api.ts` are intentionally separate — do not consolidate

### API Functions (api.ts)
- `analyzeDeal(payload)` → `POST /api/analyze`
- `draftFromUrl(url)` → `POST /api/draft-from-url`
- `finalizeAndAnalyze(draft)` → `POST /api/finalize-and-analyze` (handles 422 missing_fields)
- PDF export call — check App.tsx / AnalysisResult.tsx for usage

### NPM Scripts
```
npm run dev       # Vite dev server (http://localhost:5173)
npm run build     # tsc + vite build
npm run lint      # ESLint
npm run preview   # Preview production build
```

---

## 6. Shared Data Contract

Any change must be made in BOTH `src/lib/types.ts` (frontend) AND `app/models.py` (backend) in the same session.

| Type | Frontend | Backend |
|------|----------|---------|
| `AnalyzeRequest` | ✅ | ✅ |
| `AnalyzeResponse` | ✅ | ✅ |
| `DraftDeal` | ✅ | ✅ |
| `DraftFromUrlResponse` | ✅ | ✅ |
| `DataPoint<T>` | ✅ | ✅ |
| `RehabReality` | ✅ | ✅ |
| `Breakpoints` | ✅ | ✅ |
| `RiskFlag` | ✅ | ✅ |
| `StressTestScenario` | ✅ | ✅ |
| `Verdict` | `"BUY"\|"CONDITIONAL"\|"PASS"` | same |
| `Strategy` | `"flip"\|"brrrr"\|"wholesale"` | same |
| `Confidence` | `"HIGH"\|"MEDIUM"\|"LOW"\|"MISSING"` | same |
| `RehabSeverity` | `"LIGHT"\|"MEDIUM"\|"HEAVY"\|"EXTREME"` | same |

**AnalyzeRequest schema is frozen. Do not modify it.**

---

## 7. Commit History

**Backend (recent, newest first):**
```
1838408  fix: make JWKS preload startup-safe with lazy-load fallback
621fb06  feat: add Clerk auth + saved deals persistence layer
f0a5032  Merge pull request #2
c995435  docs: tick off Polish assumption input display in PROJECT_STATE.md
7f307e2  docs: bring PROJECT_STATE.md up to date
```

**Frontend (recent, newest first):**
```
b5f058c  fix: fall back to draft_input.address when address column is null
9a60727  feat: gate analyzer behind Clerk auth, add SignUpButton
5bec63f  fix: disable Open button in DealsPage until location.state is wired up
666e37b  feat: add Clerk auth + saved deals frontend
```

---

## 8. Known Issues

- Root `main.py` (backend) is an older v1 router setup — active app is `app/main.py`
- `app/core/analysis_engine.py` exists alongside `app/analysis_engine.py` — confirm which is imported before editing either
- `app/core/config.py` imports pydantic-settings but is dead code — not in active import chain, do not add pydantic-settings to requirements.txt
- CORS is wide open (`*`) — needs tightening to Vercel domain before production hardening
- Zillow/Redfin block URL scraping (SOURCE_BLOCKED) — known limitation, not a bug
- PDF generation must use in-memory bytes in production — disk writes will fail on Render
- Render free tier cold starts — first request after inactivity may take 50+ seconds

---

## 9. Database / Persistence State

- **ORM:** SQLAlchemy
- **DB layer:** `app/db/session.py` reads `settings.DATABASE_URL` (from `app/core/config.py`)
- **Default:** `sqlite:///./flipforge.db` — used locally and in production unless overridden
- **Configurable:** Yes — set `DATABASE_URL` env var on Render to switch to Postgres
- **Production today:** SQLite on Render's ephemeral filesystem. ⚠️ Data will be lost on each deploy/restart. Move to Postgres before treating saved deals as durable.
- **Schema managed by:** `app/db/init_db.py` (`init_db()` called at startup via `on_startup`)
- **SavedDeal model:** `app/db/models/saved_deal.py` — columns: id, user_id, address, draft_input (JSON), analysis_result (JSON), created_at

---

## 10. Explicitly Unchanged / Protected Systems

The following were NOT modified and must remain untouched:

- `AnalyzeRequest` schema — frozen, no field additions or removals
- `app/analysis_engine.py` — core underwriting math, do not rewrite
- `POST /api/analyze` — route, request shape, and response shape unchanged
- `POST /api/draft-from-url` — unchanged
- `POST /api/finalize-and-analyze` — unchanged
- Core scoring engine (verdict thresholds, stress test logic, breakpoints, rehab reality) — unchanged

---

## 11. Known Limitations / Open Gaps (backend perspective)

- **Delete deal endpoint not implemented** — no `DELETE /api/deals/{id}`
- **Edit deal endpoint not implemented** — no `PUT /api/deals/{id}`
- **SQLite on Render is ephemeral** — all saved deals lost on redeploy; move to Postgres for production durability
- **CORS not tightened** — still `allow_origins=["*"]`
- **address column normalization** — `SavedDeal.address` is populated at save-time from `draft_input.address` if available; older records may have null address (frontend has display fallback)

---

## 12. Next Recommended Task

**Open Deal** — load a saved deal back into the analyzer. This is frontend-only; no backend changes needed. See frontend PROJECT_STATE.md Section 12 for full scope.

---

## 13. Workflow / Guardrails (preserve across sessions)

1. **Load repo skills first** before doing any work
2. **Plan → File Diff → Approval → Implementation** — no exceptions
3. **Additive changes only** — do not rename fields, remove routes, or change response shapes
4. **No silent dependency installs** — get explicit approval before touching requirements.txt
5. **No schema changes without approval** — AnalyzeRequest is frozen; any other schema change needs PM sign-off
6. **Do not refactor working systems** — if it works, leave it alone

---

## 9. How to Start a New Session

1. Open claude.ai in any browser
2. Start a new Claude Code session
3. Upload both `CLAUDE.md` and `PROJECT_STATE.md`
4. Say exactly:

```
Read CLAUDE.md and PROJECT_STATE.md completely before doing anything.

Then clone both repos:
https://github.com/Gurmindersingh27/flipforge-frontend
https://github.com/Gurmindersingh27/flipforge-backend

Confirm:
1. Both repos are loaded
2. Which branch each repo is on
3. That you can read and modify files in both

Do not make any code changes yet.
```

5. Wait for confirmation, then give one goal.
