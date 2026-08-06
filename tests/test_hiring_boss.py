import unittest
from unittest.mock import patch

from server.connectors.hiring.base import RenderResult
from server.connectors.hiring.boss import BossProvider


class BossHiringProviderTests(unittest.TestCase):
    def test_login_page_is_not_created_as_a_job_posting(self):
        page = RenderResult(
            status="ok",
            title="【BOSS直聘注册登录】boss直聘在线注册登录",
            text="登录BOSS直聘",
            html="<html></html>",
            final_url="https://www.zhipin.com/web/user/?ka=header-login",
        )
        with patch("server.connectors.hiring.boss.render", return_value=page):
            with self.assertRaisesRegex(RuntimeError, "登录/注册"):
                BossProvider().expand(None, {"url": "https://www.zhipin.com/"})

    def test_company_page_without_job_links_returns_empty(self):
        page = RenderResult(
            status="ok",
            title="Example Company",
            text="公司招聘职位",
            html="<html></html>",
            anchors=[],
            final_url="https://www.zhipin.com/gongsi/example.html",
        )
        with patch("server.connectors.hiring.boss.render", return_value=page):
            refs = BossProvider().expand(None, {"url": page.final_url})
        self.assertEqual(refs, [])

    def test_direct_job_url_remains_monitorable_when_temporarily_unreadable(self):
        page = RenderResult(status="error", error="temporary failure")
        url = "https://www.zhipin.com/job_detail/abc123.html"
        with patch("server.connectors.hiring.boss.render", return_value=page):
            refs = BossProvider().expand(None, {"url": url})
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].external_id, "abc123")

    def test_non_job_registry_row_is_marked_blocked(self):
        snapshot = BossProvider().fetch(None, {"url": "https://www.zhipin.com/web/user/?ka=header-login"})
        self.assertEqual(snapshot.status, "blocked")
        self.assertIsNone(snapshot.is_open)
        self.assertFalse(snapshot.title)


if __name__ == "__main__":
    unittest.main()
