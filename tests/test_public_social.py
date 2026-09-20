import json
import unittest

from server.connectors.public_social import (
    _json_script,
    instagram_posts_from_feed,
    instagram_posts_from_html,
    instagram_posts_from_responses,
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

    def test_instagram_rest_timeline_becomes_posts_with_metrics(self):
        feed = {
            "user": {
                "username": "example",
                "full_name": "Example Brand",
                "is_private": False,
                "is_verified": True,
                "profile_pic_url": "https://cdn.example/avatar.jpg",
            },
            "items": [{
                "pk": "3939",
                "code": "REST123",
                "product_type": "clips",
                "media_type": 2,
                "taken_at": 1784956800,
                "like_count": 456,
                "comment_count": 23,
                "play_count": 9876,
                "reshare_count": 11,
                "caption": {"text": "New product launch"},
                "image_versions2": {
                    "candidates": [{"url": "https://cdn.example/post.jpg"}]
                },
            }],
        }

        posts = instagram_posts_from_feed(
            feed,
            "https://www.instagram.com/example/",
            follower_count=12345,
        )

        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post.url, "https://www.instagram.com/reel/REST123/")
        self.assertEqual(post.views, 9876)
        self.assertEqual(post.likes, 456)
        self.assertEqual(post.comments, 23)
        self.assertEqual(post.shares, 11)
        self.assertEqual(post.follower_count, 12345)
        self.assertEqual(post.raw["collection_method"], "instagram_public_timeline")

    def test_instagram_profile_400_uses_timeline_fallback(self):
        result = {
            "profile_status": 400,
            "profile_data": {"message": "schema unavailable", "status": "fail"},
            "feed_status": 200,
            "feed_data": {
                "user": {
                    "username": "example",
                    "full_name": "Example Brand",
                    "is_private": False,
                },
                "items": [{
                    "pk": "1",
                    "code": "FALLBACK1",
                    "media_type": 1,
                    "taken_at": 1784956800,
                    "like_count": 12,
                    "comment_count": 3,
                    "caption": {"text": "Fallback works"},
                }],
            },
            "og_description": (
                "141K Followers, 30 Following, 39 Posts - See Instagram photos "
                "and videos from Example Brand (@example)"
            ),
        }

        posts = instagram_posts_from_responses(
            result,
            "https://www.instagram.com/example/",
            "example",
        )

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].follower_count, 141000)
        self.assertEqual(posts[0].raw["collection_method"], "instagram_public_timeline")

    def test_instagram_unauthorized_api_uses_public_profile_html_timeline(self):
        payload = {
            "data": {
                "xig_user_by_username": {
                    "username": "plaud_official",
                    "full_name": "Plaud | Amplify Human Intelligence",
                    "profile_pic_url": "https://cdn.example/avatar.jpg",
                    "follower_count": 147081,
                    "is_private": False,
                    "is_verified": True,
                    "polaris_ordered_timeline_connection": {
                        "edges": [{
                            "node": {
                                "__typename": "XIGPolarisImageMedia",
                                "pk": "3988900139593896042",
                                "code": "Ddba4r5ABBq",
                                "accessibility_caption": (
                                    "Photo by Plaud | Amplify Human Intelligence on "
                                    "September 18, 2026."
                                ),
                                "caption": {"text": "Out in the world with Plaud One."},
                                "display_uri": "https://cdn.example/post.jpg",
                                "media_type": 1,
                                "product_type": "feed",
                            },
                        }],
                    },
                },
            },
        }
        html = '<script type="application/json">' + json.dumps(payload) + "</script>"
        posts = instagram_posts_from_html(
            html,
            "https://www.instagram.com/plaud_official/",
            "plaud_official",
        )

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].external_id, "3988900139593896042")
        self.assertEqual(posts[0].url, "https://www.instagram.com/p/Ddba4r5ABBq/")
        self.assertEqual(posts[0].occurred_at, "2026-09-18T12:00:00+00:00")
        self.assertEqual(posts[0].follower_count, 147081)
        self.assertEqual(posts[0].thumbnail_url, "https://cdn.example/post.jpg")
        self.assertEqual(posts[0].raw["collection_method"], "instagram_public_html")

    def test_instagram_rendered_post_anchors_work_without_polaris_json(self):
        html = """
        <html><head><meta property="og:description" content="Plaud | 147K Followers, 12 Following, 300 Posts"></head><body>
          <a href="/plaud_official/p/Ddba4r5ABBq/">
            <div><img alt="Photo by Plaud | Amplify Human Intelligence on September 18, 2026."
              src="https://cdn.example/post.jpg"></div>
          </a>
          <a href="https://www.instagram.com/plaud_official/reel/Dci2CEQyefX/">
            <img alt="Video by Plaud | Amplify Human Intelligence on September 1, 2026."
              src="https://cdn.example/reel.jpg">
          </a>
        </body></html>
        """
        posts = instagram_posts_from_html(
            html,
            "https://www.instagram.com/plaud_official/",
            "plaud_official",
        )

        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0].external_id, "3988900139593896042")
        self.assertEqual(posts[0].url, "https://www.instagram.com/p/Ddba4r5ABBq/")
        self.assertEqual(posts[0].occurred_at, "2026-09-18T12:00:00+00:00")
        self.assertEqual(posts[0].follower_count, 147000)
        self.assertEqual(posts[0].thumbnail_url, "https://cdn.example/post.jpg")
        self.assertEqual(posts[0].raw["collection_method"], "instagram_public_html_dom")
        self.assertEqual(posts[1].url, "https://www.instagram.com/reel/Dci2CEQyefX/")
        self.assertTrue(posts[1].raw["is_video"])

    def test_instagram_401_responses_use_rendered_post_anchors(self):
        html = (
            '<a href="/plaud_official/p/Ddba4r5ABBq/">'
            '<img alt="Photo by Plaud | Amplify Human Intelligence on September 18, 2026." '
            'src="https://cdn.example/post.jpg"></a>'
        )
        posts = instagram_posts_from_responses(
            {"profile_status": 401, "feed_status": 401, "html": html},
            "https://www.instagram.com/plaud_official/",
            "plaud_official",
        )

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].external_id, "3988900139593896042")
        self.assertEqual(posts[0].raw["collection_method"], "instagram_public_html_dom")

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
