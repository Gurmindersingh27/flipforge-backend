# FlipForge — Project State
*Update this file at the end of every session. Upload alongside CLAUDE.md at the start of every session.*

---

## Last Updated
2026-05-10

---

## 1. Current Phase & Progress

**Current phase:** Product polished and ready for first user validation. Frontend UX complete, PDF bug fixed. Zero market contact is the primary risk.

### Done
- [x] Backend Day 1 complete — DraftDeal, DataPoint/Confidence models built
- [x] `/api/draft-from-url` working
- [x] `/api/finalize-and-analyze` working (stress tests, breakpoints, rehab_reality, narratives)
- [x] NarrativeGenerator fixed — accepts base metrics
- [x] Frontend MVP exists — App.tsx, AnalysisResult.tsx, api.ts, types.ts all in place
- [x] CLAUDE.md added to both repos
- [x] PROJECT_STATE.md added to both repos
- [x] Backend audited — requirements.txt clean, all 5 routes present, start command correct
- [x] Render backend deployed successfully
- [x] Live backend URL confirmed: https://flipforge-backend.onrender.com
- [x] GET /api/health confirmed live and returning {"status":"ok"}
- [x] POST /api/analyze confirmed working in prod
- [x] POST /api/export/lender-report confirmed returning application/pdf in prod
- [x] Full frontend → backend → PDF pipeline validated end-to-end
- [x] Draft Deal editor: assumption fields (holding_months, annual_interest_rate, loan_to_cost_pct) exposed as editable inputs
- [x] Draft Deal editor: extraction notes (draft.notes, draft.signals) displayed in panel
- [x] Frontend deployed on Vercel: https://flipforge-frontend.vercel.app
- [x] View Saved Deal — /deal/:id (read-only)
- [x] Resume UX polish (conditional header, specific 422 messaging, assumption input highlighting)
- [x] Results page clarity (max_safe_offer, confidence_score, risk flags, Integrity Gate)
- [x] Saved Deals page clarity (max_safe_offer column, verdict badges, Resume action)
- [x] Deal page clarity (max_safe_offer in header, duplicate buttons removed, allowed_outputs fixed)
- [x] Legacy Manual Analyze hidden by default behind subtle toggle link
- [x] PDF bug fix — None/None% no longer rendered for holding_months, annual_interest_rate, loan_to_cost_pct (fa30d10, app/services/pdf_service.py)
- [x] RentCast address lookup cache — SQLite-backed, 30-day TTL, provider_status Literal contract (backend PR #10, frontend PR #38)

### Not Done
- [ ] Tighten CORS from * to https://flipforge-frontend.vercel.app
- [ ] Add minimal GitHub Actions CI
  - Backend: import/startup check for FastAPI app
  - Frontend: TypeScript + build check (tsc --noEmit && vite build)
  - Goal: catch import/type errors before manual PR review
  - Not urgent, but should be done soon

### Next Session Goal
Get the product in front of a real user and capture feedback. Zero market contact is the primary risk.

---

## 2. Repos

| Repo | GitHub | Deployed |
|------|--------|----------|
| Frontend | Gurmindersingh27/flipforge-frontend | Vercel |
| Backend | Gurmindersingh27/flipforge-backend | Render.com |

Active dev branch (both repos): `claude/flipforge-execution-setup-ICNZ2`
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
    rentcast_service.py        ← RentCast enrichment + SQLite cache (30-day TTL, provider_status contract)
    analyze_service.py
    deal_service.py
    scenario_service.py
  schemas/                     ← analysis, deal, investor_profile, scenario
  api/
    deals.py
    v1/analyze.py, deals.py, profile.py, scenarios.py
  db/                          ← SQLite models (deal, user, analysis, scenario, investor_profile, rentcast_cache)
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
POST /api/enrich-address           ← { address } → EnrichAddressResponse (SQLite cache, 30d TTL)
                                      provider_status: cache_hit | live_success | quota_exhausted | provider_unavailable
```

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
| `ProviderStatus` | `"cache_hit"\|"live_success"\|"quota_exhausted"\|"provider_unavailable"` | same |

**AnalyzeRequest schema is frozen. Do not modify it.**

---

## 7. Commit History

**Frontend:**
```
c5809c2  fix: add ProviderStatus type and cache metadata fields to EnrichAddressResponse (#38)
0c2a97a  Hide Legacy Manual Analyze section by default
b744b2a  feat: deal page clarity — max offer, remove duplicate buttons, fix allowed_outputs
18fe47e  feat: saved deals page clarity
07fb928  feat: results page clarity polish
9616d4e  feat: polish Resume UX — conditional header and specific validation messaging
22d97b0  Fix hardcoded API_BASE in AnalysisResult.tsx
ad39f86  Fix meta bridge + lender report + address override
e307963  Restore UI styles
23826a4  FlipForge frontend MVP
```

**Backend:**
```
196502b  fix: add RentCast cache and provider status handling (#10)
fa30d10  fix(pdf): render None percentage fields as '—' instead of 'None%'
741c4c2  FlipForge backend MVP
```

---

## 8. Known Issues

- Root `main.py` (backend) is an older v1 router setup — active app is `app/main.py`
- `app/core/analysis_engine.py` exists alongside `app/analysis_engine.py` — confirm which is imported before editing either
- `app/core/config.py` imports pydantic-settings but is dead code — not in active import chain, do not add pydantic-settings to requirements.txt
- CORS is wide open (`*`) — needs tightening to Vercel domain before production hardening
- No auth system yet
- Database models exist (SQLite/SQLAlchemy) but may not be wired into active routes
- Zillow/Redfin block URL scraping (SOURCE_BLOCKED) — known limitation, not a bug
- PDF generation must use in-memory bytes in production — disk writes will fail on Render
- Render free tier cold starts — first request after inactivity may take 50+ seconds
- No GitHub Actions CI — import/type errors are only caught at review time

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

---

## Session 2026-05-09: Fixed Finalize & Analyze Button Visibility

**Bug:** Finalize & Analyze button in Address draft flow appeared dark/invisible when enabled

**Root cause:** Vite boilerplate leftover in src/index.css:
button { background-color: #1a1a1a; }

Un-layered global CSS overrides Tailwind v4 @layer utilities, so
bg-[#E8C547] class was present but not rendering.

**Diagnostic method:** Added runtime panel (PR #35) showing:
- canFinalize: true
- btn.disabled: false
- computed.backgroundColor: rgb(26,26,26) (not gold)

Proved CSS override, not state/logic bug.

**Fixes:**
- PR #34: RentCast estimate Number() coercion (arv/rent stored as numbers)
- PR #36: Removed global button rules from index.css
- PR #37: Cleanup diagnostic code (PR #35 accidentally merged)

**Verified working:**
- Address lookup → draft → Finalize & Analyze = bright gold, clickable
- Resume Deal → same gold button
- Diagnostic panel removed from production

**Known cosmetic issue (not blocking):**
Legacy manual analyzer "Analyze Deal" button still gray - separate
styling, not same bug. Can be updated separately if desired.

**Lesson:** Runtime inspection > source speculation. Diagnostic panel
exposed real bug in 5 min after 45 min of symptom-chasing.

**Note:** RentCast quota exhausted (50 calls/month free tier).
Future: add caching/rate-limit.

---

## Session 2026-05-10 — RentCast caching / quota protection

**Goal:** Prevent repeated RentCast API calls and protect quota during demos and early usage.

**Implementation (backend PR #10, frontend PR #38):**
- New `rentcast_cache` SQLite table — auto-created by `init_db()` on startup for the current SQLite-backed environment
- Cache key: `address.strip().lower()` with whitespace collapsed — stable, no external hash
- TTL: 30 days — checked on read
- `enrich_address()` accepts optional `db: Session` — cache-aware when injected, safe without
- `enrich_address_endpoint` injects `db: Session = Depends(get_db)`
- Cache write only on `provider_status = "live_success"`

**provider_status Literal contract:**
- `cache_hit` — returned from SQLite cache within TTL
- `live_success` — RentCast responded; result written to cache
- `quota_exhausted` — RentCast returned 429; empty signals returned, not cached
- `provider_unavailable` — any other RentCast/network failure; empty signals returned, not cached

**Guardrails confirmed:**
- `analysis_engine.py` untouched
- `AnalyzeRequest` untouched
- No new pip dependencies
- Manual entry flow remains available when RentCast fails (200 + empty signals + provider_status)
- Failed/quota responses are never written to cache

**Quota note:**
RentCast quota is currently exhausted. Do not run live `/api/enrich-address` tests without explicit approval.
Cache will serve repeat lookups from SQLite once quota resets and first live call succeeds.

**Deploy:**
- Backend: Render.com auto-deploy triggered on main merge (commit 196502b)
- Frontend: Vercel auto-deploy triggered on main merge (commit c5809c2)
