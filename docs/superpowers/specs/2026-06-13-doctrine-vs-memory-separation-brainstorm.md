# Doctrine vs. Memory — separating "what the coach teaches" from "who it's talking to"

**2026-06-13 · brainstorm (not a plan yet) · triggered by a live-conversation bug**

## The bug

In a live turn the coach wrote:

> Could "Me costumbré a este tipo de dolor" be what stretched those four months
> of recovery the first time?

Gonzalo never told the coach about "four months of recovery" **in the
conversation**. The only place that fact lives is his Obsidian vault. So the
coach read his private journal, treated it as *memory of the person it's talking
to*, and asserted it back at him as established fact.

It "works" only by accident: the vault happens to be Gonzalo's. For any other
user that sentence is a fabrication. And even for Gonzalo it's wrong in kind —
it's regurgitating his journal, and it conflates "what I once wrote in my diary"
with "what I'm telling my coach right now."

**Gonzalo's principle (verbatim intent):**
- The **knowledge base is training material** — it teaches the coach how to
  connect effort, pain, reward, mindset, growth, suffering. It is *doctrine*.
- **Memory of a user is built ONLY from the conversation with that user.**
- These must never be the same store.

## Why the gate we just shipped does NOT catch this (be honest)

Stream 0/2's grounding gate verifies that every factual claim is *entailed by a
retrieved source*, where the source (`cite=S1`) **is the vault context**. So the
gate's rule is "a fact must have *a source* in the vault." This claim *has* a
vault source. **The gate would bless it.** The gate defends against pure
hallucination (facts with no source at all); it does **not** defend against
*source-role confusion* (using doctrine as if it were user memory). This bug is
orthogonal to, and older than, the gate — but the gate as shipped gives false
confidence here. That's the gap this brainstorm closes.

## Current architecture (verified 2026-06-13)

- **Vault RAG** (`agent/retrieval.py`, `vault_rag/builder.py`): `ObsidianReader`
  indexes `input_dir = vault_dir` **recursively over the whole vault root**, raw
  Obsidian files, sentence-chunked. Retrieved deterministically every turn,
  injected as `<vault_context>`. The prompt frames it as "Gonzalo's knowledge
  base… ground your reply in these" — i.e. as doctrine — but the index actually
  contains his raw first-person journal.
- **What's in the vault root being indexed:**
  - `gonzalo-book/deep-dive/` (202), `gonzalo-book/themes/` (16),
    `gonzalo-book/frameworks/` (8) — **distilled doctrine** (the teachings).
  - `thoughts/` (31, dated first-person reflections) and
    `gonzalo-book/entries/` (45) — **raw autobiography**.
  - `_inbox/` (51) — **coach conversation logs** (the coach's own past output
    being fed back in as "knowledge" — a second contamination).
- **mem0** (`mem0_mcp/`): a per-user (`user_id`-scoped) vector+graph fact store —
  **already the right shape for user memory** — but currently **dormant**: not
  read into the prompt, not written from turns, not vault-fed, not mentioned in
  the system prompt. It exists only as an optional MCP tool the model never calls.
- **No de-personalized lessons layer** feeds the agent. A themes classifier
  sidecar exists but only tags inbox items for Notion; it doesn't touch
  retrieval.

**Conclusion:** doctrine and user-fact are fully conflated in one index, and the
store that *should* hold user memory (mem0) is wired but inert.

## The principle, stated as an invariant

> A claim of the form "**you** did/felt/experienced X" may be asserted only if X
> is grounded in **conversation-derived user memory**. The vault may only ground
> claims of the form "**the lesson/pattern here is** Y" (principles), never
> biography about the interlocutor.

## Two-source model

| Source | Built from | Warrants | Injected as |
|---|---|---|---|
| **Doctrine (D)** | vault (de-personalized teachings) | conceptual / principle claims | `<doctrine>` |
| **Memory (M)** | this user's conversations only | facts about the user | `<about_this_user>` |

## Brainstorm — doctrine layer (teach without leaking biography)

- **D1 — Prompt reframe (cheap, today, partial).** Reframe the `<vault_context>`
  block + `coach_prompt.md`: "These are *teaching excerpts* — reflections written
  by someone else. Extract the principle; NEVER attribute the events or
  biography in them to the person you're talking to. You know nothing about this
  person except what they tell you in this conversation." Add a hard rule:
  "Never state a fact about the user they did not say themselves." Weak (LLMs
  still leak under momentum), but immediate risk reduction.
