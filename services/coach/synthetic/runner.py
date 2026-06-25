"""Synthetic-conversation runner.

Drives a `Profile` through a multi-turn conversation with the coach:

    opener → coach reply → driver reply → coach reply → ...

Every turn runs on the **test channel** (`coach.turn(..., channel="test")`) so the
coach skips the vault inbox, and every exchange is logged to the conversation log
flagged ``test=True`` so it surfaces in the monitor UI clearly marked as a test.

`run_profile` is the single-conversation engine (everything injected, fully
tested offline). `run_many` fans profiles out across a thread pool so many
personas hit the coach at once (scalability) — each gets its own CoachClient and
its own ``synthetic-<slug>`` id. There is also a CLI entrypoint (`python -m
synthetic.runner`) for live runs.
"""
from __future__ import annotations

import argparse
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from synthetic.driver import persona_reply
from synthetic.profiles import Profile, load_profiles_dir

log = logging.getLogger("coach.synthetic")

# Used when the driver fails to produce a message — keeps a long run alive instead
# of aborting the whole conversation on one flaky CLI call.
_FALLBACK_REPLY = "Can you say a bit more about that?"


def run_profile(
    profile: Profile,
    *,
    coach,
    convo,
    reply_fn: Callable[[Profile, list[dict]], str],
    user_id: str | None = None,
) -> dict:
    """Run one full synthetic conversation. Returns a small summary dict."""
    uid = user_id or profile.user_id()
    history: list[dict] = []
    user_msg = profile.opener
    for turn in range(profile.turn_count):
        convo.append(uid, "user", user_msg, name=profile.name, test=True)
        history.append({"role": "user", "content": user_msg})

        result = coach.turn(uid, user_msg, channel="test")
        coach_reply = ((result or {}).get("reply") or "").strip()
        convo.append(uid, "coach", coach_reply, test=True)
        history.append({"role": "assistant", "content": coach_reply})

        if turn < profile.turn_count - 1:
            try:
                user_msg = reply_fn(profile, history) or _FALLBACK_REPLY
            except Exception:
                log.exception("driver reply failed for %s; using fallback", uid)
                user_msg = _FALLBACK_REPLY
    return {"user_id": uid, "turns": profile.turn_count, "history": history}


def run_many(
    profiles: list[Profile],
    *,
    make_coach: Callable[[], object],
    convo,
    reply_fn: Callable[[Profile, list[dict]], str],
    concurrency: int = 4,
) -> list[dict]:
    """Run several profiles concurrently (one thread + one coach client each)."""
    results: list[dict] = []

    def _one(profile: Profile) -> dict:
        return run_profile(profile, coach=make_coach(), convo=convo, reply_fn=reply_fn)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for res in pool.map(_one, profiles):
            results.append(res)
    return results


def main() -> int:  # pragma: no cover - thin CLI glue over tested pieces
    """Live entrypoint: load profiles and drive them against a running coach.

    The conversations appear in the monitor UI badged as tests and never touch the
    inbox. Optionally `--reset` wipes each synthetic user's coach session + mem0
    afterwards so repeated runs start clean.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Drive synthetic users against the coach")
    ap.add_argument("--profiles-dir", default=str(Path(__file__).parent / "profiles"))
    ap.add_argument("--coach-url", default=os.environ.get("COACH_AGENT_URL", "http://coach-agent:8800"))
    ap.add_argument("--convo-dir", default=os.environ.get("COACH_CONVO_LOG_DIR", "/state/conversations"))
    ap.add_argument("--concurrency", type=int, default=int(os.environ.get("SYNTHETIC_CONCURRENCY", "3")))
    ap.add_argument("--turns", type=int, default=None, help="override every profile's turn_count")
    ap.add_argument("--model", default=os.environ.get("SYNTHETIC_MODEL", "sonnet"))
    ap.add_argument("--reset", action="store_true", help="wipe synthetic users' coach state after the run")
    args = ap.parse_args()

    from telegram_bot.coach_client import CoachClient
    from telegram_bot.conversation_log import ConversationLog

    profiles = load_profiles_dir(args.profiles_dir, turn_count=args.turns)
    if not profiles:
        log.error("no profiles found in %s", args.profiles_dir)
        return 1
    convo = ConversationLog(Path(args.convo_dir))

    def reply_fn(profile: Profile, history: list[dict]) -> str:
        return persona_reply(profile, history, model=args.model)

    log.info("driving %d profile(s) @ concurrency=%d against %s",
             len(profiles), args.concurrency, args.coach_url)
    results = run_many(
        profiles,
        make_coach=lambda: CoachClient(args.coach_url),
        convo=convo,
        reply_fn=reply_fn,
        concurrency=args.concurrency,
    )
    total_turns = sum(r["turns"] for r in results)
    log.info("done: %d conversations, %d turns", len(results), total_turns)

    if args.reset:
        resetter = CoachClient(args.coach_url)
        for r in results:
            try:
                resetter.reset(r["user_id"])
            except Exception:
                log.exception("reset failed for %s", r["user_id"])
        log.info("reset %d synthetic users", len(results))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
