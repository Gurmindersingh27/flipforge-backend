# FlipForge — Project State
*Update this file at the end of every session. Upload alongside CLAUDE.md at the start of every session.*

---

## Last Updated
2026-06-04

---

## 1. Current Phase & Progress

**Product direction:** Upload the house. Know the rehab. Know the offer.

**Short pitch:** FlipForge helps real estate investors decide if a property is worth flipping before they waste time, money, or emotion on it. Upload photos, estimate rehab, stress-test the deal, and know your max safe offer before chasing the property.

**Current core loop:**
1. Enter property or deal info.
2. Upload property photos.
3. Photo Rehab Analyzer estimates visible condition and rehab range.
4. User applies mid rehab estimate into the deal.
5. Existing underwriting engine calculates max safe offer, risk, verdict, and investor outputs.
6. User decides whether to offer, negotiate, verify, or walk away.

**Current product state:**
- Photo Rehab Analyzer v1 backend shipped (PR #13, merged, commit f39b1db).
- Backend deployed on Render. Anthropic package installed. uvicorn running.
- ANTHROPIC_API_KEY and ANTHROPIC_MODEL=claude-sonnet-4-5 set in Render production.
- PHOTO_REHAB_DEV_STUB is NOT set in production.
- Real Anthropic vision call not yet manually QA'd in browser — next session is QA only.

### Done
- [x] Backend Day 1 complete — DraftDeal, DataPoint/Confidence models built
- [x] `/api/draft-from-url` working
- [x] `/api/finalize-and-analyze` working (stress tests, breakpoints, rehab_reality, narratives)
- [x] NarrativeGenerator fixed — accepts base metrics
- [x] Backend audited — requirements.txt clean, all routes present, start command correct
- [x] Render backend deployed successfully
- [x] Live backend URL confirmed: https://flipforge-backend.onrender.com
- [x] GET /api/health confirmed live and returning {"status":"ok"}
- [x] POST /api/analyze confirmed working in prod
- [x] POST /api/export/lender-report confirmed returning application/pdf in prod
- [x] Full frontend → backend → PDF pipeline validated end-to-end
- [x] PDF bug fix — None/None% no longer rendered for holding_months, annual_interest_rate, loan_to_cost_pct (fa30d10)
- [x] RentCast address lookup cache — SQLite-backed, 30-day TTL, provider_status Literal contract (PR #10)
- [x] Verdict hard-fail fix — backend PR #11 (merge commit 14d4dd4)
  - overall_verdict now hard-fails to PASS when net_profit <= 0 AND purchase_price > max_safe_offer
  - app/analysis_engine.py only. No schema/model/route changes.
  - Confirmed live in production.
- [x] Offer Gap QA complete — all three callout states verified in production
- [x] Photo Rehab Analyzer v1 — backend PR #13 (merge commit f39b1db)
  - New endpoint: POST /api/photo-rehab-analysis (multipart/form-data)
  - New service: app/services/photo_rehab_service.py (Anthropic vision AI call)
  - New pricing module: app/services/rehab_pricing.py (controlled SE US contractor pricing)
  - New Pydantic models in app/models.py: RoomFinding, RehabItem, PhotoRehabRiskFlag, PhotoRehabTotals, PhotoRehabAnalysisResponse
  - New dependencies in requirements.txt: python-multipart>=0.0.9, anthropic>=0.40.0
  - AI identifies condition/severity only. Backend pricing controls all dollar estimates.
  - Photos processed in memory only — never stored.
  - Validation: 1-8 photos, max 3MB each, JPEG/PNG/WEBP only
  - Totals semantics: subtotal_low/mid/high = before contingency; low/mid/high = after contingency
  - Dev stub activated ONLY when PHOTO_REHAB_DEV_STUB=true — NOT set in production
  - analysis_engine.py untouched. AnalyzeRequest unchanged. AnalyzeResponse unchanged.
  - Render deploy confirmed: anthropic installed, python-multipart installed, uvicorn running.

### Not Done / Blocked
- [ ] **Photo Rehab Analyzer live browser QA — MUST COMPLETE BEFORE NEXT FEATURE**
  - Real Anthropic call not yet QA'd in browser
  - See Next Session Goal for full QA checklist
- [ ] Tighten CORS from * to https://flipforge-frontend.vercel.app
- [ ] Add minimal GitHub Actions CI
  - Backend: import/startup check for FastAPI app
  - Frontend: TypeScript + build check
  - Not urgent, but should be done soon

### Next Session Goal
**Photo Rehab Analyzer QA + Deploy Verification**

Goal: Verify the live loop — upload photo → get rehab estimate → apply mid rehab → run deal analysis.

QA checklist:
- [ ] Confirm backend health endpoint: GET /api/health → {"status":"ok"}
- [ ] Confirm PHOTO_REHAB_DEV_STUB is NOT set in Render production
- [ ] Confirm ANTHROPIC_API_KEY is set in Render (without revealing value)
- [ ] Confirm ANTHROPIC_MODEL is claude-sonnet-4-5
- [ ] Upload 1-2 small real property photos via frontend
- [ ] Confirm provider_status is live_success
- [ ] Confirm rehab estimate displays (condition badge, cost totals, contingency)
- [ ] Confirm missing_photo_warnings display
- [ ] Confirm risk_flags display
- [ ] Confirm disclaimer displays
- [ ] Confirm "Use Mid as Rehab Budget" updates the rehab budget field
- [ ] Confirm running analysis uses the applied rehab budget
- [ ] Test both Draft/Resume flow and Legacy Manual Analyze flow
- [ ] Test invalid upload cases: too many photos, file over 3MB, unsupported file type

Do not start the next feature until this QA checklist passes.

---

## 2. Repos

| Repo | GitHub | Deployed |
|------|--------|----------|
| Frontend | Gurmindersingh27/flipforge-frontend | Vercel |
| Backend | Gurmindersingh27/flipforge-backend | Render.com |

No active dev branch. Work on named feature branches; never push to main directly.

---

## 3. What This App Does

FlipForge is a risk-first real estate deal underwriting tool for serious investors.

**The core loop:** Upload the house → know the rehab → know the offer.

The investor enters (or pastes a listing URL for) a property, optionally uploads photos for AI rehab estimation, and gets:
- Photo Rehab Analyzer — upload property photos, AI estimates visible rehab scope and cost range
- Net profit, ROI, profit margin
- Flip / BRRRR / Wholesale scores and verdicts (BUY / CONDITIONAL / PASS)
- Max Safe Offer (MAO)
- Offer Gap callout comparing offer vs MAO (Overpay Risk / Offer Gap / Offer Cushion)
- Repair Budget Builder — manual line-item rehab estimator
- Rehab Reality classification (LIGHT / MEDIUM / HEAVY / EXTREME)
- Stress test scenarios (ARV -5%, ARV -10%, Rehab +15%, Hold +2mo)
- Risk flags, breakpoints, confidence score
- "Why this verdict" rationale
- Lender report PDF export

---

## 4. Backend (Python / FastAPI)

**Stack:** FastAPI 0.115 / Uvicorn / Pydantic v2 / httpx / BeautifulSoup4 / ReportLab / anthropic / python-multipart
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
python-multipart>=0.0.9
anthropic>=0.40.0
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
    photo_rehab_service.py     ← Photo rehab analysis (Anthropic vision AI + rehab_pricing.py)
    rehab_pricing.py           ← Controlled SE US contractor pricing constants (flat/per_sqft/per_bath)
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
GET  /api/health                        ← confirmed live in prod
POST /api/analyze                       ← AnalyzeRequest → AnalyzeResponse (SCHEMA FROZEN)
POST /api/draft-from-url                ← { url } → DraftFromUrlResponse
POST /api/finalize-and-analyze          ← DraftDeal → AnalyzeResponse (422 if fields missing)
POST /api/export/lender-report          ← LenderReportRequest → PDF bytes
POST /api/enrich-address                ← { address } → EnrichAddressResponse (SQLite cache, 30d TTL)
                                           provider_status: cache_hit | live_success | quota_exhausted | provider_unavailable
POST /api/photo-rehab-analysis          ← multipart/form-data (photos + optional sqft/region/property_type/user_notes)
                                           → PhotoRehabAnalysisResponse
                                           Validation: 1-8 photos, max 3MB each, JPEG/PNG/WEBP only
                                           provider_status: live_success | ai_not_configured | ai_error | dev_stub
```

### Analysis Engine Logic (analysis_engine.py)
- `compute_base_metrics()` — all core financials
- `compute_max_safe_offer()` — binary search for max purchase price at required margin
- `compute_flip/brrrr/wholesale_score()` — scoring per strategy
- `build_stress_tests()` — 5 scenarios: Base, ARV-5%, ARV-10%, Rehab+15%, Hold+2mo
- `compute_rehab_reality()` — ratio thresholds: <20% LIGHT, 20-40% MEDIUM, 40-60% HEAVY, >=60% EXTREME
- `compute_breakpoints()` — finds first stress scenario that fails
- `compute_confidence_score()` — weighted: margin strength (45%), stress robustness (30%), risk penalty (25%)
- `build_notes()` — produces 2–3 human-readable rationale strings surfaced in frontend "Why this verdict"
- Verdict thresholds: score >= 75 = BUY, >= 55 = CONDITIONAL, else PASS

### Photo Rehab Analyzer (photo_rehab_service.py + rehab_pricing.py)
- `analyze_photos()` — main entry point, called from POST /api/photo-rehab-analysis
- Sends base64-encoded images to Anthropic Claude vision model
- AI identifies condition/severity per category — AI never invents dollar amounts
- `rehab_pricing.py` maps (category, severity) to controlled dollar ranges
- 9 pricing categories: kitchen, bathrooms, flooring, paint_drywall, roof, hvac, electrical, plumbing, windows_exterior
- Pricing types: flat, per_sqft (needs sqft), per_bath (uses bathroom count from AI)
- Contingency: confidence <50 → 20%, 50-75 → 15%, >75 → 10%
- Totals: subtotal_low/mid/high = pre-contingency; low/mid/high = post-contingency
- Dev stub: activated ONLY when PHOTO_REHAB_DEV_STUB=true — NOT set in production
- Env vars: ANTHROPIC_API_KEY (required for live_success), ANTHROPIC_MODEL (default: claude-sonnet-4-5)

### URL Scraping (url_service.py)
- httpx fetch with browser User-Agent
- Returns SOURCE_BLOCKED on 403/429 (Zillow/Redfin block this — known, not a bug)
- ARV and rehab_budget are ALWAYS missing — investor must fill manually
- Only purchase_price can realistically be scraped

### PDF Export (pdf_service.py)
- Uses ReportLab (pure Python, no system deps)
- ⚠️ Production risk: must use in-memory bytes (StreamingResponse), no disk writes

### RentCast Enrichment (rentcast_service.py)
- SQLite cache, 30-day TTL, cache key = normalized address
- provider_status contract: cache_hit | live_success | quota_exhausted | provider_unavailable
- RentCast quota may be exhausted — do not run live tests without explicit approval

---

## 5. Shared Data Contract

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
| `PhotoRehabCondition` | `"light"\|"medium"\|"heavy"\|"unknown"` | same |
| `PhotoRehabProviderStatus` | `"live_success"\|"ai_not_configured"\|"ai_error"\|"dev_stub"` | same |
| `PhotoRehabAnalysisResponse` | ✅ | ✅ |
| `RoomFinding` | ✅ | ✅ |
| `RehabItem` | ✅ | ✅ |
| `PhotoRehabRiskFlag` | ✅ | ✅ |
| `PhotoRehabTotals` | ✅ | ✅ |

**AnalyzeRequest schema is frozen. Do not modify it.**

---

## 6. Commit History

**Backend:**
```
f39b1db  Merge pull request #13 — feat: photo rehab analyzer backend v1
14d4dd4  Merge pull request #11 — fix: hard-fail overall_verdict to PASS
2a6d8b0  fix: hard-fail overall_verdict to PASS when net_profit <= 0 and purchase_price > max_safe_offer
036f36e  docs: session closeout 2026-05-10 — deal-memo polish (frontend-only)
0eacb12  docs: update PROJECT_STATE.md and CLAUDE.md for 2026-05-10 session closeout
196502b  fix: add RentCast cache and provider status handling (#10)
fa30d10  fix(pdf): render None percentage fields as '—' instead of 'None%'
741c4c2  FlipForge backend MVP
```

**Frontend:**
```
370f5f2  feat: add photo rehab analyzer frontend (PR #42)
3a2b600  fix: show repair budget builder in legacy manual analyzer (#41)
07654861 feat: result screen deal-memo polish — offer gap callout + verdict rationale (#40)
a46dda8  Merge pull request #39 — feat: add Repair Budget Builder
c5809c2  fix: add ProviderStatus type and cache metadata fields to EnrichAddressResponse (#38)
23826a4  FlipForge frontend MVP
```

---

## 7. Known Issues

- Root `main.py` is an older v1 router setup — active app is `app/main.py`
- `app/core/analysis_engine.py` exists alongside `app/analysis_engine.py` — confirm which is imported before editing either
- `app/core/config.py` imports pydantic-settings but is dead code — not in active import chain
- CORS is wide open (`*`) — needs tightening to Vercel domain before production hardening
- Zillow/Redfin block URL scraping (SOURCE_BLOCKED) — known limitation, not a bug
- PDF generation must use in-memory bytes in production — disk writes will fail on Render
- Render free tier cold starts — first request after inactivity may take 50+ seconds
- No GitHub Actions CI — import/type errors are only caught at review time
- RentCast quota may be exhausted — do not run live /api/enrich-address without explicit approval
- **Photo Rehab Analyzer — real Anthropic call not yet manually QA'd in browser**
- **Photo Rehab Analyzer — API cost exists per photo analysis call (Anthropic charges per token)**
- **Photo Rehab Analyzer — cold start + AI call may be slow on first request**
- **Photo Rehab Analyzer — results are AI-assisted planning estimates, not contractor bids**
- **Photo Rehab Analyzer — unknown/invisible systems (roof, HVAC, electrical, plumbing) generate warnings, not pricing**

---

## 8. Feature Backlog

**Do not start any of these until Photo Rehab Analyzer live QA passes.**

### Next Features To Add After Photo Rehab QA

**Priority 1 — Deal Killer Summary**
- Prominent top-of-results section in plain English.
- Explains what kills this specific deal (asking price above MAO, rehab too high, ARV too sensitive, unknown major systems, stress test failure).

**Priority 2 — Investor Action Plan**
- After each analysis, show next steps tailored to the result.
- Examples: offer no more than $X, verify major systems, request access, use negotiation script.

**Priority 3 — Copyable Investor Summary**
- One-click copy of property basics, rehab estimate, max safe offer, verdict, deal killers, risk warnings, next action.

**Priority 4 — Lender / Investor Report Polish**
- Make PDF/report feel lender-grade. Include photo rehab summary once QA confirms it works.

**Priority 5 — Comps / ARV Confidence Engine**
- Data-agnostic comp analysis, ARV bands, confidence scoring, outlier handling.

**Priority 6 — Title / Lien / Auction Risk Engine**
- Tax delinquency, HOA/municipal liens, judgments/mechanic liens, preforeclosure, scheduled auction, probate/estate, code violations.

**Priority 7 — Deal Alert Engine** *(later)*
- Scan listings against buy box. Alert users to possible deals.

**Priority 8 — Saved Deals / Deal Memory** *(later)*
- Better history, track photo rehab results, track user decisions.

**Priority 9 — Contractor Marketplace** *(later, not now)*
**Priority 10 — Learning Brain** *(later, not now)*
**Priority 11 — Social / Investor Profiles** *(later, not now)*

**Parked: AIM — Asset Intelligence Modules**
- Cars, furniture, equipment, non-real-estate assets. Do not build until real estate MVP is validated.

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

## Session 2026-05-09 — Finalize & Analyze button visibility fixed

**Root cause:** Vite boilerplate `button { background-color: #1a1a1a; }` in src/index.css overrode Tailwind v4 utilities.
**Fixes:** PR #34 (Number() coercion), PR #36 (remove global button rules), PR #37 (remove diagnostic panel).
**Note:** RentCast quota exhausted during debugging.

---

## Session 2026-05-10 — RentCast caching / quota protection

**PR:** backend #10 (commit 196502b), frontend #38 (commit c5809c2)
- SQLite cache, 30-day TTL. provider_status: cache_hit | live_success | quota_exhausted | provider_unavailable.
- Cache write only on live_success.

---

## Session 2026-05-10 — Result screen deal-memo polish (frontend-only)

**PR:** frontend #40 (commit 07654861) — src/AnalysisResult.tsx only. No backend changes.

---

## Session 2026-05-11 — Wire RepairBudgetBuilder into Legacy Manual Analyze (frontend-only)

**PR:** frontend #41 (commit 3a2b600) — src/App.tsx only. No backend changes.

---

## Session 2026-05-11 — Backend verdict hard-fail fix

**PR:** backend #11 (merge commit 14d4dd4) — app/analysis_engine.py only.
- overall_verdict hard-fails to PASS when net_profit <= 0 AND purchase_price > max_safe_offer.
- Confirmed live in production.

---

## Production QA — Offer Gap callout (completed 2026-05-11)

All three states verified in production.
- Red: purchase 220k, ARV 300k, rehab 50k → PASS verdict, Integrity Gate active.
- Amber: purchase 176k, ARV 300k, rehab 50k → Offer Gap amber.
- Green: purchase 160k, ARV 300k, rehab 50k → Offer Cushion green, PDF + script available.

---

## Session 2026-06-04 — Photo Rehab Analyzer v1

**Backend PR:** #13 (merged, commit f39b1db)

**What was added:**
- `POST /api/photo-rehab-analysis` endpoint (multipart/form-data)
- `app/services/photo_rehab_service.py` — Anthropic vision AI call, response normalization, dev stub
- `app/services/rehab_pricing.py` — controlled SE US contractor pricing constants
- New Pydantic models in `app/models.py`: RoomFinding, RehabItem, PhotoRehabRiskFlag, PhotoRehabTotals, PhotoRehabAnalysisResponse
- New dependencies: `python-multipart>=0.0.9`, `anthropic>=0.40.0`

**What was NOT changed:**
- analysis_engine.py — untouched
- AnalyzeRequest — unchanged
- AnalyzeResponse — unchanged
- All existing routes — unchanged

**Render production env vars:**
- ANTHROPIC_API_KEY: set (hidden)
- ANTHROPIC_MODEL: claude-sonnet-4-5
- PHOTO_REHAB_DEV_STUB: NOT set (do not add)

**Deploy verification:**
- Render build logs confirmed: anthropic installed, python-multipart installed, uvicorn running.
- Dev-stub smoke test passed: response shape valid, validation working, totals semantics correct.

**Known risks going into QA:**
- Real Anthropic call not yet browser-tested
- API cost per photo analysis call (Anthropic charges per token)
- Cold start + AI processing may be slow on first request
- Results are planning estimates, not contractor bids
- Unknown major systems generate inspection warnings, not fake pricing
