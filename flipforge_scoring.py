# ---------------------------------------------------------
# FLIPFORGE SCORE ENGINE
# ---------------------------------------------------------

def compute_flipforge_score(
    profit: float,
    roi_percent: float,
    target_min_profit: float,
    target_min_roi_percent: float,
    arv_confidence: float,
    rehab_total: float,
    sqft: float,
    condition_score: float = 60,
    extra_rehab_from_photos: float = 0,
    risk_raw: float = 50,
):
    """
    Returns FlipForge Score package:
    - overall score
    - grade
    - verdict
    - sub_scores
    - flags
    - recommended actions
    """

    # -----------------------------
    # Sub-score: Profit Score
    # -----------------------------
    profit_score = min(100, max(0, (profit / target_min_profit) * 70))

    # -----------------------------
    # Sub-score: ROI Score
    # -----------------------------
    roi_score = min(100, max(0, (roi_percent / target_min_roi_percent) * 80))

    # -----------------------------
    # Sub-score: Safety Score (inverse risk)
    # -----------------------------
    # Base 50
    safety = 50

    # Higher rehab = more risky
    if rehab_total > 0.5 * sqft:  # rough threshold
        safety -= 10

    # Condition impact
    if condition_score < 40:
        safety -= 15
    elif condition_score < 20:
        safety -= 25

    # Consider raw risk score (0–100, higher = more dangerous)
    # Flip to safety:
    safety -= (risk_raw - 50) / 2

    safety_score = int(min(100, max(0, safety)))

    # -----------------------------
    # Sub-score: Rehab Complexity
    # -----------------------------
    complexity_raw = 0
    complexity_raw += extra_rehab_from_photos / max(1, (sqft ** 0.5))
    complexity_score = int(min(100, max(0, 100 - complexity_raw)))

    # -----------------------------
    # Sub-score: Market Strength
    # -----------------------------
    if arv_confidence >= 0.85:
        market_score = 85
    elif arv_confidence >= 0.7:
        market_score = 70
    elif arv_confidence >= 0.5:
        market_score = 55
    else:
        market_score = 40

    # -----------------------------
    # Sub-score: Condition
    # -----------------------------
    cond_score = int(condition_score)

    # -----------------------------
    # Final FlipForge Score
    # -----------------------------
    ffs = (
        profit_score * 0.25 +
        roi_score * 0.25 +
        safety_score * 0.15 +
        complexity_score * 0.10 +
        market_score * 0.10 +
        cond_score * 0.15
    )

    final_score = int(min(100, max(0, ffs)))

    # -----------------------------
    # Grade Assignment
    # -----------------------------
    if final_score >= 90:
        grade = "A"
    elif final_score >= 80:
        grade = "B"
    elif final_score >= 65:
        grade = "C"
    elif final_score >= 50:
        grade = "D"
    else:
        grade = "F"

    # Optional +/- improvement
    if final_score % 10 >= 7 and grade in ["A", "B", "C"]:
        grade += "+"

    # -----------------------------
    # Verdict
    # -----------------------------
    if final_score >= 80:
        verdict = "green_light"
    elif final_score >= 65:
        verdict = "conditional"
    else:
        verdict = "walk_away"

    # -----------------------------
    # Flags
    # -----------------------------
    flags = []
    if profit_score < 50:
        flags.append("weak_profit")
    if roi_score < 50:
        flags.append("weak_roi")
    if safety_score < 50:
        flags.append("high_risk")
    if cond_score < 50:
        flags.append("poor_condition")
    if market_score < 60:
        flags.append("weak_market")

    # -----------------------------
    # Recommended Actions
    # -----------------------------
    recommended = []
    if verdict == "conditional":
        recommended.append("Acquire below list price to improve margin.")
        recommended.append("Verify ARV with tighter comp set.")
        recommended.append("Increase rehab contingency buffer.")
    if verdict == "walk_away":
        recommended.append("Deal too risky at current price.")
        recommended.append("Only proceed with a significant discount.")
    if cond_score < 50:
        recommended.append("Expect extra unknown rehab costs due to condition.")

    return {
        "score": final_score,
        "grade": grade,
        "verdict": verdict,
        "sub_scores": {
            "profit_score": int(profit_score),
            "roi_score": int(roi_score),
            "safety_score": int(safety_score),
            "complexity_score": int(complexity_score),
            "market_score": int(market_score),
            "condition_score": int(cond_score),
        },
        "flags": flags,
        "recommended_actions": recommended
    }
