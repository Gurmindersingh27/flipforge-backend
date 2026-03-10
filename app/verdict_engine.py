from typing import List, Dict, Tuple

# Each stress result must contain:
# name: str
# profit: float
# margin: float
StressResult = Dict[str, float]


def evaluate_verdict(
    stress_results: List[StressResult],
    min_profit: float,
    min_margin: float,
) -> Tuple[str, str]:
    """
    Returns:
      verdict: BUY | CONDITIONAL | PASS
      reason: short explanation string
    """

    # 1. FAIL FAST — first stress that breaks minimums
    for stress in stress_results:
        if stress["profit"] < min_profit or stress["margin"] < min_margin:
            name = stress["name"]
            if name == "Base":
                return (
                    "PASS",
                    "Fails at Base assumptions (before stress).",
                )
            return (
                "PASS",
                f"Fails under {name} stress",
            )

    # 2. Find weakest surviving stress (lowest margin)
    weakest = min(stress_results, key=lambda s: s["margin"])

    # 3. CONDITIONAL band (within 20% of min margin)
    if weakest["margin"] <= min_margin * 1.2:
        return (
            "CONDITIONAL",
            f"Fragile under {weakest['name']} stress",
        )

    # 4. Otherwise BUY
    strongest = max(stress_results, key=lambda s: s["margin"])
    return (
        "BUY",
        f"Strongest performance under {strongest['name']} stress",
    )


def outputs_allowed(verdict: str) -> Dict[str, bool]:
    """
    Integrity Gate — controls what the UI is allowed to show
    """

    if verdict == "PASS":
        return {
            "lender_report": False,
            "negotiation_script": False,
            "mao": False,
        }

    if verdict == "CONDITIONAL":
        return {
            "lender_report": True,
            "negotiation_script": True,  # limited later
            "mao": True,
        }

    # BUY
    return {
        "lender_report": True,
        "negotiation_script": True,
        "mao": True,
    }


# -----------------------
# Local sanity tests
# -----------------------
if __name__ == "__main__":

    # Shock Case
    stress_results_1 = [
        {"name": "Base", "profit": 32000, "margin": 0.16},
        {"name": "ARV -5%", "profit": 14000, "margin": 0.07},
        {"name": "Rehab +10%", "profit": 28000, "margin": 0.14},
    ]

    verdict, reason = evaluate_verdict(
        stress_results_1,
        min_profit=30000,
        min_margin=0.15,
    )

    print("Shock Case:", verdict, reason)
    print("Outputs:", outputs_allowed(verdict))

    # Time Fragility Case
    stress_results_2 = [
        {"name": "Base", "profit": 52000, "margin": 0.21},
        {"name": "Holding +2", "profit": 38000, "margin": 0.165},
    ]

    verdict, reason = evaluate_verdict(
        stress_results_2,
        min_profit=30000,
        min_margin=0.15,
    )

    print("Time Fragility:", verdict, reason)
    print("Outputs:", outputs_allowed(verdict))
