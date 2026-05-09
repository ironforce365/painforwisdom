from typing import Any, Dict, List, TypedDict


class State(TypedDict, total=False):
    video_path: str
    transcript_path: str
    transcript_text: str
    extraction_report: str
    core_insight: str
    tweet: str
    metrics: List[Dict[str, Any]]
