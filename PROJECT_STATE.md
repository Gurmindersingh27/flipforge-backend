# FlipForge — Project State
*Update this file at the end of every session. Upload alongside CLAUDE.md at the start of every session.*

---

## Last Updated
2026-07-31

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
- Photo Rehab Analyzer v1 backend shipped and QA-verified in production (PR #13, merged, commit f39b1db).
- Live browser QA complete — core loop validated end-to-end.
- Live Anthropic vision call confirmed working in production (provider_status: live_success).
- ANTHROPIC_API_KEY and ANTHROPIC_MODEL=claude-sonnet-4-5 set in Render production.
- PHOTO_REHAB_DEV_STUB is NOT set in production.
- Deal Killer Summary v1 shipped (frontend PR #45, merged) — frontend-only, no backend changes.
- **Active phase: Demo Conversion Readiness.** Frontend is the primary current source of truth (see frontend PROJECT_STATE.md); backend is unchanged since Photo Rehab Analyzer v1.
- **Lender demo integrity fixes (frontend PRs #56 + #57, merged 2026-07-31) — frontend-only, NO backend changes.** These aligned the frontend to existing backend behavior; they did not modify any backend source, model, schema, `AnalyzeRequest`, API contract, or `analysis_engine.py`.
  - LTC display (PR #56): the frontend now displays the actual LTC used by underwriting — the draft's submitted `loan_to_cost_pct` in the draft flow, and the backend's existing 90% default (`app/models.py` `loan_to_cost_pct = 0.90`) in the manual flow. The 90% default is unchanged and remains the backend source of truth.
  - Non-positive rent (PR #57): the frontend now normalizes blank/zero/negative `est_monthly_rent` to omitted (`null`) before calling `/api/analyze` and `/api/finalize-and-analyze`. This matches the backend's existing rent semantics — `est_monthly_rent is None` means "no rent" (BRRRR read limited, no rent-to-cost flag), while a non-`None` `0.0` was previously treated as a real $0 rent. Backend rent logic in `app/analysis_engine.py` is unchanged and remains the source of truth.

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
- [x] Photo Rehab Analyzer live browser QA — COMPLETE (2026-06-04)
  - Manual Analyze Legacy flow: 1 photo uploaded, live_success, estimate displayed, mid applied, Analyze Deal ran successfully
  - Draft/Resume flow: URL draft opened, photo uploaded, live_success, mid applied into draft rehab_budget, Finalize & Analyze ran successfully
  - Invalid upload: PDF blocked at file-picker, 12 photos triggered frontend validation ("Maximum 8 photos. You selected 12.")
  - Oversized file not tested (no >3MB test file available — not a blocker)
  - Core loop validated: upload photo → estimate rehab → apply mid → run underwriting
- [x] Deal Killer Summary v1 — frontend PR #45 (merged, no backend changes)
  - Frontend-only feature. No backend files, schemas, models, or analysis_engine.py touched.
  - Visual QA not yet done — next session goal.

### Not Done / Blocked
- [ ] Tighten CORS from * to https://flipforge-frontend.vercel.app
  - Note: code in app/main.py is already tightened to Vercel domain; CLAUDE.md docs are stale on this point
- [ ] Add minimal GitHub Actions CI
  - Backend: import/startup check for FastAPI app
  - Frontend: TypeScript + build check
  - Not urgent, but should be done soon

### Next Session Goal
**Deal Killer Summary visual QA in production/preview**

- Test PASS, CONDITIONAL, and BUY verdict cases in browser
- Check spacing and mobile layout
- No backend work expected for this session
- Which existing backend fields power it? (max_safe_offer, net_profit, risk_flags, stress_tests, breakpoints)
- Does it require any new backend fields or just frontend logic?

No implementation is approved yet. Scope first, then get PM approval before writing code.

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
b96c13f  chore: replace em dashes with ASCII hyphens in comments (PR #45)
c213c1b  feat: add Deal Killer Summary v1 to result screen (PR #45)
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
- **Photo Rehab Analyzer — estimate variability observed across repeated calls on same image**
  - Same photo produced Medium vs Heavy classifications across calls
  - Mid estimate range observed ~$24K–$52K on the same image
  - Root cause: non-deterministic LLM classification without temperature=0 enforcement
  - Not a current blocker; flagged for future prompt/consistency tuning
- **Photo Rehab Analyzer — API cost exists per photo analysis call (Anthropic charges per token)**
- **Photo Rehab Analyzer — cold start + AI call may be slow on first request**
- **Photo Rehab Analyzer — results are AI-assisted planning estimates, not contractor bids**
- **Photo Rehab Analyzer — unknown/invisible systems (roof, HVAC, electrical, plumbing) generate warnings, not pricing**

---

## 8. Feature Backlog

**Photo Rehab Analyzer live QA is complete. Next feature requires PM scope approval before any code.**

### Next Features To Add (in priority order)

**Priority 1 — Deal Killer Summary** *(merged frontend PR #45 — awaiting visual QA)*
- Shipped frontend-only. No backend changes required or made.
- Visual QA (PASS / CONDITIONAL / BUY cases, spacing, mobile) is the next session goal.

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

---

## Production QA — Photo Rehab Analyzer (completed 2026-06-04)

**Result: PASS — core product loop validated end-to-end in production.**

**Manual Analyze Legacy flow:**
- 1 real property photo uploaded via https://flipforge-frontend.vercel.app
- provider_status: live_success (confirmed — real Anthropic vision call to /api/photo-rehab-analysis)
- Rehab estimate returned: overall condition, confidence score, low/mid/high totals, contingency %, missing photo warnings, risk flags, disclaimer
- "Use Mid as Rehab Budget" applied mid value into rehab_budget field
- Analyze Deal ran successfully using the applied rehab budget

**Draft/Resume flow:**
- URL draft flow opened
- Photo Rehab Analyzer appeared in draft flow
- 1 real property photo uploaded
- provider_status: live_success
- Mid rehab value applied into draft rehab_budget DataPoint field
- Finalize & Analyze ran successfully afterward

**Invalid upload checks:**
- PDF file: blocked at file-picker level (browser MIME filter — never reached backend)
- 12 photos: frontend validation triggered — "Maximum 8 photos. You selected 12." (never reached backend)
- Oversized file: not tested — no >3MB test file available (not a blocker; backend 422 guard is in place)

**Observation — estimate variability:**
- Same photo produced different condition classifications across calls (Medium vs Heavy)
- Mid estimate range ~$24K–$52K observed on the same image across calls
- Root cause: non-deterministic LLM behavior without temperature=0 enforcement
- Flagged for future prompt/consistency tuning — not a current blocker

**QA conclusion:** POST /api/photo-rehab-analysis is production-ready. Feature is ready for demo.
**Next step:** Scope Deal Killer Summary before any implementation.

---

## Session 2026-06-04 — Deal Killer Summary v1 (frontend-only)

**Frontend PR:** #45 (merged) — no backend files changed in this session.

- New frontend component: src/components/DealKillerSummary.tsx
- Updated: src/AnalysisResult.tsx (import + JSX placement only)
- No backend files touched. No schema changes. No analysis_engine.py changes. No RentCast or Anthropic calls.
- Build passed. Visual QA not yet done — next session goal.

---

## Session 2026-07-31 — Lender demo integrity fixes (frontend-only)

**Frontend PRs:** #56 (LTC display, source 8e5e710, merge 6f0b01c) and #57 (non-positive rent, source 7c643d7, merge fc0aab5). **No backend files changed.**

- Both fixes are frontend-only and align the UI to existing backend behavior. No backend source, model, schema, `AnalyzeRequest`, API contract, or `analysis_engine.py` changes; no dependencies; no scoring/confidence/risk-logic changes.
- LTC display (PR #56): frontend now shows the underwriting LTC — draft's submitted `loan_to_cost_pct` in the draft flow, the backend's existing 90% default in manual. Misleading manual LTC input removed. Backend 90% default unchanged.
- Non-positive rent (PR #57): frontend normalizes blank/zero/negative rent to `null` (omitted) in manual `/api/analyze`, manual memo/PDF metadata, and draft/finalize (shallow copy; draft state not mutated). This matches the backend's `None`-vs-not-`None` rent semantics; a `0` was previously sent as a real $0 rent and produced a false `Weak rent-to-cost for BRRRR` flag and "Rent provided" note. Backend rent logic unchanged.
- Production QA passed against the locked demo set (obvious PASS $185K/$240K/$45K; corrected-offer BUY $135K/$240K/$45K with LTC 90%; clean BUY $200K/$345K/$50K with rent 0 omitted → BUY, confidence 93, MSO $212,300, net profit $50,150, 17.0% margin, 34.0% ROI, all stress BUY, no false rent flag). See frontend PROJECT_STATE.md for full detail — frontend is the primary source of truth.
- Active phase: Demo Conversion Readiness. No backend work is approved.

## 2026-09-10 — Rehab Budget + Revisions v1

User approved implementing the persistent rehab budget and revision workflow. This is a product experiment; customer demand and local residential unit-cost calibration remain unproven.

Backend adds validated scope items and quote provenance, optional parent-deal links, and an additive `deal_revisions` companion table created by existing startup initialization. Existing `saved_deals` columns and old records are unchanged. Save/read/list contracts gain optional fields. Parent links are owner-checked; scoped/revised saves recompute through the unchanged engine and reject scope/rehab mismatches. Earlier revisions remain immutable. Frontend shared types mirror these additions.

Validation: 11 unittest integration/regression tests pass against isolated SQLite, including migration from an existing saved table, cross-user denial, malformed quote rejection, contingency rounding, full $50K to $67K + two-month revision, and locked PASS/BUY/BUY scenarios. Added Backend CI. No dependencies, engine, AnalyzeRequest, PDF service, or paid-provider integrations changed.

Release status at commit: backend feature branch ready for PR/CI; deployment and frontend integration pending. Deploy backend and verify OpenAPI additions before releasing the frontend. PostgreSQL runtime and signed-in production QA are not claimed. Rollback application code without dropping the additive table; do not delete saved records.
