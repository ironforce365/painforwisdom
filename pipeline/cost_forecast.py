"""Token + cost forecast for the LangGraph pipeline.

Run BEFORE any historical replay or batch regression. Estimates per-run input
and output tokens, API-path $ cost, subscription-path token quota share, and
recommends a safe replay batch size against the Anthropic Pro/Max 5-hour
quota window.

Usage:
    python -m pipeline.cost_forecast --transcript path/to/transcript.txt
    python -m pipeline.cost_forecast --video path/to/video.mp4   # transcribes first

The forecast is heuristic — real runs vary. Output tokens are estimated from
PoC observations + agent prompt structure. Cache write/read is modelled on
the empirical Sonnet 4.6 ~2,048-token threshold.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import litellm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "agents"

# Anthropic Pro/Max published quotas vary; use a conservative public estimate.
# Pro: ~10-40 Sonnet messages per 5h. Max 5x: ~50-200. Max 20x: ~200-800.
# Quota is messages, not tokens — but each call also counts against rolling
# input-token-per-minute (ITPM) limits. We forecast both.
PRO_MAX5_SONNET_MSGS_PER_5H = 200       # Max 5x typical
SONNET_ITPM_PER_MINUTE = 50_000          # documented per-org default-tier ITPM (approx)

# Pricing (USD per 1M tokens) — published for Sonnet 4.6 and Haiku 4.5.
PRICES = {
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_write": 1.25,
        "cache_read": 0.10,
    },
}


@dataclass
class StageForecast:
    name: str
    model: str
    prompt_path: Optional[Path]
    extra_user_tokens: int       # transcript / extraction / blog content fed in user msg
    output_tokens_est: int        # heuristic
    web_search: bool = False
    cache_engages: bool = True   # padded to >2048 tokens

    def system_token_count(self) -> int:
        if not self.prompt_path or not self.prompt_path.is_file():
            return 0
        body = _strip_frontmatter(self.prompt_path.read_text())
        return litellm.token_counter(model=self.model, text=body)

    def cached_input_tokens(self) -> int:
        # System prompt sits behind cache_control. After first call in the
        # 5-min window it's a cache READ. We forecast the steady state where
        # cache is warm for stages 2+ that share the *same* system prompt.
        return self.system_token_count() if self.cache_engages else 0

    def fresh_input_tokens(self) -> int:
        # User message + identity prefix (~12 tokens) — never cached.
        return self.extra_user_tokens + 12


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip()
    return text


def _transcribe(video_path: Path) -> Path:
    print(f"[forecast] transcribing {video_path.name} ...")
    proc = subprocess.run(
        ["./extract_transcription.sh", str(video_path)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    # Locate the produced transcript: extract_transcription.sh writes
    # transcript_<date>.txt next to the video by convention.
    out_lines = (proc.stdout or "").splitlines()
    for line in reversed(out_lines):
        if line.endswith(".txt") and Path(line).is_file():
            return Path(line)
    # Fallback: search for transcript_*.txt under cwd or video dir
    for cand in PROJECT_ROOT.glob("transcript_*.txt"):
        return cand
    raise RuntimeError("transcribe stage produced no transcript file we could locate")


def _stage_specs(transcript_text: str, extraction_report_est: str, blog_post_est: str) -> List[StageForecast]:
    transcript_tokens = litellm.token_counter(model="claude-sonnet-4-6", text=transcript_text)
    extraction_tokens = litellm.token_counter(model="claude-sonnet-4-6", text=extraction_report_est)
    _ = blog_post_est  # blog_post heuristic reserved for future per-stage chaining

    return [
        StageForecast(
            name="extract",
            model="claude-sonnet-4-6",
            prompt_path=AGENTS_DIR / "coaching-thought-extractor.md",
            extra_user_tokens=transcript_tokens,
            output_tokens_est=600,
        ),
        StageForecast(
            name="kb-curator",
            model="claude-sonnet-4-6",
            prompt_path=AGENTS_DIR / "kb-curator.md",
            extra_user_tokens=extraction_tokens + 400,  # + vault context skim
            output_tokens_est=400,
        ),
        StageForecast(
            name="writer",
            model="claude-sonnet-4-6",
            prompt_path=AGENTS_DIR / "painforwisdom-writer.md",
            extra_user_tokens=extraction_tokens + transcript_tokens,
            output_tokens_est=1500,
        ),
        StageForecast(
            name="research",
            model="claude-sonnet-4-6",
            prompt_path=AGENTS_DIR / "research-curator.md",
            extra_user_tokens=extraction_tokens + 200,
            output_tokens_est=1200,
            web_search=True,
        ),
        # validator runs as pure-Python audit by default — no LLM tokens.
        # Listed here for visibility only.
    ]


def forecast(transcript_text: str) -> Dict:
    # Heuristic placeholders for downstream stages' input.
    extraction_report_est = "## Content Quality\nStrong\n\n## Core Insight\n" + ("x " * 250)
    blog_post_est = "x " * 600

    stages = _stage_specs(transcript_text, extraction_report_est, blog_post_est)

    rows = []
    total_input_fresh = 0
    total_input_cached_read = 0
    total_input_cache_write = 0
    total_output = 0
    total_cost_api = 0.0

    for s in stages:
        sys_tokens = s.system_token_count()
        cache_writes = sys_tokens if s.cache_engages else 0  # first call writes
        cache_reads = 0  # forecast worst case: every stage writes its own (different system prompts)
        fresh = s.fresh_input_tokens()
        out = s.output_tokens_est

        price = PRICES.get(s.model, PRICES["claude-sonnet-4-6"])
        cost = (
            cache_writes * price["cache_write"]
            + cache_reads * price["cache_read"]
            + fresh * price["input"]
            + out * price["output"]
        ) / 1_000_000

        rows.append(
            {
                "stage": s.name,
                "model": s.model,
                "system_tokens": sys_tokens,
                "fresh_input_tokens": fresh,
                "cache_write_tokens": cache_writes,
                "cache_read_tokens": cache_reads,
                "output_tokens_est": out,
                "web_search": s.web_search,
                "api_cost_usd": round(cost, 4),
                "subscription_cost_usd": 0.0,
            }
        )

        total_input_fresh += fresh
        total_input_cached_read += cache_reads
        total_input_cache_write += cache_writes
        total_output += out
        total_cost_api += cost

    total_input_tokens = total_input_fresh + total_input_cached_read + total_input_cache_write
    sonnet_msg_count = sum(1 for s in stages if s.model == "claude-sonnet-4-6")
    quota_share_pct = (sonnet_msg_count / PRO_MAX5_SONNET_MSGS_PER_5H) * 100

    # ITPM check — if we burst all stages back-to-back, do we exceed per-min input cap?
    burst_input = total_input_tokens
    itpm_share_pct = (burst_input / SONNET_ITPM_PER_MINUTE) * 100

    max_safe_replays_per_5h = max(1, PRO_MAX5_SONNET_MSGS_PER_5H // sonnet_msg_count)

    return {
        "stages": rows,
        "totals": {
            "input_tokens": total_input_tokens,
            "fresh_input_tokens": total_input_fresh,
            "cache_write_tokens": total_input_cache_write,
            "cache_read_tokens": total_input_cached_read,
            "output_tokens_est": total_output,
            "api_cost_usd_per_run": round(total_cost_api, 4),
            "subscription_cost_usd_per_run": 0.0,
            "sonnet_msg_count_per_run": sonnet_msg_count,
            "quota_share_pct_per_run_estimate": round(quota_share_pct, 2),
            "itpm_share_pct_burst": round(itpm_share_pct, 2),
            "max_safe_replays_per_5h_window": max_safe_replays_per_5h,
            "assumptions": {
                "pro_max5_sonnet_msgs_per_5h": PRO_MAX5_SONNET_MSGS_PER_5H,
                "sonnet_itpm_per_minute": SONNET_ITPM_PER_MINUTE,
                "validator": "pure-Python (no LLM tokens)",
                "notion_stages": "REST API (no LLM tokens)",
                "transcribe": "local Whisper (no LLM tokens)",
            },
        },
    }


def render_markdown(report: Dict) -> str:
    lines = ["# Pipeline Cost Forecast", ""]
    t = report["totals"]
    lines.append("## Per-run totals")
    lines.append(f"- Input tokens (incl. cache): **{t['input_tokens']:,}**")
    lines.append(f"  - Fresh: {t['fresh_input_tokens']:,}")
    lines.append(f"  - Cache writes: {t['cache_write_tokens']:,}")
    lines.append(f"  - Cache reads: {t['cache_read_tokens']:,}")
    lines.append(f"- Output tokens (est.): **{t['output_tokens_est']:,}**")
    lines.append(f"- **API-path cost: ${t['api_cost_usd_per_run']:.4f} / run**")
    lines.append(f"- **Subscription-path cost: $0.00 / run** (Pro/Max)")
    lines.append("")
    lines.append("## Subscription quota impact (Pro/Max 5x assumed)")
    lines.append(f"- Sonnet messages per run: **{t['sonnet_msg_count_per_run']}**")
    lines.append(f"- 5h-window quota share: **~{t['quota_share_pct_per_run_estimate']:.1f}%** per run")
    lines.append(f"- Max safe replays per 5h window: **~{t['max_safe_replays_per_5h_window']}**")
    lines.append(f"- ITPM burst share (back-to-back): ~{t['itpm_share_pct_burst']:.1f}%")
    lines.append("")
    lines.append("## Per-stage breakdown")
    lines.append("| Stage | Model | System | Fresh | Cache W | Cache R | Out | API $ |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in report["stages"]:
        lines.append(
            f"| {r['stage']} | {r['model']} | {r['system_tokens']:,} | "
            f"{r['fresh_input_tokens']:,} | {r['cache_write_tokens']:,} | "
            f"{r['cache_read_tokens']:,} | {r['output_tokens_est']:,} | "
            f"${r['api_cost_usd']:.4f} |"
        )
    lines.append("")
    lines.append("## Assumptions")
    for k, v in t["assumptions"].items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--transcript", help="Path to transcript .txt")
    g.add_argument("--video", help="Path to video .mp4 (will transcribe first)")
    ap.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "pipeline" / "forecast.md"),
        help="Markdown output path",
    )
    ap.add_argument("--json", default=None, help="Optional JSON output path")
    args = ap.parse_args()

    if args.transcript:
        transcript_text = Path(args.transcript).read_text()
    else:
        tpath = _transcribe(Path(args.video).resolve())
        transcript_text = tpath.read_text()

    report = forecast(transcript_text)
    md = render_markdown(report)
    Path(args.out).write_text(md)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))

    print(md)
    print(f"[forecast] wrote {args.out}")


if __name__ == "__main__":
    main()
