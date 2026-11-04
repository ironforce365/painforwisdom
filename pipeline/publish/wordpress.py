"""WordPress adapter: media, post, then the Notion write-back of the url (spec 2026-11-03-console-operability §6.3)."""
from __future__ import annotations

from pathlib import Path

from pipeline.notion_client import update_blog_page_wordpress_url
from pipeline.publish.package import package_path
from pipeline.publishing_config import current_profile, load
from pipeline.wordpress_client import WordPressClient, render_wp_html

YOUTUBE_PLACEHOLDER = "[YOUTUBE_SHORT_URL]"


def publish(run_dir: Path, package: dict) -> dict:
    fields, readonly = package["fields"], package["readonly"]
    config = load(current_profile())["wordpress"]
    html = render_wp_html(body_md=fields["body"], date_mmddyy=readonly.get("date_mmddyy", ""),
                          youtube_url_placeholder=fields.get("youtube_url") or YOUTUBE_PLACEHOLDER)
    image = next((Path(a["path"]) for a in package["assets"] if a["role"] == "featured_image"), None)
    with WordPressClient(site=config["site"], auth_mode=config["auth_mode"]) as wp:
        media_id = wp.upload_media(image, alt_text=fields["title"]).media_id if image else None
        post = wp.create_draft_post(title=fields["title"], html=html, excerpt=fields.get("excerpt", ""),
                                    tags=fields.get("tags"), categories=fields.get("categories"),
                                    status=fields.get("status", "draft"), featured_media_id=media_id)
    notion_page_id = readonly.get("notion_blog_page_id")
    if notion_page_id:
        try:
            update_blog_page_wordpress_url(notion_page_id, post.url,
                                           status="Published" if fields.get("status") == "publish" else "Draft Created",
                                           excerpt=fields.get("excerpt", ""))
        except Exception as exc:  # noqa: BLE001 - the post exists; a failed write-back must not read as a failed publish
            package_path(run_dir, package["target"]).with_name("NOTION_UPDATE_FAILED.md").write_text(
                f"WP post created at {post.url} but Notion update failed: {type(exc).__name__}: {exc}\n")
    return {"id": str(post.post_id), "url": post.url}
