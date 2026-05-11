"""Track A round 2: build constraints-as-redirectors + guilt-recovery clusters."""
from __future__ import annotations

import sys

from pipeline.scripts.poc_brief_v2 import run_cluster

CLUSTERS = [
    {
        "theme": "constraints-as-redirectors",
        "sub_angle": "Fear override when constraints redirect to scarier action",
        "framing": (
            "What forces a person into discomfort they would never pick voluntarily? "
            "Two converging sources on the override mechanism: the Tenacious Brain "
            "paper on aMCC as the neural substrate for choosing aversive effort, and "
            "the Goggins/Huberman conversation on what daily fear-override looks like "
            "from the inside. Where does an externally-imposed constraint act on the "
            "same circuitry as a self-imposed protocol?"
        ),
        "vault_entry": "2026-03-12-constraints-as-redirectors",
        "sources": [
            {
                "slug": "amcc-tenacious-brain",
                "title": "The Tenacious Brain: How the Anterior Mid-Cingulate Cortex Contributes to Achieving Goals",
                "author_host": "Touroutoglou, Andreano, Dickerson, Feldman Barrett",
                "type": "Paper",
                "specific_location": "Cortex, vol. 123 (2020), pp. 12-29",
                "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7381101/",
                "research_angle": "aMCC and fear override",
                "relevance": (
                    "The neural substrate behind choosing the scarier option. aMCC "
                    "structure and function predict the ability to convert anticipated "
                    "aversive cost into volitional action."
                ),
            },
            {
                "slug": "goggins-huberman",
                "title": "David Goggins: How to Build Immense Inner Strength",
                "author_host": "Andrew Huberman / David Goggins",
                "type": "Podcast",
                "specific_location": "Huberman Lab, full transcript",
                "url": "https://www.hubermanlab.com/episode/david-goggins-how-to-build-immense-inner-strength",
                "research_angle": "aMCC and fear override",
                "relevance": (
                    "First-person mechanics of choosing the harder available option "
                    "every day — including the inner-dialogue protocol that fires when "
                    "circumstances push you toward something you would normally avoid."
                ),
            },
        ],
    },
    {
        "theme": "guilt-recovery",
        "sub_angle": "Releasing guilt when discipline becomes identity protection",
        "framing": (
            "Three voices on the disciplined person's hardest override: choosing to "
            "stop without paying for it in guilt. A psychiatry classic on the "
            "cognitive distortions that manufacture guilt (Burns, Feeling Good), a "
            "behavior-design rule for recovering agency after a lapse (Clear's "
            "'never miss twice'), and a long-form interview with the perfectionism "
            "researcher Thomas Curran on why the 'I never stop' identity is more "
            "self-protection than excellence."
        ),
        "vault_entry": "2026-04-07-rest-without-guilt",
        "sources": [
            {
                "slug": "hiddenbrain-curran",
                "title": "Hidden Brain — 'Escaping Perfectionism' with Thomas Curran",
                "author_host": "Shankar Vedantam / Thomas Curran",
                "type": "Podcast",
                "specific_location": "Episode aired September 1, 2025; full transcript",
                "url": "https://hiddenbrain.org/podcast/escaping-perfectionism/",
                "research_angle": "identity-based-self-criticism",
                "relevance": (
                    "Curran's research argues perfectionism is a self-protection "
                    "strategy that withdraws effort to preserve identity. Directly "
                    "names the mechanism behind 'I never stop' as identity defence, "
                    "not discipline."
                ),
            },
            {
                "slug": "atomic-habits-never-miss-twice",
                "title": "Atomic Habits — Ch. 16: 'How to Stick with Good Habits Every Day' (Never Miss Twice)",
                "author_host": "James Clear",
                "type": "Book",
                "specific_location": "Ch. 16 — the 'never miss twice' rule",
                "url": "https://jamesclear.com/atomic-habits",
                "research_angle": "agency-restoration-after-lapse",
                "relevance": (
                    "Operational rule for recovering agency without spiral after a "
                    "skipped day — name the lapse, do not extend it, do not "
                    "compensate. Aligns with rest-without-guilt as a one-step "
                    "behaviour, not a moral act."
                ),
            },
            {
                "slug": "feeling-good-defeating-guilt",
                "title": "Feeling Good — Ch. 8: 'Ways of Defeating Guilt'",
                "author_host": "David D. Burns",
                "type": "Book",
                "specific_location": "Chapter 8 of Feeling Good",
                "url": "https://feelinggood.com/",
                "research_angle": "guilt-spiral-cognitive-distortion",
                "relevance": (
                    "CBT-era source on the specific cognitive distortions that "
                    "convert a missed session into compounding guilt. Names the "
                    "distortions by type so they can be detected at point of "
                    "occurrence."
                ),
            },
        ],
    },
]


def main() -> int:
    for cluster in CLUSTERS:
        run_cluster(cluster)
    print("\n[poc-v2-round2] both clusters done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
