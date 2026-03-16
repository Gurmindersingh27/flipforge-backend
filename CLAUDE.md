# FlipForge — CLAUDE.md
*Read this file before doing anything. This is the source of truth.*

---

## Non-Negotiable Rules

1. Do not rename or change any existing schema fields, API paths, or response shapes. Additive changes only.
2. Do not refactor folder structure or "clean up" code. Keep files where they are.
3. Before any code changes, output a Plan + File Diff List (files you'll touch + why). Wait for approval.
4. Deployment first. No UI work until backend is live and Vercel can call it successfully.
5. Code is source of truth. If this doc conflicts with actual source files, follow the code.
6. Do not add dependencies without explicit approval.
7. Do not push to main directly. Work on a feature branch, let the PM review the diff first.
8. Any type change must be made in BOTH app/models.py (backend) AND src/lib/types.ts (frontend) simultaneously.

---

## What FlipForge Is

A risk-first real estate deal underwriting tool. Not a cashflow calculator — a "where does this deal break?" engine.

**Core user flow:**
1. Paste listing URL → backend returns best-effort DraftDeal
2. User fills missing fields in Draft Deal editor
3. Frontend posts to `/api/finalize-and-analyze`
4. Backend returns full AnalyzeResponse (verdict, risk cards, stress tests, breakpoints, rehab reality, narratives)
5. User downloads lender PDF report

**Target users:** Serious investors, hard money lenders, BRRRR operators, acquisition managers. Not beginners.

---

## Two Repos — Both Required

| Repo | Remote | Status |
|------|--------|--------|
| Frontend | github.com/Gurmindersingh27/flipforge-frontend | Public |
| Backend | github.com/Gurmindersingh27/flipforge-backend | Public |

To start a session:
```
git clone https://github.com/Gurmindersingh27/flipforge-frontend
git clone https://github.com/Gurmindersingh27/flipforge-backend
```

---

## Frontend (React + TypeScript + Vite)

**Stack:** React 19 / TypeScript ~5.9 / Vite 7 / No UI library / Vanilla CSS

**Source files:**
```
src/
  main.tsx                    # App entry point
  App.tsx                     # Root component
  App.css / index.css         # Global styles
  config.ts                   # API_BASE_URL (reads VITE_API_BASE_URL, fallback http://127.0.0.1:8000)
  shield.ts                   # Shield logic
  AnalysisResult.tsx          # Deal analysis display
  components/
    ShieldHeader.tsx          # Header component
  lib/
    api.ts                    # All fetch calls to backend — DO NOT restructure
    types.ts                  # All shared TypeScript types — canonical contract
  assets/
    react.svg
```

**API calls (src/lib/api.ts):**
- `POST /api/analyze` → `analyzeDeal()`
- `POST /api/draft-from-url` → `draftFromUrl()`
- `POST /api/finalize-and-analyze` → `finalizeAndAnalyze()` (handles 422 missing_fields)
- `POST /api/export/lender-report` → PDF export
- `POST /api/generate/negotiation-script` → `generateNegotiationScript()`

**Key types (src/lib/types.ts):**
- `AnalyzeRequest` / `AnalyzeResponse`
- `DraftDeal` with `DataPoint<T>` confidence metadata
- `DraftFromUrlResponse` — wrapper `{ draft: DraftDeal }`
- `Verdict`: `"BUY" | "CONDITIONAL" | "PASS"`
- `Strategy`: `"flip" | "brrrr" | "wholesale"`
- `RehabReality`, `Breakpoints`, `RiskFlag`, `StressTestScenario`

**NPM scripts:**
```
npm run dev       # Vite dev server
npm run build     # tsc + vite build
npm run lint      # ESLint
npm run preview   # Preview production build
```

---

## Backend (Python + FastAPI)

**Stack:** FastAPI 0.115 / Uvicorn / Pydantic v2 / httpx / BeautifulSoup4 / ReportLab / SQLite (dev)

**Entry point:** `app/main.py`

**Source structure:**
```
app/
  main.py                      # FastAPI app, all route definitions
  models.py                    # Pydantic models — canonical contract, mirrors types.ts
  analysis_engine.py           # Core deal math (DO NOT REWRITE)
  core/
    analysis_engine.py         # Also exists — confirm which is active before touching
    config.py                  # Settings (DATABASE_URL via pydantic-settings)
    scoring.py
  services/
    url_service.py             # URL scraping → DraftDeal
    pdf_service.py             # Lender report PDF generation (production risk — see below)
    script_service.py          # deterministic negotiation script generator
    analyze_service.py
    deal_service.py
    scenario_service.py
  schemas/                     # analysis, deal, investor_profile, scenario
  api/
    deals.py
    v1/
      analyze.py
      deals.py
      profile.py
      scenarios.py
  db/                          # SQLite models (deal, user, analysis, scenario, investor_profile)
render.yaml                    # Render.com deployment config
requirements.txt
```

**Live API endpoints:**
```
GET  /api/health
POST /api/analyze                  # Core deal analysis — AnalyzeRequest schema is FROZEN
POST /api/draft-from-url           # Scrape listing URL → DraftDeal
POST /api/finalize-and-analyze     # DraftDeal → AnalyzeResponse (422 if fields missing)
POST /api/export/lender-report     # AnalyzeResponse → PDF bytes
POST /api/generate/negotiation-script  # NegotiationScriptRequest → NegotiationScriptResponse
```

**Run locally:**
```
uvicorn app.main:app --reload
```

---

## Shared Data Contract

Any change to these types must be made in BOTH files simultaneously:

| Type | Frontend | Backend |
|------|----------|---------|
| `AnalyzeRequest` | src/lib/types.ts | app/models.py |
| `AnalyzeResponse` | src/lib/types.ts | app/models.py |
| `DraftDeal` | src/lib/types.ts | app/models.py |
| `DataPoint<T>` | src/lib/types.ts | app/models.py |

**AnalyzeRequest schema is frozen. Do not touch it.**

---

## Known Production Risk — PDF Export

`pdf_service.py` must:
- Use **in-memory bytes** (`StreamingResponse`) — no writing to disk
- Handle asset paths (fonts, logos) correctly for cloud deployment
- This is the #1 "works locally, dies in prod" failure point

Do not touch pdf_service.py without explicitly flagging this risk first.

---

## Deployment State

- **Backend:** Render.com (render.yaml present in repo)
- **Frontend:** Vercel (linked to GitHub)
- **CORS:** Currently `allow_origins=["*"]` — tighten to Vercel domain once deployed
- **Env var:** Frontend reads `VITE_API_BASE_URL` — must be set to live Render URL on Vercel

---

## Commit History

**Frontend:**
```
22d97b0  Fix hardcoded API_BASE in AnalysisResult.tsx
ad39f86  Fix meta bridge + lender report + address override
e307963  Restore UI styles
23826a4  FlipForge frontend MVP
```

**Backend:**
```
741c4c2  FlipForge backend MVP
```

---

## Session Start Checklist

- [ ] Read this file completely before touching any code
- [ ] Clone both repos if not already present
- [ ] Confirm current goal with PM before starting
- [ ] Output Plan + File Diff List before writing any code
- [ ] Wait for approval before executing
