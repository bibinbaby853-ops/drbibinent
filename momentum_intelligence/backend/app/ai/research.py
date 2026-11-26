"""Optional Claude research narrative on top of the deterministic daily report (Phase 10).

* Server-side only, official ``anthropic`` SDK; the key comes from MI_ANTHROPIC_API_KEY (never the browser).
* The model receives ONLY the report's stored data (JSON) and is told to use nothing else.
* Every number in its answer must pass ``grounding.check``; otherwise the narrative is rejected and the
  deterministic report stands alone. A refusal, a truncated answer or an API error is recorded, never shown.
* With AI disabled (MI_AI_ENABLED false or no key) nothing is called and status is ``disabled``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..config import get_settings
from . import grounding

SYSTEM = """You write the short research commentary that accompanies a daily report from an Indian equity \
momentum research platform that trades only on paper.

Use only the JSON data the user provides. Do not use outside knowledge about markets, companies, news or prices.

Rules:
- Every number you write must appear in the data (you may round it). Do not compute new numbers (no differences, \
sums, averages or percentages that are not already in the data). Do not write any number that is not in the data.
- No predictions, price targets, buy/sell/hold advice or recommendations. Describe what the rules and data show.
- Say plainly that the platform found no out-of-sample edge in backtests and that the paper accounts are an ongoing test.
- Plain English for an Indian retail investor. About 200 to 300 words. Markdown with these short sections: \
Market, Sectors, Setups, Paper accounts. No tables, no headings beyond those four."""


@dataclass
class Narrative:
    status: str                 # accepted | rejected | refused | truncated | error | disabled
    text: str | None
    model: str | None
    grounding: dict | None
    usage: dict | None
    detail: str | None = None


def enabled() -> bool:
    s = get_settings()
    return bool(s.ai_enabled and s.anthropic_api_key)


def _client():
    import anthropic
    s = get_settings()
    return anthropic.Anthropic(api_key=s.anthropic_api_key.get_secret_value(), max_retries=2, timeout=120.0)


def narrate(report_data: dict, *, client=None) -> Narrative:
    s = get_settings()
    if client is None and not enabled():
        return Narrative("disabled", None, None, None, None, "AI layer disabled (MI_AI_ENABLED / MI_ANTHROPIC_API_KEY)")
    client = client or _client()
    payload = json.dumps(report_data, default=str, ensure_ascii=False)
    try:
        with client.beta.messages.stream(
            model=s.ai_model,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            system=SYSTEM,
            messages=[{"role": "user", "content": f"Report data (JSON):\n{payload}\n\nWrite the commentary."}],
        ) as stream:
            msg = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001 - any API failure leaves the deterministic report in place
        return Narrative("error", None, s.ai_model, None, None, f"{type(exc).__name__}: {exc}"[:500])
    usage = {"input_tokens": getattr(msg.usage, "input_tokens", None), "output_tokens": getattr(msg.usage, "output_tokens", None)}
    served = getattr(msg, "model", s.ai_model)
    if msg.stop_reason == "refusal":
        det = getattr(msg, "stop_details", None)
        return Narrative("refused", None, served, None, usage, getattr(det, "explanation", None) if det else None)
    if msg.stop_reason == "max_tokens":
        return Narrative("truncated", None, served, None, usage, "answer hit max_tokens")
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    if not text:
        return Narrative("error", None, served, None, usage, "empty answer")
    g = grounding.check(text, report_data)
    return Narrative("accepted" if g["ok"] else "rejected", text, served, g, usage,
                     None if g["ok"] else f"unsupported numbers: {', '.join(g['unsupported'] + g['unsupported_dates'])}")


def narrate_report(db, report, *, client=None):
    """Narrate a stored report and save the outcome (any status). Returns the AINarrative row."""
    from ..models import AINarrative
    n = narrate(report.data, client=client)
    # rejected text is kept for audit; the API only ever returns accepted text
    row = AINarrative(report_id=report.id, status=n.status, model=n.model, text=n.text,
                      grounding=n.grounding, usage=n.usage, detail=n.detail)
    db.add(row)
    db.flush()
    return row
