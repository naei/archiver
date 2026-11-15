"""Sample links and answers shared by several test files."""
import json

from archiver import net


VIDEO_ID = "EXAMPLE0001"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
POST_ID = "100000000000000001"
POST_URL = f"https://x.com/example/status/{POST_ID}"


def x_post(**overrides):
    post = {
        "id_str": POST_ID, "created_at": "2017-08-06T01:05:32.000Z",
        "text": "Merci &amp; bravo https://t.co/link https://t.co/media",
        "entities": {
            "urls": [{"url": "https://t.co/link", "expanded_url": "https://example.org/page"}],
            "media": [{"url": "https://t.co/media"}],
        },
        "user": {"name": "Example", "screen_name": "example"},
        "mediaDetails": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/abc.jpg",
             "original_info": {"width": 2048, "height": 1536}, "ext_alt_text": "Une équipe"},
            {"type": "video", "video_info": {"duration_millis": 4500, "variants": [
                {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/a.m3u8"},
                {"content_type": "video/mp4", "bitrate": 256000, "url": "https://video.twimg.com/low.mp4"},
                {"content_type": "video/mp4", "bitrate": 2176000, "url": "https://video.twimg.com/high.mp4"},
            ]}},
        ],
    }
    post.update(overrides)
    return post


def json_answer(data):
    return net.Response("", "application/json", json.dumps(data).encode(), {})
