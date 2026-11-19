"""Export every public painforwisdom.wordpress.com post into the blog site repo.

Pulls posts through the public WP.com REST API (no auth), converts the post HTML
to markdown, downloads featured and inline images, and rewrites links between
posts to the site's ``/YYYY/MM/DD/slug/`` paths
(docs/superpowers/specs/2026-11-18-own-blog-design.md section 6). Same layout and
front-matter as pipeline/publish/blog.py, plus ``wordpress_url``.

Output under --out (the site repo root):
  src/content/posts/<slug>.md   front-matter + body
  public/images/<file>          featured image ``<slug>.<ext>``, inline images ``<slug>-<n>.<ext>``

Fails loudly on an HTML tag it does not know or a link to a post that is not in
the export, so nothing is dropped silently.

Usage:
  python -m pipeline.scripts.migrate_wordpress_posts --out /path/to/painforwisdom-site
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx
import yaml
from lxml import html as lhtml

SITE = "painforwisdom.wordpress.com"
API = f"https://public-api.wordpress.com/rest/v1.1/sites/{SITE}/posts"
PAGE_SIZE = 100
IMAGE_WIDTH = 2048  # WP.com resizes on ?w=; some originals are 23 MB

# Editor links ("wordpress.com/post/<site>/<id>") also appear in post bodies.
_POST_PATH = re.compile(r"^/(\d{4})/(\d{2})/(\d{2})/([^/]+)/?$")
_EDITOR_PATH = re.compile(rf"^/post/{re.escape(SITE)}/(\d+)/?$")
_INLINE_TAGS = {"a", "strong", "b", "em", "i", "sup", "br", "span"}


def fetch_posts(client: httpx.Client) -> list[dict]:
    posts: list[dict] = []
    page = 1
    while True:
        r = client.get(API, params={"number": PAGE_SIZE, "page": page, "order": "ASC", "order_by": "date"})
        r.raise_for_status()
        data = r.json()
        posts.extend(data["posts"])
        if not data["posts"] or len(posts) >= data["found"]:
            break
        page += 1
    if len(posts) != data["found"]:
        raise RuntimeError(f"fetched {len(posts)} posts, API reports {data['found']}")
    return posts


def site_path(post: dict) -> str:
    """The post's path on the new site: the WordPress path, which the site keeps."""
    path = urlsplit(post["URL"]).path
    m = _POST_PATH.match(path)
    if not m or m.group(4) != post["slug"]:
        raise ValueError(f"post URL {post['URL']} is not /YYYY/MM/DD/{post['slug']}/")
    return "/{}/{}/{}/{}/".format(*m.groups())


class LinkResolver:
    """Maps a WordPress URL to the site path of the post it points at."""

    def __init__(self, posts: list[dict]):
        self.by_id = {p["ID"]: site_path(p) for p in posts}
        self.paths = set(self.by_id.values())

    def path_for(self, href: str) -> str | None:
        """Site path of the linked post; None for an external link. Raises on an internal link to an unknown post."""
        u = urlsplit(href)
        host = u.netloc.lower()
        if host == SITE:
            m = _POST_PATH.match(u.path)
            if m and "/{}/{}/{}/{}/".format(*m.groups()) in self.paths:
                return "/{}/{}/{}/{}/".format(*m.groups())
        elif host == "wordpress.com":
            m = _EDITOR_PATH.match(u.path)
            if m and int(m.group(1)) in self.by_id:
                return self.by_id[int(m.group(1))]
        elif host != "wp.me":
            return None
        raise ValueError(f"internal link does not resolve to an exported post: {href}")


def _strip_query(url: str) -> str:
    u = urlsplit(url)
    return urlunsplit((u.scheme, u.netloc, u.path, "", ""))


def clean_youtube(url: str) -> str:
    """Drops the ``si`` share-tracking parameter."""
    u = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(u.query) if k != "si"]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))


