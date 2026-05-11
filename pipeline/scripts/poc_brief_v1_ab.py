"""One-off: rebuild v1 hormesis/seneca/goggins content in v2 format for A/B audio test."""
from __future__ import annotations

import sys

from pipeline.scripts.poc_brief_v2 import run_cluster

CLUSTER = {
    "theme": "deliberate-discomfort",
    "sub_angle": "Voluntary discomfort dose-response across mechanisms",
    "framing": (
        "Three independent voices on chosen suffering: a 21st-century neuroscience "
        "review of hormetic dose responses, a Stoic letter from Seneca on planned "
        "poverty, and David Goggins' lived protocol on the Huberman Lab podcast. "
        "What changes — in the body, in the mind, in the sense of self — when "
        "discomfort is *opportunistic and rotated* rather than escaped?"
    ),
    "vault_entry": "2026-02-20-opportunistic-discomfort",
    "sources": [
        {
            "slug": "hormesis-paper",
            "title": "Hormesis: A Fundamental Concept in Biology",
            "author_host": "Edward J. Calabrese, Mark P. Mattson",
            "type": "Paper",
            "specific_location": "Microbial Cell, vol. 4(5), 2017",
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC5354598/",
            "research_angle": "Mechanism of low-dose stress producing protective adaptation across biological systems.",
            "relevance": (
                "The biological substrate for why voluntary discomfort works at all. "
                "Hormesis is not a metaphor — it's the dose-response curve underneath "
                "every deliberate-discomfort practice."
            ),
        },
        {
            "slug": "seneca-letter18",
            "title": "Letter 18 — On Festivals and Fasting",
            "author_host": "Seneca",
            "type": "Article",
            "specific_location": "Letters from a Stoic, Letter 18 (c. 65 CE)",
            "url": "https://therealizedman.com/seneca-letter-18-the-merits-of-voluntary-discomfort/",
            "research_angle": "Voluntary poverty as preparedness training: rehearse the feared condition so the fear loses its grip.",
            "relevance": (
                "The earliest written articulation of pre-loading discomfort — the "
                "philosophical taproot of every modern \"do hard things\" practice."
            ),
        },
        {
            "slug": "goggins-huberman",
            "title": "David Goggins: How to Build Immense Inner Strength",
            "author_host": "Andrew Huberman / David Goggins",
            "type": "Podcast",
            "specific_location": "Huberman Lab, full transcript",
            "url": "https://www.hubermanlab.com/episode/david-goggins-how-to-build-immense-inner-strength",
            "research_angle": "First-person practice: how a single person operationalizes voluntary discomfort as a daily protocol, including the inner-dialogue mechanics.",
            "relevance": (
                "What the lived application looks like when the dose-response curve "
                "from the Calabrese/Mattson paper meets the Stoic disposition from "
                "Seneca — at the level of one human's daily decisions."
            ),
        },
    ],
}


def main() -> int:
    run_cluster(CLUSTER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