- **D2a — Index only the distilled dirs (cheap, structural, high-leverage).**
  Point the RAG builder at `gonzalo-book/deep-dive` + `themes` + `frameworks`
  only; **exclude `thoughts/`, `gonzalo-book/entries/`, `_inbox/`**. The
  deep-dives/themes/frameworks are already the de-personalized teachings, so
  biography can't be retrieved because it isn't in the index. Mostly a config +
  reindex change. **Caveat to verify:** sample the deep-dives — if they still
  carry heavy first-person biography, D2a alone isn't enough and we need D2/D3.
- **D2 — Distill a dedicated lessons corpus.** A pipeline that turns vault
  entries into transferable principles stripped of first-person events
  ("Pain present before and after effort but silent during it can mask
  accumulating tendon damage"). The coach retrieves from this. Strongest
  separation; most work; risks losing Gonzalo's texture/voice.
- **D3 — Type-tag spans at ingestion** (`principle | autobiography |
  bibliography`) and retrieve only `principle ∪ bibliography` for coaching.
  Extends the existing themes classifier. Granular; medium effort.

## Brainstorm — user memory (activate the dormant mem0)

- **M1 — Turn mem0 into conversation-only memory (the core build).**
  (a) after each turn, extract durable facts the *user* stated and `mem0_add`
  (scoped `user_id`); (b) at turn start, pre-retrieve relevant user memories and
  inject a separate `<about_this_user>` block, distinct from doctrine. Source =
  conversation only; the vault never writes to mem0. This is where "you told me
  the Achilles has bothered you all week" legitimately lives, and it persists
  across sessions (today the in-memory SessionMap forgets on restart).
- **M2 — Opt-in history import for the owner.** Gonzalo may genuinely want the
  coach to remember his Achilles history. Provide an explicit, deliberate import
  that loads chosen vault facts into *his* user-memory, clearly marked as memory
  — never the implicit retrieval path. Default off. Keeps the invariant clean
  while letting him seed his own memory on purpose.

## Brainstorm — enforcement (make the gate source-aware — reuses what we shipped)

The gate's bones are right; upgrade its source model:
- Split the single `S1` into source **kinds**: `D*` (doctrine) and `M*` (memory).
- Claim contract: `cite=D1` for conceptual; `cite=M1` for facts about the user.
- `decide()` rule: a **fact** citing only doctrine (`D`) → **demote** (not
  legitimately known about this user); a fact citing memory (`M`) and entailed →
  assert; conceptual citing `D` → fine.
- Effect on the bug: "four months" is a fact with only `D` support → **demoted to
  a question** ("Did an earlier round of this cost you months?") instead of
  asserted. Exactly the desired behavior — and it turns Gonzalo's principle into
  a machine-checked invariant.

The gate's value proposition sharpens from "facts need *a* source" to "facts
about the user need a *user-memory* source."

## Side issue surfaced

`_inbox/` (coach conversation logs) is currently in the retrieval index — the
coach's own past replies are being recycled as "knowledge." Exclude it
regardless of which doctrine option we pick.

## Proposed sequencing (for discussion)

1. **Today / cheap:** D1 prompt reframe + the "never assert user-biography"
   rule. Drop `_inbox/` (and likely `thoughts/`, `entries/`) from the index
   (D2a) and reindex — pending a quick read of a few deep-dives to confirm
   they're clean enough.
2. **Core build:** M1 — activate mem0 as conversation-only memory with its own
   `<about_this_user>` block + cross-session persistence.
3. **Enforce:** upgrade the gate to source-typed warrants (D vs M).
4. **Later / optional:** D2 distillation or D3 type-tagging if deep-dives leak;
   M2 opt-in owner import.

## Open forks (need Gonzalo)

1. Are the **deep-dives clean doctrine**, or do they carry first-person
   biography too? (Decides whether D2a suffices or we need D2/D3.) — *I can
   sample them.*
2. Should the coach **remember Gonzalo across sessions at all**? Conversation-
   derived cross-session memory (M1) is legit and still honors "memory =
   conversation." Vault-seeded memory needs the explicit M2 opt-in. Which does
   he want?
3. **Sequencing:** ship the cheap D1+D2a mitigation now (with the gate already
   live), or hold and do the structural M1+gate-typing together?