def _is_youtube(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return host.endswith("youtube.com") or host == "youtu.be"


def _escape(text: str) -> str:
    # "<" as an entity: not every markdown parser honours "\<".
    return re.sub(r"([\\`*_\[\]])", r"\\\1", text).replace("<", "&lt;")


class Converter:
    def __init__(self, slug: str, resolver: LinkResolver):
        self.slug = slug
        self.resolver = resolver
        self.footnote_ids: list[str] = []
        self.images: list[tuple[str, str]] = []  # (source URL, file name under images/)
        self.youtube_url: str | None = None

    def convert(self, content: str) -> str:
        root = lhtml.fragment_fromstring(content, create_parent="div")
        notes = root.xpath('.//ol[contains(@class, "wp-block-footnotes")]')
        for ol in notes:
            self.footnote_ids = [li.get("id") for li in ol.findall("li")]
        blocks = [self.block(el) for el in root if el not in notes]
        if (root.text or "").strip():
            raise ValueError(f"{self.slug}: bare text outside a block")
        for ol in notes:
            for n, li in enumerate(ol.findall("li"), 1):
                blocks.append(f"[^{n}]: {self.inline(li).strip()}")
        return "\n\n".join(b for b in blocks if b) + "\n"

    def block(self, el, depth: int = 0) -> str:
        tag = el.tag
        if tag == "p":
            return self.inline(el).strip()
        if tag in ("h2", "h3", "h4"):
            return "#" * int(tag[1]) + " " + self.inline(el).strip()
        if tag == "hr":
            return "---"
        if tag in ("ul", "ol"):
            return self.list_block(el, depth)
        if tag == "figure":
            imgs = el.findall(".//img")
            if len(imgs) != 1 or el.find(".//figcaption") is not None:
                raise ValueError(f"{self.slug}: unsupported figure")
            return self.image(imgs[0])
        raise ValueError(f"{self.slug}: unsupported block tag <{tag}>")

    def list_block(self, el, depth: int) -> str:
        lines = []
        for n, li in enumerate(el.findall("li"), 1):
            marker = f"{n}." if el.tag == "ol" else "-"
            nested = [c for c in li if c.tag in ("ul", "ol")]
            text = self.inline(li, skip=nested).strip()
            lines.append("   " * depth + f"{marker} {text}")
            for sub in nested:
                lines.append(self.list_block(sub, depth + 1))
        return "\n".join(lines)

    def image(self, img) -> str:
        src = _strip_query(img.get("src"))
        name = f"{self.slug}-{len(self.images) + 1}{PurePosixPath(urlsplit(src).path).suffix}"
        self.images.append((src, name))
        return f"![{_escape(img.get('alt') or '')}](/images/{name})"

    def inline(self, el, skip=()) -> str:
        out = [_escape(el.text or "")]
        for child in el:
            if child in skip:
                pass
            else:
                out.append(self.inline_el(child))
            out.append(_escape(child.tail or ""))
        return "".join(out)

    def inline_el(self, el) -> str:
        tag = el.tag
        if tag not in _INLINE_TAGS:
            raise ValueError(f"{self.slug}: unsupported inline tag <{tag}>")
        if tag == "br":
            return "  \n"  # two-space hard break: the one form every markdown parser supports
        if tag == "sup" and "fn" in (el.get("class") or "").split():
            ref = el.get("data-fn")
            if ref not in self.footnote_ids:
                raise ValueError(f"{self.slug}: footnote {ref} has no text")
            return f"[^{self.footnote_ids.index(ref) + 1}]"
        if tag == "a":
            href = el.get("href") or ""
            if href.startswith("#") and href.endswith("-link"):
                return ""  # footnote back-link
            text = self.inline(el)
            if _is_youtube(href) and self.youtube_url is None:
                self.youtube_url = clean_youtube(href)
            return f"[{text}]({self.resolver.path_for(href) or href})"
        inner = self.inline(el)
        if tag in ("strong", "b"):
            return _wrap(inner, "**")
        if tag in ("em", "i"):
            return _wrap(inner, "*")
        if tag == "sup":
            return f"<sup>{inner}</sup>"
        return inner  # span


def _wrap(inner: str, mark: str) -> str:
    """Emphasis must hug the text, so surrounding spaces move outside the markers."""
    core = inner.strip()
    if not core:
        return inner
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()):]
    return f"{lead}{mark}{core}{mark}{trail}"


def render_post(post: dict, resolver: LinkResolver) -> tuple[str, list[tuple[str, str]]]:
    """Returns the markdown file text and the (URL, file name) images it needs."""
    conv = Converter(post["slug"], resolver)
    body = conv.convert(post["content"])
    images = list(conv.images)
    featured = ""
    if post.get("featured_image"):
        src = _strip_query(post["featured_image"])
        featured = f"{post['slug']}{PurePosixPath(urlsplit(src).path).suffix.lower()}"
        images.append((src, featured))
    excerpt = " ".join(lhtml.fragment_fromstring(post.get("excerpt") or "<p></p>", create_parent="div").text_content().split())
    front = {  # keys of pipeline/publish/blog.py:_front_matter, plus wordpress_url
        "title": html.unescape(post["title"]),
        "date": "-".join(site_path(post).split("/")[1:4]),
        "slug": post["slug"],
        "excerpt": excerpt,
        "tags": list(post.get("tags") or {}),
        "youtube_url": conv.youtube_url or "",
        "image": featured,
        "wordpress_url": post["URL"],
    }
    head = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1000)
    return f"---\n{head}---\n\n{body}", images


def migrate(out: Path, client: httpx.Client) -> int:
    posts = fetch_posts(client)
    resolver = LinkResolver(posts)
    rendered = [(p, *render_post(p, resolver)) for p in posts]  # convert all before writing anything
    posts_dir, images_dir = out / "src" / "content" / "posts", out / "public" / "images"
    posts_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    for post, text, images in rendered:
        for url, name in images:
            dest = images_dir / name
            if not dest.exists():
                r = client.get(url, params={"w": IMAGE_WIDTH})
                r.raise_for_status()
                dest.write_bytes(r.content)
        (posts_dir / f"{post['slug']}.md").write_text(text, encoding="utf-8")
    return len(rendered)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export WordPress posts into the blog site repo.")
    ap.add_argument("--out", type=Path, required=True, help="site repo root")
    args = ap.parse_args(argv)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        n = migrate(args.out, client)
    print(f"wrote {n} posts to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
