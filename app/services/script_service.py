from __future__ import annotations
from ..models import NegotiationScriptRequest


def generate_negotiation_script(req: NegotiationScriptRequest) -> str:
    r = req.result
    max_safe = r.max_safe_offer or 0.0
    # Target offer: 97% of max safe offer, rounded to nearest $1,000
    target_offer = round(max_safe * 0.97 / 1000) * 1000

    buyer = req.buyer_name or "I"
    seller = req.seller_name or "there"
    prop = req.property_address or "the property"

    # Grammar helper: "I am" vs "Name is"
    verb = "am" if buyer == "I" else "is"

    paras: list[str] = []

    # Opening
    paras.append(
        f"Hi {seller}, thank you for your time. After completing a full financial "
        f"underwriting of {prop}, {buyer} {verb} prepared to offer ${target_offer:,.0f}."
    )

    # Deal basis
    net = r.net_profit or 0.0
    pct = (r.profit_pct or 0.0) * 100
    paras.append(
        f"My analysis shows a maximum safe offer of ${max_safe:,.0f} based on the "
        f"after-repair value, projected total project costs of ${r.total_project_cost:,.0f} "
        f"(which includes purchase, rehab, closing, carrying, and selling expenses), and "
        f"a required profit margin. At ${target_offer:,.0f} the deal produces an estimated "
        f"net profit of ${net:,.0f} ({pct:.1f}% margin) — enough to justify the risk "
        f"while giving us both room to negotiate."
    )

    # Price gap paragraph (only if seller ask is provided and above target)
    if req.seller_ask_price and req.seller_ask_price > target_offer:
        gap = req.seller_ask_price - target_offer
        paras.append(
            f"I noticed your asking price is ${req.seller_ask_price:,.0f}. "
            f"The ${gap:,.0f} gap between your ask and my offer reflects the costs "
            f"and risks my underwriting has surfaced — this isn't a lowball, it's the "
            f"math. If there's flexibility on terms — seller financing, a closing cost "
            f"credit, or a phased structure — I'm open to that conversation."
        )

    # Risk transparency (use typed_flags.label for human-readable text)
    flags = r.typed_flags or []
    if flags:
        flag_labels = ", ".join(f.label for f in flags)
        paras.append(
            f"To be transparent, my analysis flagged the following risks with this "
            f"deal: {flag_labels}. These are priced into my offer, not ignored."
        )

    # Closing
    paras.append(
        f"I'm ready to move quickly. I have financing in place, can provide proof of "
        f"funds on request, and can close on a timeline that works for you. "
        f"My offer of ${target_offer:,.0f} is based on conservative, data-driven "
        f"underwriting — not speculation. I hope we can make this work."
    )

    return "\n\n".join(paras)
