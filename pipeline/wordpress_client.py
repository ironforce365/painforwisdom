"""WordPress.com REST API v2 thin client.

Targets the public-api.wordpress.com REST surface for a single site so it
works with both ``painforwisdom.wordpress.com`` and any other WP.com
hosted blog.

Auth modes (set via ``WORDPRESS_AUTH_MODE``):
  - ``app_password`` (default): HTTP Basic with
    ``WORDPRESS_USERNAME`` / ``WORDPRESS_APP_PASSWORD``. Fastest to set
    up, only viable on the paid WP.com plan.
  - ``oauth2``: Bearer with ``WORDPRESS_OAUTH_TOKEN``.

Heads-up: WordPress.com **free plan blocks write operations**. The
pipeline therefore defaults to dormant mode (``WORDPRESS_ENABLED!=true``)
and never calls this client until Gonzalo upgrades to Personal.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


class WordPressClientError(RuntimeError):
    """Raised on auth misconfiguration or API errors."""


@dataclass
class MediaUploadResult:
    media_id: int
    source_url: str
    raw: Dict[str, Any]


@dataclass
class PostResult:
    post_id: int
    url: str
    raw: Dict[str, Any]


def _public_api_base(site: str) -> str:
    site = site.strip().rstrip("/")
    # public-api.wordpress.com/wp/v2/sites/<host> works for WP.com sites.
    return f"https://public-api.wordpress.com/wp/v2/sites/{site}"


def _auth_for(mode: str) -> httpx.Auth:
    if mode == "app_password":
        user = os.environ.get("WORDPRESS_USERNAME", "")
        pwd = os.environ.get("WORDPRESS_APP_PASSWORD", "")
        if not user or not pwd:
            raise WordPressClientError(
                "WORDPRESS_USERNAME / WORDPRESS_APP_PASSWORD missing for app_password auth."
            )
        return httpx.BasicAuth(user, pwd)
    if mode == "oauth2":
        token = os.environ.get("WORDPRESS_OAUTH_TOKEN", "")
        if not token:
            raise WordPressClientError("WORDPRESS_OAUTH_TOKEN missing for oauth2 auth.")
        return _BearerAuth(token)
    raise WordPressClientError(f"Unknown WORDPRESS_AUTH_MODE={mode!r}")


class _BearerAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request):  # type: ignore[override]
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


class WordPressClient:
    """Stateless wrapper. Construct once per node call."""

    def __init__(
        self,
        site: Optional[str] = None,
        auth_mode: Optional[str] = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.site = site or os.environ.get("WORDPRESS_SITE") or "painforwisdom.wordpress.com"
        self.auth_mode = (auth_mode or os.environ.get("WORDPRESS_AUTH_MODE") or "app_password").strip().lower()
        self.base_url = _public_api_base(self.site)
        self._auth = _auth_for(self.auth_mode)
        self._client = httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WordPressClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    # ---- HTTP helpers --------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = self._client.request(method, url, auth=self._auth, **kwargs)
        except httpx.HTTPError as exc:
            raise WordPressClientError(f"{method} {url} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise WordPressClientError(
                f"{method} {url} returned {resp.status_code}: {resp.text[:600]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise WordPressClientError(
                f"{method} {url} returned non-JSON: {resp.text[:200]}"
            ) from exc

    # ---- Public surface ------------------------------------------------

    def upload_media(self, image_path: Path, alt_text: str = "") -> MediaUploadResult:
        """POST /wp/v2/media — uploads a binary, returns the new media object."""
        if not image_path.is_file():
            raise WordPressClientError(f"image not found: {image_path}")
        mime, _ = mimetypes.guess_type(image_path.name)
        mime = mime or "application/octet-stream"
        headers = {
            "Content-Disposition": f'attachment; filename="{image_path.name}"',
            "Content-Type": mime,
        }
        with image_path.open("rb") as f:
            data = f.read()
        url = f"{self.base_url}/media"
        try:
            resp = self._client.post(url, content=data, headers=headers, auth=self._auth)
        except httpx.HTTPError as exc:
            raise WordPressClientError(f"media upload failed: {exc}") from exc
        if resp.status_code >= 400:
            raise WordPressClientError(
                f"media upload returned {resp.status_code}: {resp.text[:600]}"
            )
        payload = resp.json()
        media_id = int(payload.get("id") or 0)
        source_url = str(payload.get("source_url") or "")
        if alt_text:
            try:
                self._request(
                    "POST", f"/media/{media_id}", json={"alt_text": alt_text}
                )
            except WordPressClientError as exc:
                # alt-text is decorative; don't fail the whole upload over it.
                print(f"[wordpress] WARN alt_text patch failed for media {media_id}: {exc}")
        return MediaUploadResult(media_id=media_id, source_url=source_url, raw=payload)

    def create_draft_post(
        self,
        *,
        title: str,
        html: str,
        excerpt: str = "",
        tags: Optional[List[str]] = None,
        featured_media_id: Optional[int] = None,
        slug: Optional[str] = None,
    ) -> PostResult:
        """POST /wp/v2/posts with status="draft"."""
        body: Dict[str, Any] = {
            "title": title,
            "content": html,
            "status": "draft",
            "excerpt": excerpt,
        }
        if tags:
            body["tags"] = self._resolve_tag_ids(tags)
        if featured_media_id:
            body["featured_media"] = featured_media_id
        if slug:
            body["slug"] = slug
        payload = self._request("POST", "/posts", json=body)
        post_id = int(payload.get("id") or 0)
        url = str(payload.get("link") or "")
        return PostResult(post_id=post_id, url=url, raw=payload)

    def update_post(self, post_id: int, **fields: Any) -> Dict[str, Any]:
        """PATCH /wp/v2/posts/<id> for backfill amendments."""
        return self._request("POST", f"/posts/{post_id}", json=fields)

    # ---- Tag helpers ---------------------------------------------------

    def _resolve_tag_ids(self, names: List[str]) -> List[int]:
        """For each tag name, look it up; create if missing. Returns the int IDs."""
        ids: List[int] = []
        for name in names:
            name = name.strip()
            if not name:
                continue
            existing = self._request("GET", "/tags", params={"search": name, "per_page": 5})
            tag_id: Optional[int] = None
            if isinstance(existing, list):
                for tag in existing:
                    if str(tag.get("name", "")).strip().lower() == name.lower():
                        tag_id = int(tag.get("id") or 0)
                        break
            if tag_id is None:
                try:
                    created = self._request("POST", "/tags", json={"name": name})
                    tag_id = int(created.get("id") or 0)
                except WordPressClientError as exc:
                    print(f"[wordpress] WARN failed to create tag {name!r}: {exc}")
                    continue
            if tag_id:
                ids.append(tag_id)
        return ids


# ---- Markdown / HTML helpers ---------------------------------------------


def markdown_to_html(md: str) -> str:
    """Convert blog post markdown into WP-friendly HTML.

    Uses the optional ``markdown`` package when installed; falls back to a
    minimal converter (paragraphs + bold) when not. The fallback is good
    enough for Gonzalo's dense prose-style posts but the dependency is
    listed in requirements.txt for fidelity.
    """
    try:
        import markdown as _markdown  # type: ignore
        return _markdown.markdown(
            md,
            extensions=["fenced_code", "footnotes", "tables", "sane_lists"],
            output_format="html",
        )
    except ImportError:
        return _fallback_markdown_to_html(md)


_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _fallback_markdown_to_html(md: str) -> str:
    paragraphs: List[str] = []
    for para in re.split(r"\n\s*\n", md.strip()):
        text = para.strip()
        if not text:
            continue
        text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)
        text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
        text = _ITALIC_RE.sub(r"<em>\1</em>", text)
        text = text.replace("\n", "<br/>")
        paragraphs.append(f"<p>{text}</p>")
    return "\n".join(paragraphs)


def first_50_words(text: str, *, max_words: int = 50) -> str:
    """Return a clean excerpt of at most ``max_words`` words.

    Stops at sentence boundary close to the limit so the excerpt does not
    end mid-clause. Strips bold / italic markup and date stamps.
    """
    cleaned = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    cleaned = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", cleaned)
    cleaned = re.sub(r"^\s*\*?\d{1,2}/\d{1,2}/\d{2,4}\.?\*?\s*", "", cleaned, count=1)
    cleaned = cleaned.replace("\n", " ").strip()

    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned
    truncated = " ".join(words[:max_words])
    # Walk back to last sentence-ending punctuation, if close.
    end = max(
        truncated.rfind(". "),
        truncated.rfind("! "),
        truncated.rfind("? "),
    )
    if end >= len(truncated) - 80 and end > 0:
        return truncated[: end + 1]
    return truncated + "…"


def render_wp_html(
    *,
    body_md: str,
    date_mmddyy: str,
    featured_image_url: str = "",
    youtube_url_placeholder: str = "[YOUTUBE_SHORT_URL]",
) -> str:
    """Render the body markdown into the painforwisdom blog post layout.

    Structure:
      - italicized date line containing the YouTube placeholder
      - optional featured image (skipped here because WP renders the
        ``featured_media`` separately at the top of the post)
      - body paragraphs (markdown→HTML)
    """
    body_md = body_md.strip()

    # Drop the *MM/DD/YY.* line if it appears at the very top — we render
    # our own first line with the YouTube placeholder.
    body_md = re.sub(
        r"^\s*\*?\s*\d{1,2}/\d{1,2}/\d{2,4}\.?\s*\*?\s*\n+",
        "",
        body_md,
        count=1,
    )

    body_html = markdown_to_html(body_md)
    date_line_html = (
        f'<p><em>{date_mmddyy} — <a href="{youtube_url_placeholder}">'
        f'YouTube short</a></em></p>'
    )
    parts: List[str] = [date_line_html]
    if featured_image_url:
        parts.append(
            f'<p><img src="{featured_image_url}" alt="featured image"/></p>'
        )
    parts.append(body_html)
    return "\n".join(parts)


def write_dormant_bundle(
    out_dir: Path,
    *,
    title: str,
    body_md: str,
    html: str,
    excerpt: str,
    tags: List[str],
    featured_image_path: Optional[Path],
    metadata: Dict[str, Any],
) -> Dict[str, str]:
    """Materialise the WP-ready bundle on disk so a dormant pipeline run still
    leaves something Gonzalo can paste into WordPress manually.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "post.md").write_text(body_md)
    (out_dir / "post.html").write_text(html)
    meta = {
        "title": title,
        "excerpt": excerpt,
        "tags": tags,
        "featured_image": str(featured_image_path) if featured_image_path else "",
        **metadata,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    paths = {
        "bundle_dir": str(out_dir),
        "post_md": str(out_dir / "post.md"),
        "post_html": str(out_dir / "post.html"),
        "meta_json": str(out_dir / "meta.json"),
    }
    return paths
