"""Score the grounding judge against Gonzalo's calibration labels.

Reads the machine sidecar (CALIBRATION_VAULT.jsonl: full source + claim per case)
and his verdicts from the filled markdown table, runs the judge+gate decision per
case, and reports agreement. Run the judge half anytime (a baseline); agreement is
only computed for rows he has labeled.

Pure helpers (parse_md_verdicts, judge_action, score) are unit-tested; __main__
does the real LLM calls.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from eval.grounding.decide import decide
from eval.grounding.judge import judge_claims
from eval.grounding.types import Action, Claim, ClaimType, Source

_VALID = {"assert", "state_as_read", "demote"}
_ROW = re.compile(r"^\|\s*(\d+)\s*\|")


def load_cases(jsonl_path: Path) -> list[dict]:
    return [json.loads(ln) for ln in jsonl_path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def parse_md_verdicts(md_text: str) -> dict[int, str]:
    """Pull {case_number: verdict} from the filled 'Your verdict' column."""
    out: dict[int, str] = {}
    for line in md_text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells[0] and cells[-1] are the empty edges; verdict is the last real cell.
        verdict = cells[-2].lower()
        if verdict in _VALID:
            out[int(m.group(1))] = verdict
    return out


def load_labels_json(path: Path) -> dict[int, str]:
    """Pull {case_number: verdict} from the normalized labels sidecar.

    The sidecar preserves Gonzalo's prose + correction text per case; here we
    take only the normalized verdict, ignoring any not in the controlled vocab.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, str] = {}
    for k, v in data.get("labels", {}).items():
        verdict = (v.get("verdict") or "").lower()
        if verdict in _VALID:
            out[int(k)] = verdict
    return out


def judge_action(case: dict, *, temperature: int, judge_fn=judge_claims) -> Action:
    ctype = ClaimType(case["type"])
    cites = ["S1"] if ctype in (ClaimType.FACT, ClaimType.CONCEPTUAL) else []
    claim = Claim(id=f"c{case['n']}", type=ctype, text=case["claim"], cites=cites, confidence=case.get("conf"))
    source = Source(id="S1", tier=1, kind="vault_entry", text=case["source_full"])
    verdict = judge_fn([claim], [source])[0]
    return decide(verdict, confidence=case.get("conf"), temperature=temperature).action


def score(cases: list[dict], verdicts: dict[int, str], *, temperature: int, judge_fn=judge_claims) -> dict:
    rows: list[dict] = []
    agree = labeled = 0
    for case in cases:
        n = case["n"]
        ja = judge_action(case, temperature=temperature, judge_fn=judge_fn)
        human = verdicts.get(n)
        match = human is not None and ja.value == human
        if human is not None:
            labeled += 1
            agree += int(match)
        rows.append({"n": n, "judge": ja.value, "human": human, "match": match, "claim": case["claim"]})
    return {
        "rows": rows,
        "labeled": labeled,
        "agree": agree,
        "agreement": (agree / labeled) if labeled else 0.0,
        "temperature": temperature,
    }


def _main() -> None:
    ap = argparse.ArgumentParser(description="Score the grounding judge against calibration labels.")
    ap.add_argument("--cases", default="services/coach/eval/grounding/CALIBRATION_VAULT.jsonl")
    ap.add_argument("--md", default="services/coach/eval/grounding/CALIBRATION_VAULT.md")
    ap.add_argument("--labels", default="services/coach/eval/grounding/CALIBRATION_VAULT.labels.json",
                    help="normalized verdict sidecar; preferred over parsing --md prose")
    ap.add_argument("--temperature", type=int, default=6)
    args = ap.parse_args()

    cases = load_cases(Path(args.cases))
    labels_path = Path(args.labels)
    md_path = Path(args.md)
    if labels_path.exists():
        verdicts = load_labels_json(labels_path)
        src = labels_path.name
    elif md_path.exists():
        verdicts = parse_md_verdicts(md_path.read_text(encoding="utf-8"))
        src = md_path.name
    else:
        verdicts, src = {}, "(none)"

    report = score(cases, verdicts, temperature=args.temperature)
    for r in report["rows"]:
        flag = "" if r["human"] is None else ("  ✓" if r["match"] else f"  ✗ (you: {r['human']})")
        print(f"#{r['n']:>2} judge={r['judge']:<13}{flag}  | {r['claim'][:80]}")
    if report["labeled"]:
        print(f"\nlabels: {src} · AGREEMENT: {report['agree']}/{report['labeled']} = {report['agreement']:.2f} (temp {args.temperature})")
    else:
        print(f"\n(no labels yet — judge baseline only; fill verdicts in {src})")


if __name__ == "__main__":
    _main()
