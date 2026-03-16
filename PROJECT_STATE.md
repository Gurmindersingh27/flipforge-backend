# FlipForge — Project State
*Update this file at the end of every session. Upload alongside CLAUDE.md at the start of every session.*

---

## Last Updated
2026-03-16

---

## 1. Current Phase & Progress

**Current Phase:** Core Institutional Outputs Complete
**Next Phase:** Frontend Draft Deal Editor

### Done
- [x] Backend Day 1 complete — DraftDeal, DataPoint/Confidence models built
- [x] `/api/draft-from-url` working
- [x] `/api/finalize-and-analyze` working (stress tests, breakpoints, rehab_reality, narratives)
- [x] NarrativeGenerator fixed — accepts base metrics
- [x] Frontend MVP exists — App.tsx, AnalysisResult.tsx, api.ts, types.ts all in place
- [x] CLAUDE.md added to both repos
- [x] PROJECT_STATE.md added to both repos
- [x] Backend confirmed live on Render — https://flipforge-backend.onrender.com
- [x] `/api/health` confirmed returning 200
- [x] `/api/analyze` confirmed working in prod — returns full AnalyzeResponse including allowed_outputs
- [x] `/api/export/lender-report` confirmed returning `application/pdf` in prod
- [x] `VITE_API_BASE_URL` set to live Render URL on Vercel
- [x] Integrity Gate — `outputs_allowed()` wired into `analyze_deal()`, `allowed_outputs` serialized in AnalyzeResponse
- [x] `allowed_outputs` added to backend `AnalyzeResponse` Pydantic model (app/models.py)
- [x] `allowed_outputs` properly typed in frontend `AnalyzeResponse` interface (src/lib/types.ts) — cast hack removed
- [x] `POST /api/generate/negotiation-script` — live in production, deterministic, no LLM
- [x] `script_service.py` — uses `typed_flags[].label`, plain prose output, `seller_ask_price` optional
- [x] Frontend Negotiation Script button wired — real API call, script panel, Copy Script button
- [x] E2E validated: Analyze → allowed_outputs → Script API → UI panel → Copy

### Not Done
- [ ] CORS still showing `*` in prod response headers — needs tightening to Vercel domain only (audit before next major release)
- [ ] PDF asset path audit for production (known risk — do not touch pdf_service.py without flagging)
- [ ] Frontend Draft Deal editor UI
- [ ] `property_address` not passed to script endpoint yet — frontend sends null, address shows as "the property" in script output (wire in Draft Deal editor phase)
- [ ] `seller_ask_price` input not yet in UI — script generates without it for now (wire in Draft Deal editor phase)

### Next Session Goal
Build the Frontend Draft Deal Editor:
1. User pastes listing URL → `/api/draft-from-url` → DraftDeal fields populate
2. User fills/edits missing fields (ARV, rehab budget always missing from scrape)
3. Frontend posts DraftDeal to `/api/finalize-and-analyze`
4. Renders verdict + risk cards
5. Pass `property_address` from DraftDeal into negotiation script call

---

## 2. Repos

| Repo | GitHub | Deployed |
|------|--------|----------|
| Frontend | Gurmindersingh27/flipforge-frontend | Vercel — https://flipforge-frontend.vercel.app |
| Backend | Gurmindersingh27/flipforge-backend | Render — https://flipforge-backend.onrender.com |

Feature implemented via branch: `claude/search-negotiation-references-NzSQx`
Merged into main on 2026-03-16. Future work should start from main using a new feature branch.
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
- Negotiation Script (deterministic, copy-ready, gated by Integrity Gate)

---

## 4. Backend (Python / FastAPI)

**Stack:** FastAPI 0.115 / Uvicorn / Pydantic v2 / httpx / BeautifulSoup4 / ReportLab
**Entry point:** `app/main.py` (NOT root `main.py` — that is an older v1 setup)
**Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
**Deploy:** Render.com (`render.yaml` present in repo)

