"""config/pricing.json, pipeline/pricing.py and read-time API-equivalent costs (spec 2026-11-05-console-end-to-end)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pipeline import cost_forecast, pricing
from pipeline.runtime import DEEP_MODEL, DEFAULT_MODEL
from pipeline.ui import runs

FIXTURES = Path(__file__).parent / "fixtures" / "ui" / "processed"
NODES = ["extract", "kb_curator", "writer"]


@pytest.mark.parametrize("model,inp,out,read,write", [
    ("claude-opus-5-5", 4, 20, 0.20, 5),
    ("claude-sonnet-5-5", 2, 10, 0.10, 2.50),
    ("claude-haiku-5-5", 0.10, 0.50, 0.01, 0.125),
    ("claude-fable-5-1", 10, 50, 0.25, 12.50),
])
def test_cost_per_mtok(model, inp, out, read, write):  # [DT-D6]
    m = 1_000_000
    assert pricing.cost(model, m, 0, 0, 0) == pytest.approx(inp)
    assert pricing.cost(model, 0, m, 0, 0) == pytest.approx(out)
    assert pricing.cost(model, 0, 0, m, 0) == pytest.approx(read)
    assert pricing.cost(model, 0, 0, 0, m) == pytest.approx(write)
    assert pricing.cost(f"anthropic/{model}", m, 0, 0, 0) == pytest.approx(inp)


def test_unknown_model_is_none_never_zero():  # [DT-D6]
    assert pricing.cost("fake-model", 1000, 1000, 0, 0) is None
    assert pricing.cost(None, 1000, 1000, 0, 0) is None


def test_cost_forecast_reads_the_one_table():  # [DT-D6]
    assert cost_forecast.PRICES[DEFAULT_MODEL]["input"] == 2.0
    assert cost_forecast.PRICES[DEEP_MODEL]["cache_write"] == 5.0
    assert cost_forecast.PRICES is pricing.PRICES


def test_catalog_has_source_and_date():  # [DT-D6]
    raw = json.loads(pricing.CATALOG.read_text())
    assert raw["as_of"] == "2026-11-05" and raw["source"].startswith("https://")


def _row(**kw):
    return {"ts": "2026-11-05T00:00:00", "stage": "extract", "duration_s": 1.0, **kw}


TOKENS = {"input_tokens": 3000, "output_tokens": 500, "cache_read_tokens": 1000, "cache_creation_tokens": 1000}
# input_tokens is litellm's prompt_tokens, which already holds both cache counts: 1000 fresh here.
SONNET = (1000 * 2 + 500 * 10 + 1000 * 0.10 + 1000 * 2.5) / 1_000_000


def test_zero_logged_cost_is_priced_from_the_row_model():  # [DT-D6]
    t = runs.telemetry([_row(model="claude-sonnet-5-5", cost_usd=0.0, **TOKENS)], 10.0, 0.0)
    assert t["per_node"]["extract"]["cost_usd"] == pytest.approx(SONNET)
    assert t["per_node"]["extract"]["cost_estimated"] is True
    assert t["cost_usd"] == pytest.approx(SONNET) and t["cost_estimated"] is True


def test_real_billed_cost_is_kept():  # [DT-D6]
    t = runs.telemetry([_row(model="claude-sonnet-5-5", cost_usd=0.42, **TOKENS)], 10.0, 0.0)
    assert t["per_node"]["extract"]["cost_usd"] == 0.42
    assert t["per_node"]["extract"]["cost_estimated"] is False
    assert t["cost_usd"] == 0.42 and t["cost_estimated"] is False


def test_unknown_model_gives_none_at_both_levels():  # [DT-D6]
    t = runs.telemetry([_row(model="mystery", cost_usd=0.0, **TOKENS)], 10.0, 0.0)
    assert t["per_node"]["extract"]["cost_usd"] is None and t["per_node"]["extract"]["cost_estimated"] is False
    assert t["cost_usd"] is None and t["cost_estimated"] is False
    assert t["tokens"]["output"] == 500


def test_one_unknown_node_does_not_blank_the_run():  # [DT-D6]
    rows = [_row(model="claude-sonnet-5-5", cost_usd=0.0, **TOKENS),
            {**_row(stage="writer", model="mystery", cost_usd=0.0), **TOKENS}]
    t = runs.telemetry(rows, 10.0, 0.0)
    assert t["per_node"]["writer"]["cost_usd"] is None
    assert t["cost_usd"] == pytest.approx(SONNET) and t["cost_estimated"] is True


def test_no_tokens_stay_zero_not_estimated():  # [DT-D6]
    t = runs.telemetry([_row(cost_usd=0.0)], 10.0, 0.0)
    assert t["per_node"]["extract"]["cost_usd"] == 0.0 and t["cost_estimated"] is False


def test_kb_curator_row_prices_through_usage_lines():  # [DT-D6]
    """The real shape: per_call on the row, the model only in usage.jsonl."""
    calls = [{**TOKENS, "cost_usd": 0.0}, {**TOKENS, "cost_usd": 0.0}]
    row = {"ts": "2026-11-05T00:00:00", "stage": "kb-curator", "duration_s": 5.0, "llm_calls": 2, "total_cost_usd": 0.0,
           "per_call": calls}
    usage = [{"ts": 1.0, "event": "start", "call_id": "a", "node": "kb_curator", "model": "claude-opus-5-5"},
             {"ts": 2.0, "event": "end", "call_id": "a", "node": "kb_curator", "model": "claude-opus-5-5", **TOKENS,
              "cost_usd": 0.0}]
    t = runs.telemetry([row], 10.0, 0.0, usage)
    node = t["per_node"]["kb_curator"]
    assert node["model"] == "claude-opus-5-5"
    expected = 2 * (1000 * 4 + 500 * 20 + 1000 * 0.2 + 1000 * 5) / 1_000_000
    assert node["cost_usd"] == pytest.approx(expected) and node["cost_estimated"] is True
    assert runs.telemetry([row], 10.0, 0.0)["per_node"]["kb_curator"]["cost_usd"] is None  # no usage.jsonl: unknown


def test_live_usage_lines_are_priced():  # [DT-D6]
    usage = [{"ts": 2.0, "event": "end", "call_id": "a", "node": "writer", "model": "claude-sonnet-5-5", **TOKENS,
              "cost_usd": 0.0}]
    t = runs.telemetry([], None, 0.0, usage, active=True)
    assert t["per_node"]["writer"]["cost_usd"] == pytest.approx(SONNET)
    assert t["per_node"]["writer"]["cost_estimated"] is True and t["cost_estimated"] is True


def test_fixture_run_list_and_detail_agree(tmp_path):  # [DT-D6]
    root = tmp_path / "processed"
    shutil.copytree(FIXTURES, root)
    rd = root / "fx-done" / "langgraph"
    lines = [json.loads(ln) for ln in (rd / "runs.jsonl").read_text().splitlines()]
    for line in lines:
        line.pop("cost_usd", None)
        line.pop("total_cost_usd", None)
        for call in line.get("per_call", []):
            call["cost_usd"] = 0.0
    (rd / "runs.jsonl").write_text("\n".join(json.dumps(ln) for ln in lines) + "\n")
    usage = {"ts": 1.0, "event": "end", "call_id": "k", "node": "kb_curator", "model": "claude-opus-5-5"}
    (rd / "usage.jsonl").write_text(json.dumps(usage) + "\n")
    detail = runs.load_run(root, "fx-done", NODES)["telemetry"]
    assert detail["per_node"]["kb_curator"]["cost_estimated"] is True
    assert detail["per_node"]["kb_curator"]["cost_usd"] > 0
    item = next(r for r in runs.list_runs(root) if r["run_id"] == "fx-done")
    assert item["cost_usd"] == detail["cost_usd"] and item["cost_estimated"] == detail["cost_estimated"]
