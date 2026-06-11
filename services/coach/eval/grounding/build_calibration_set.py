"""Build a calibration set from Gonzalo's real vault entries.

Pipeline: sample entries -> extract the factual source sections (Story Anchor +
Raw Transcript Notes) -> ask a coach-voice generator (subscription `claude -p`)
for the claims it would make, tagged -> parse with the segmenter -> random-sample
-> write a peer-review table.

The generated claims are realistic (coach voice, grounded only in the excerpt),
including the bold interpretations the coach is tempted to assert -- which is
exactly what Gonzalo needs to label assert / state_as_read / demote.

Pure helpers (extract_source, sample_claims) are unit-tested; build() does the
real LLM calls and is run manually.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

from eval.llm import call_llm
from eval.grounding.segmenter import segment
from eval.grounding.types import Claim, ClaimType

# Sections of an entry that hold Gonzalo's actual behaviour/words (not the
# already-interpreted Core Insight / Framework sections, which would leak answers).
_SOURCE_SECTIONS = ("Story Anchor", "Raw Transcript Notes")


def extract_source(entry_text: str) -> str:
    """Pull the factual source sections out of an entry's markdown."""
    chunks: list[str] = []
    for name in _SOURCE_SECTIONS:
        m = re.search(rf"^##\s+{re.escape(name)}\s*$(.*?)(?=^##\s|\Z)", entry_text,
                      flags=re.MULTILINE | re.DOTALL)
        if m:
            body = m.group(1).strip()
            if body:
                chunks.append(f"{name}:\n{body}")
    return "\n\n".join(chunks)


_GEN_SYSTEM = (
    "You are this endurance coach:\n\n{coach_prompt}\n\n"
    "---\n\n"
    "You are handed a short debrief of one athlete's session (source S1 below). "
    "Emit the claims you would make about THIS athlete, as a coach, one per line, "
    "tagged exactly like:\n"
    "[[claim id=c1 type=fact cite=S1]] <text>\n"
    "[[claim id=c2 type=interpretation conf=7]] <text>\n"
    "Rules: type is fact (stated as fact about the athlete, cite=S1), interpretation "
    "(your read, conf 1-10, no cite), or conceptual (general principle, cite=S1 if from "
    "the debrief). Include the interpretations you are genuinely tempted to make -- do "
    "NOT sanitise them into safe hedges; assign an honest confidence. 3-6 claims. "
    "Output ONLY the tagged lines."
)


def generate_claims(source: str, coach_prompt: str, *, model: str = "claude-sonnet-4-6") -> list[Claim]:
    raw = call_llm(system=_GEN_SYSTEM.format(coach_prompt=coach_prompt),
                   user=f"S1 (athlete debrief):\n{source}", model=model)
    claims, _ = segment(raw)
    return claims


def sample_claims(rows: list[dict], *, sample: int, seed: int) -> list[dict]:
    """Sample rows, weighting interpretations (the calibration-relevant ones)."""
    rng = random.Random(seed)
    interp = [r for r in rows if r["type"] == ClaimType.INTERPRETATION.value]
    other = [r for r in rows if r["type"] != ClaimType.INTERPRETATION.value]
    rng.shuffle(interp)
    rng.shuffle(other)
    n_other = max(0, min(len(other), sample // 5))  # ~20% facts/conceptual for contrast
    picked = interp[: sample - n_other] + other[:n_other]
    rng.shuffle(picked)
    return picked


def _excerpt(source: str, limit: int = 280) -> str:
    flat = " ".join(source.split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def build(*, entries_dir: Path, n_entries: int, sample: int, seed: int,
          coach_prompt_path: Path, model: str) -> list[dict]:
    coach_prompt = coach_prompt_path.read_text(encoding="utf-8")
    paths = sorted(entries_dir.glob("*.md"))
    rng = random.Random(seed)
    chosen = rng.sample(paths, min(n_entries, len(paths)))
    rows: list[dict] = []
    for p in chosen:
        source = extract_source(p.read_text(encoding="utf-8"))
        if not source:
            continue
        try:
            claims = generate_claims(source, coach_prompt, model=model)
        except Exception as e:  # noqa: BLE001 - skip a bad entry, keep going
            print(f"  ! {p.name}: generation failed: {e}")
            continue
        for c in claims:
            rows.append({
                "entry": p.stem,
                "source_full": source,   # full source kept for later judge scoring
                "claim": c.text,
                "type": c.type.value,
                "conf": c.confidence,
            })
        print(f"  {p.name}: {len(claims)} claims")
    return sample_claims(rows, sample=sample, seed=seed)


def _render_table(rows: list[dict]) -> str:
    lines = [
        "## Vault-grounded cases (peer-review)",
        "",
        "Generated from real entries via the coach-voice generator. Source = your own "
        "Story Anchor / Raw Transcript Notes. Fill **your verdict**: `assert` (fine as "
        "fact), `state_as_read` (fair as the coach's read, not fact), or `demote` "
        "(overreach — ask, don't assert).",
        "",
        "| # | Entry | Source (your words) | Coach claim | Type/conf | Your verdict |",
        "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        tc = r["type"] + (f" conf={r['conf']}" if r["conf"] is not None else "")
        src = _excerpt(r["source_full"]).replace("|", "\\|")
        claim = r["claim"].replace("|", "\\|")
        lines.append(f"| {i} | {r['entry']} | {src} | {claim} | {tc} | |")
    return "\n".join(lines) + "\n"


def _main() -> None:
    ap = argparse.ArgumentParser(description="Build a vault-grounded calibration set.")
    ap.add_argument("--entries-dir", default="/home/gonzalo/workspace/painforwisdom/painforwisdom/obsidian-vault/gonzalo-book/entries")
    ap.add_argument("--coach-prompt", default="services/coach/coach_prompt.md")
    ap.add_argument("--n-entries", type=int, default=12)
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--out", default="services/coach/eval/grounding/CALIBRATION_VAULT.md")
    args = ap.parse_args()

    rows = build(
        entries_dir=Path(args.entries_dir),
        n_entries=args.n_entries,
        sample=args.sample,
        seed=args.seed,
        coach_prompt_path=Path(args.coach_prompt),
        model=args.model,
    )
    out = Path(args.out)
    out.write_text(_render_table(rows), encoding="utf-8")
    sidecar = out.with_suffix(".jsonl")
    with sidecar.open("w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            f.write(json.dumps({"n": i, **r}, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(rows)} cases -> {out} (+ {sidecar.name} for scoring)")


if __name__ == "__main__":
    _main()
