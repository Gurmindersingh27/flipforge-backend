# app/narratives/narrative_generator.py

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models import RiskFlag, RehabReality, Breakpoints, StressTestScenario


class NarrativeGenerator:
    """
    Backend-first deterministic narratives.
    No math. No LLM. No randomness.
    This is the standardized voice for UI / PDF / agent alerts.
    """

    @staticmethod
    def build(
        *,
        base: Any,  # BaseMetrics from analysis_engine (kept as Any to avoid circular imports)
        overall_verdict: str,
        confidence_score: int,
        best_strategy: str,
        rehab_reality: RehabReality,
        breakpoints: Breakpoints,
        typed_flags: List[RiskFlag],
        stress_tests: List[StressTestScenario],
    ) -> Dict[str, Any]:
        worst_fail = NarrativeGenerator._worst_stress_fail(stress_tests)
        primary_break = breakpoints.first_break_scenario

        net_profit = float(getattr(base, "net_profit", 0.0))

        return {
            "verdict_header": f"Overall Verdict: {overall_verdict}",
            "overall_verdict": NarrativeGenerator._overall_verdict(
                overall_verdict,
                net_profit=net_profit,
            ),
            "confidence": NarrativeGenerator._confidence(confidence_score),
            "rehab_reality": NarrativeGenerator._rehab_reality(rehab_reality),
            "stress": NarrativeGenerator._stress(overall_verdict, primary_break, worst_fail),
            "risk_summary": NarrativeGenerator._risk_summary(typed_flags),
            "strategy_fit": NarrativeGenerator._strategy_fit(best_strategy),
            "meta": {
                "rehab_ratio": float(rehab_reality.rehab_ratio),
                "rehab_severity": rehab_reality.severity,
                "first_break_scenario": primary_break,
                "worst_fail_scenario": worst_fail.name if worst_fail else None,
            },
        }

    # -------------------------
    # Core narratives
    # -------------------------
    @staticmethod
    def _overall_verdict(v: str, *, net_profit: float) -> str:
        gap_sentence = (
            "Profit exists on paper, but the margin of error is insufficient for a safe exit. "
        )

        if v == "BUY":
            return (
                "The deal clears required return thresholds with sufficient margin. "
                "Profitability remains intact under defined stress scenarios."
            )

        if v == "CONDITIONAL":
            return (
                "The deal meets baseline return targets but is sensitive to downside assumptions. "
                "Execution discipline is required to preserve margin."
            )

        # PASS
        base_text = (
            "At current pricing, the deal fails to support adequate margin "
            "once costs, risk, and stress scenarios are applied."
        )

        # 👉 Gap Logic
        if net_profit > 0:
            return gap_sentence + base_text

        return base_text

    @staticmethod
    def _confidence(score: int) -> str:
        if score >= 75:
            return (
                "The deal demonstrates strong robustness. Returns remain viable across tested downside scenarios "
                "with limited reliance on favorable assumptions."
            )
        if score >= 55:
            return "The deal is viable but assumption-sensitive. Small adverse changes materially impact returns."
        return (
            "The deal lacks structural robustness. Minor deviations from assumptions result in unacceptable margin compression."
        )

    @staticmethod
    def _rehab_reality(rr: RehabReality) -> str:
        pct = rr.rehab_ratio * 100.0
        if pct < 1:
            return "Rehab intensity is classified as Light. Rehab budget is minimal relative to purchase price."

        sev = rr.severity

        if sev == "LIGHT":
            return f"Rehab intensity is classified as Light (~{pct:.0f}% of purchase price) with limited execution risk."
        if sev == "MEDIUM":
            return f"Rehab intensity is classified as Medium (~{pct:.0f}% of purchase price). Overruns begin to erode margin if not controlled."
        if sev == "EXTREME":
            return f"Rehab intensity is classified as Extreme (~{pct:.0f}% of purchase price). Execution risk is high and downside tolerance is limited."
        return f"Rehab intensity is classified as Heavy (~{pct:.0f}% of purchase price). Cost overruns and delays materially compress returns."

    @staticmethod
    def _stress(
        overall_verdict: str,
        first_break: Optional[str],
        worst_fail: Optional[StressTestScenario],
    ) -> str:
        if overall_verdict in ("BUY", "CONDITIONAL") and not first_break:
            return "The deal remains viable across tested downside scenarios, indicating tolerance to adverse market and cost conditions."

        if first_break:
            return f"Project viability breaks under {first_break}. This indicates sensitivity to downside conditions."

        if worst_fail:
            return f"Project viability breaks under {worst_fail.name}. This indicates sensitivity to downside conditions."

        return "Stress testing indicates limited downside tolerance under current assumptions."

    @staticmethod
    def _risk_summary(flags: List[RiskFlag]) -> str:
        if not flags:
            return "Identified risks are limited and manageable within standard underwriting assumptions."

        has_critical = any(f.severity == "critical" for f in flags)
        has_moderate = any(f.severity == "moderate" for f in flags)

        top = flags[:2]
        top_labels = ", ".join([f.label for f in top if f.label]) or ", ".join([f.code for f in top])

        if has_critical:
            return (
                f"Critical risks materially threaten deal viability, closing certainty, or capital protection. "
                f"Primary risks include {top_labels}. Aggressive pricing or avoidance is advised."
            )
        if has_moderate:
            return (
                f"Primary risks include {top_labels}. These factors affect margin and timeline but are addressable "
                "through pricing and execution discipline."
            )
        return (
            f"Primary risks include {top_labels}. These factors are present but typically manageable with standard controls."
        )

    @staticmethod
    def _strategy_fit(best: str) -> str:
        b = (best or "").lower()
        if b == "flip":
            return (
                "Capital is best deployed via resale. Returns favor short-term execution over long-term hold once risk and capital efficiency are considered."
            )
        if b == "brrrr":
            return (
                "The deal supports long-term equity retention. Rental performance and refinance potential justify a hold strategy relative to risk."
            )
        return (
            "Assignment produces superior risk-adjusted returns due to execution risk and capital exposure associated with direct ownership."
        )

    # -------------------------
    # Helpers
    # -------------------------
    @staticmethod
    def _worst_stress_fail(stress: List[StressTestScenario]) -> Optional[StressTestScenario]:
        if not stress:
            return None
        failing = [s for s in stress if s.verdict == "PASS"]
        if not failing:
            return None
        return sorted(failing, key=lambda s: s.net_profit)[0]