### Active API Endpoints
```
GET  /api/health
POST /api/analyze                        ← AnalyzeRequest → AnalyzeResponse (SCHEMA FROZEN)
POST /api/draft-from-url                 ← { url } → DraftFromUrlResponse
POST /api/finalize-and-analyze           ← DraftDeal → AnalyzeResponse (422 if fields missing)
POST /api/export/lender-report           ← LenderReportRequest → PDF bytes
POST /api/generate/negotiation-script    ← NegotiationScriptRequest → NegotiationScriptResponse
```

### File Structure
```
app/
  main.py                      ← FastAPI app + ALL active routes (use this)
  models.py                    ← ALL Pydantic models (canonical — sync with types.ts)
  analysis_engine.py           ← Core deal math (ACTIVE engine — do not rewrite)
  verdict_engine.py            ← outputs_allowed() Integrity Gate logic
  core/
    analysis_engine.py         ← Duplicate/older — verify which is imported before editing
    config.py                  ← Settings (DATABASE_URL, reads .env)
    scoring.py
  services/
    url_service.py             ← Scrapes listing URLs → DraftDeal
    pdf_service.py             ← Generates lender report PDF (ReportLab)
    script_service.py          ← NEW — deterministic negotiation script generator
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

### Models Added (app/models.py)
- `allowed_outputs: Optional[Dict[str, bool]]` added to `AnalyzeResponse`
- `NegotiationScriptRequest` — result: AnalyzeResponse + optional seller_ask_price, property_address, buyer_name, seller_name
- `NegotiationScriptResponse` — negotiation_script: str

### Analysis Engine Logic (analysis_engine.py)
- `compute_base_metrics()` — all core financials
- `compute_max_safe_offer()` — binary search for max purchase price at required margin
- `compute_flip/brrrr/wholesale_score()` — scoring per strategy
- `build_stress_tests()` — 5 scenarios: Base, ARV-5%, ARV-10%, Rehab+15%, Hold+2mo
- `compute_rehab_reality()` — ratio thresholds: <20% LIGHT, 20-40% MEDIUM, 40-60% HEAVY, >=60% EXTREME
- `compute_breakpoints()` — finds first stress scenario that fails
- `compute_confidence_score()` — weighted: margin strength (45%), stress robustness (30%), risk penalty (25%)
- Verdict thresholds: score >= 75 = BUY, >= 55 = CONDITIONAL, else PASS
- `outputs_allowed()` from verdict_engine.py — called after verdict, populates allowed_outputs

### PDF Export (pdf_service.py)
- Uses ReportLab (pure Python, no system deps)
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
  AnalysisResult.tsx          ← Deal analysis results + Negotiation Script panel
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
- `exportLenderReportPdf(payload)` → `POST /api/export/lender-report`
- `generateNegotiationScript(payload)` → `POST /api/generate/negotiation-script`

### Types Added (src/lib/types.ts)
- `NegotiationScriptRequest`
- `NegotiationScriptResponse`
- `allowed_outputs?: Record<string, boolean>` properly typed on `AnalyzeResponse` (cast hack removed)

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

**AnalyzeRequest schema is frozen. Do not modify it.**

| Type | Frontend | Backend |
|------|----------|---------|
| `AnalyzeRequest` | ✅ | ✅ |
| `AnalyzeResponse` | ✅ | ✅ |
| `DraftDeal` | ✅ | ✅ |
| `NegotiationScriptRequest` | ✅ | ✅ |
| `NegotiationScriptResponse` | ✅ | ✅ |

---

## 7. Known Issues

- Root `main.py` (backend) is an older v1 router setup — active app is `app/main.py`
- `app/core/analysis_engine.py` exists alongside `app/analysis_engine.py` — confirm which is imported before editing either
- CORS response headers still show `*` in production — config may not match deployed state, needs audit
- No auth system yet
- Database models exist (SQLite/SQLAlchemy) but not wired into active routes
- Zillow/Redfin block URL scraping (SOURCE_BLOCKED) — known limitation, not a bug
- PDF generation must use in-memory bytes in production — disk writes will fail on Render
- Negotiation script sends `property_address: null` — shows as "the property" until Draft Deal editor wires real address

---

## 8. How to Start a New Session

1. Upload both `CLAUDE.md` and `PROJECT_STATE.md`
2. Clone both repos if not already present
3. Confirm current branch on both repos
4. State the one goal for the session
5. No code before Plan + File Diff reviewed

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
