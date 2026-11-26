"""Phase 10: grounding check, narrator outcomes with a fake Claude client (no network), AI-disabled behaviour."""

from types import SimpleNamespace as NS

import pytest

from app.ai import grounding
from app.ai.research import narrate

DATA = {"session_date": "2026-11-25", "regime": {"name": "sideways", "score": 0.0, "policy": {"max_new_entries_per_day": 1, "risk_multiplier": 0.5}},
        "universe": {"eligible": 935, "traded": 2982},
        "confirmed_setups": [{"symbol": "AUROPHARMA", "entry": 1843.0, "stop": 1720.94, "score": 91.0},
                             {"symbol": "ZYDUSLIFE", "entry": 1236.0, "stop": 1168.58, "score": 87.0}],
        "accounts": [{"name": "fwd-D0", "equity": 100000.0, "return_pct": 0.0, "drawdown_pct": 0.0}],
        "note": "close at 15:30 IST", "breadth": 46.4244}


# ------------------------------------------------------------------ grounding

def test_grounded_text_passes_with_rounding_and_separators():
    text = ("On 2026-11-25 the regime was sideways (score 0), allowing 1 entry at 0.5x risk. 935 of 2,982 stocks were eligible; "
            "breadth 46.4%. Two setups: AUROPHARMA (entry 1,843, stop 1,720.94) and ZYDUSLIFE. fwd-D0 equity ₹1,00,000. Data as of 15:30 IST.")
    g = grounding.check(text, DATA)
    assert g["ok"] and g["numbers_checked"] >= 9, g


def test_invented_numbers_and_dates_are_rejected():
    g = grounding.check("Breadth rose to 52.1% and AUROPHARMA could reach 2,100 by 2026-12-15.", DATA)
    assert not g["ok"] and set(g["unsupported"]) == {"52.1", "2,100"} and g["unsupported_dates"] == ["2026-12-15"]


def test_computed_numbers_are_rejected():
    # 1843 - 1720.94 = 122.06: a derived number not present in the data
    assert grounding.check("The stop is 122.06 below the entry.", DATA)["unsupported"] == ["122.06"]


def test_list_lengths_count_as_data():
    assert grounding.check("There were 2 confirmed setups.", DATA)["ok"]


# ------------------------------------------------------------------ narrator with a fake client

class FakeClient:
    def __init__(self, text="", stop_reason="end_turn", raise_exc=None):
        self.calls = []
        msg = NS(stop_reason=stop_reason, model="claude-opus-5-5", usage=NS(input_tokens=1200, output_tokens=300),
                 content=[NS(type="text", text=text)], stop_details=NS(explanation="declined") if stop_reason == "refusal" else None)
        outer = self

        class Stream:
            def __enter__(self):
                if raise_exc:
                    raise raise_exc
                return self

            def __exit__(self, *a):
                return False

            def get_final_message(self):
                return msg

        class Messages:
            def stream(self, **kw):
                outer.calls.append(kw)
                return Stream()

        self.beta = NS(messages=Messages())


def test_accepted_narrative_and_request_shape():
    c = FakeClient("**Market** sideways, score 0; 935 of 2,982 eligible. AUROPHARMA entry 1,843, stop 1,720.94.")
    n = narrate(DATA, client=c)
    assert n.status == "accepted" and n.grounding["ok"]
    kw = c.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]
    assert kw["output_config"] == {"effort": "medium"} and "thinking" not in kw and "temperature" not in kw
    assert "Use only the JSON data" in kw["system"] and '"eligible": 935' in kw["messages"][0]["content"]


def test_ungrounded_narrative_is_rejected_not_shown():
    n = narrate(DATA, client=FakeClient("Expect a 12% rally to 2,064."))
    assert n.status == "rejected" and "12" in n.detail and "2,064" in n.detail


@pytest.mark.parametrize("kw,status", [({"stop_reason": "refusal"}, "refused"), ({"stop_reason": "max_tokens", "text": "partial"}, "truncated"),
                                       ({"raise_exc": RuntimeError("boom")}, "error"), ({"text": ""}, "error")])
def test_refusal_truncation_and_errors_are_never_shown(kw, status):
    n = narrate(DATA, client=FakeClient(**kw))
    assert n.status == status and (n.text is None)


def test_disabled_means_no_call(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "ai_enabled", False)
    n = narrate(DATA)
    assert n.status == "disabled" and n.text is None


@pytest.mark.db
def test_api_only_exposes_accepted_text(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from app.ai import research
    from app.database import ping, session_scope
    from app.main import create_app
    from app.models import Report
    ok, d = ping()
    if not ok:
        pytest.skip(d)
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config("alembic.ini"), "head")
    with session_scope() as db:
        db.execute(text("truncate ai_narratives, reports restart identity cascade"))
        db.add(Report(kind="post_market", session_date=__import__("datetime").date(2026, 11, 25), data_as_of="x", sources=[], content_md="# r", data=DATA))
    c = TestClient(create_app())
    monkeypatch.setattr(research, "enabled", lambda: False)
    assert c.post("/api/v1/reports/1/narrate").status_code == 409                      # disabled: refused, nothing called
    r = c.get("/api/v1/reports/1").json()
    assert r["ai"]["enabled"] is False and r["ai"]["status"] is None and r["markdown"] == "# r"   # works without AI
    with session_scope() as db:
        rep = db.get(Report, 1)
        research.narrate_report(db, rep, client=FakeClient("A 12% rally to 2,064 is likely."))
    r = c.get("/api/v1/reports/1").json()
    assert r["ai"]["status"] == "rejected" and "text" not in r["ai"]
    with session_scope() as db:
        research.narrate_report(db, db.get(Report, 1), client=FakeClient("Regime sideways, score 0; 935 eligible."))
    r = c.get("/api/v1/reports/1").json()
    assert r["ai"]["status"] == "accepted" and r["ai"]["text"].startswith("Regime sideways") and r["ai"]["grounding"]["ok"]
