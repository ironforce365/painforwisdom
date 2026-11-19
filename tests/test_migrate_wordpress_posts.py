import httpx
import pytest
import yaml

from pipeline.scripts import migrate_wordpress_posts as m

SITE = "https://painforwisdom.wordpress.com"

OLDER = {
    "ID": 243, "slug": "half-way", "title": "Half way", "date": "2024-07-06T10:00:00-07:00",
    "URL": f"{SITE}/2024/07/06/half-way/", "tags": {}, "featured_image": "",
    "content": "<p>Old post.</p>",
}
NEWER = {
    "ID": 300, "slug": "here-i-go", "title": "here I go again&#8230;", "date": "2024-08-01T10:00:00-07:00",
    "URL": f"{SITE}/2024/08/01/here-i-go/", "tags": {"running": {}, "pain": {}},
    "featured_image": f"{SITE}/wp-content/uploads/2024/08/photo.jpeg?w=1024",
    "content": (
        '<p class="wp-block-paragraph">08/01/24. Youtube: <a href="https://youtube.com/shorts/abc?si=track">link</a>.</p>'
        f'<p>See <a href="{SITE}/2024/07/06/half-way/">this</a> and '
        '<a href="https://wordpress.com/post/painforwisdom.wordpress.com/243">that</a>.'
        '<strong>Bold </strong>text 220 &lt;age&gt; a_b<br>next'
        '<sup data-fn="f1" class="fn"><a href="#f1" id="f1-link">1</a></sup></p>'
        "<ul><li>one</li><li>two</li></ul>"
        '<figure class="wp-block-image"><img src="' + SITE + '/wp-content/uploads/x.png?w=9" alt=""/></figure>'
        '<ol class="wp-block-footnotes"><li id="f1">A note <a href="#f1-link">↩︎</a></li></ol>'
        "<p></p>"
    ),
}


def _client():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "public-api.wordpress.com":
            return httpx.Response(200, json={"found": 2, "posts": [OLDER, NEWER]})
        return httpx.Response(200, content=b"img-" + req.url.path.encode())
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_migrate_writes_front_matter_body_and_images(tmp_path):
    assert m.migrate(tmp_path, _client()) == 2
    text = (tmp_path / "here-i-go.md").read_text()
    _, head, body = text.split("---\n", 2)
    assert yaml.safe_load(head) == {
        "title": "here I go again…", "date": "2024-08-01T10:00:00-07:00", "slug": "here-i-go",
        "tags": ["running", "pain"], "youtube_url": "https://youtube.com/shorts/abc",
        "featured_image": "images/here-i-go.jpeg",
    }
    assert "See [this](half-way.md) and [that](half-way.md)." in body
    assert "**Bold** text 220 &lt;age> a\\_b  \nnext[^1]" in body
    assert "- one\n- two" in body
    assert "![](images/here-i-go-1.png)" in body
    assert body.rstrip().endswith("[^1]: A note")
    assert (tmp_path / "images" / "here-i-go.jpeg").read_bytes() == b"img-/wp-content/uploads/2024/08/photo.jpeg"
    assert (tmp_path / "images" / "here-i-go-1.png").exists()


@pytest.mark.parametrize("href", [f"{SITE}/2023/01/01/gone/", "https://wordpress.com/post/painforwisdom.wordpress.com/9"])
def test_unresolved_internal_link_fails(href):
    with pytest.raises(ValueError, match="does not resolve"):
        m.LinkResolver([OLDER]).slug_for(href)


def test_unknown_tag_fails():
    with pytest.raises(ValueError, match="unsupported block tag <table>"):
        m.Converter("s", m.LinkResolver([OLDER])).convert("<table></table>")
