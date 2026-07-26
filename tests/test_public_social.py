import json
import unittest

from server.connectors.public_social import (
    _json_script,
    instagram_posts_from_user,
    tiktok_posts_from_data,
)
from server.fetchers import FetchError


class PublicSocialNormalizerTests(unittest.TestCase):
    def test_instagram_profile_response_becomes_posts_with_metrics(self):
        user = {
            "username": "example",
            "full_name": "Example Brand",
            "is_private": False,
            "is_verified": True,
            "profile_pic_url": "https://cdn.example/avatar.jpg",
            "edge_followed_by": {"count": 12345},
            "edge_owner_to_timeline_media": {
                "edges": [{
                    "node": {
                        "id": "3939",
                        "shortcode": "ABC123",
                        "product_type": "clips",
                        "is_video": True,
                        "video_view_count": 9876,
                        "taken_at_timestamp": 1784956800,
                        "thumbnail_src": "https://cdn.example/post.jpg",
                        "edge_liked_by": {"count": 456},
                        "edge_media_to_comment": {"count": 23},
                        "edge_media_to_caption": {
                            "edges": [{"node": {"text": "New product launch"}}]
                        },
                    }
                }]
            },
        }

        posts = instagram_posts_from_user(user, "https://www.instagram.com/example/")

        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post.url, "https://www.instagram.com/reel/ABC123/")
        self.assertEqual(post.views, 9876)
        self.assertEqual(post.likes, 456)
        self.assertEqual(post.comments, 23)
        self.assertEqual(post.follower_count, 12345)
        self.assertEqual(post.raw["collection_method"], "instagram_public_web")

    def test_private_instagram_profile_is_rejected(self):
        with self.assertRaises(FetchError):
            instagram_posts_from_user(
                {"username": "private", "is_private": True},
                "https://www.instagram.com/private/",
            )

    def test_withheld_instagram_video_views_are_not_stored_as_real_zero(self):
        user = {
            "username": "example",
            "is_private": False,
            "edge_owner_to_timeline_media": {
                "edges": [{"node": {
                    "id": "1",
                    "shortcode": "ABC",
                    "is_video": True,
                    "video_view_count": 0,
                    "edge_liked_by": {"count": 10},
                    "edge_media_to_comment": {"count": 2},
                }}]
            },
        }

        post = instagram_posts_from_user(user, "https://www.instagram.com/example/")[0]

        self.assertIsNone(post.views)

    def test_tiktok_playlist_entries_become_posts_with_metrics(self):
        profile = {
            "user": {
                "uniqueId": "example",
                "nickname": "Example Brand",
                "verified": True,
                "privateAccount": False,
                "avatarMedium": "https://cdn.example/avatar.jpg",
            },
            "statsV2": {"followerCount": "54321", "videoCount": "20"},
        }
        entries = [{
            "id": "7666",
            "url": "https://www.tiktok.com/@example/video/7666",
            "description": "Product demonstration",
            "uploader": "example",
            "channel": "Example Brand",
            "timestamp": 1784956800,
            "view_count": 3000,
            "like_count": 200,
            "comment_count": 15,
            "repost_count": 9,
            "save_count": 40,
            "duration": 28,
            "thumbnails": [{"url": "https://cdn.example/video.jpg"}],
        }]

        posts = tiktok_posts_from_data(profile, entries, "https://www.tiktok.com/@example")

        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post.views, 3000)
        self.assertEqual(post.likes, 200)
        self.assertEqual(post.comments, 15)
        self.assertEqual(post.shares, 9)
        self.assertEqual(post.follower_count, 54321)
        self.assertEqual(post.raw["save_count"], 40)
        self.assertEqual(post.raw["collection_method"], "tiktok_public_web_ytdlp")

    def test_tiktok_hydration_script_is_parsed(self):
        payload = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"user": {"uniqueId": "x"}}}}}
        html = f'<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(payload)}</script></html>'

        self.assertEqual(_json_script(html, "__UNIVERSAL_DATA_FOR_REHYDRATION__"), payload)


if __name__ == "__main__":
    unittest.main()
