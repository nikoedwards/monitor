import unittest
from unittest.mock import patch

from server.connectors.sales.amazon import (
    ScrapeAmazonProvider,
    _BROWSER_HEADERS,
    _extract_price,
    _strip_html,
)


class AmazonSalesParserTests(unittest.TestCase):
    def test_price_is_read_from_buy_box_not_coupon(self):
        html = """
        <div>Save $10 with coupon</div>
        <div id="corePrice_feature_div">
          <span class="a-price"><span class="a-offscreen">$29.99</span></span>
        </div>
        """
        self.assertEqual(_extract_price(html), 29.99)

    def test_coupon_alone_is_not_a_price(self):
        self.assertIsNone(_extract_price("<div>Save $10 with coupon</div>"))

    def test_json_ld_price_fallback(self):
        html = '<script type="application/ld+json">{"offers":{"price":"18.50"}}</script>'
        self.assertEqual(_extract_price(html), 18.5)

    def test_strip_product_title_markup(self):
        self.assertEqual(_strip_html(" Plaud <b>NotePin</b> &amp; Case "), "Plaud NotePin & Case")

    @patch("server.connectors.sales.amazon.fetch_page")
    def test_review_count_from_accessible_label(self, fetch_page):
        fetch_page.return_value = {
            "html": '<span id="acrCustomerReviewText" aria-label="825 Reviews">(825)</span>',
            "text": "4.6 out of 5",
            "meta": {"og:title": "Example product"},
            "title": "Example product",
            "final_url": "https://www.amazon.com/dp/B000000000",
        }
        snapshot = ScrapeAmazonProvider().fetch(None, {"url": "https://www.amazon.com/dp/B000000000", "asin": "B000000000"})
        self.assertEqual(snapshot.review_count, 825)

    @patch("server.connectors.sales.amazon.fetch_page")
    def test_product_fetch_retries_when_default_request_hits_continue_shopping(self, fetch_page):
        fetch_page.side_effect = [
            {
                "html": "<html><title>Amazon.com</title></html>",
                "text": "Amazon.com Click the button below to continue shopping",
                "meta": {},
                "title": "Amazon.com",
                "final_url": "https://www.amazon.com/dp/B000000000",
            },
            {
                "html": (
                    '<span id="productTitle">Example</span>'
                    '<div id="corePrice_feature_div"><span class="a-offscreen">$20.00</span></div>'
                ),
                "text": (
                    "Best Sellers Rank #100 in Electronics (See Top 100 in Electronics) "
                    "#20 in Headphones (See Top 100 in Headphones)"
                ),
                "meta": {"og:title": "Example"},
                "title": "Example",
                "final_url": "https://www.amazon.com/dp/B000000000",
            },
        ]

        snapshot = ScrapeAmazonProvider().fetch(
            None,
            {"url": "https://www.amazon.com/dp/B000000000", "asin": "B000000000", "marketplace": "US"},
        )

        self.assertEqual(2, fetch_page.call_count)
        self.assertEqual(
            (("https://www.amazon.com/dp/B000000000",), {"headers": _BROWSER_HEADERS}),
            fetch_page.call_args_list[1],
        )
        self.assertEqual("Example", snapshot.title)
        self.assertEqual(100, snapshot.category_rank)
        self.assertEqual(20, snapshot.subcategory_rank)
        self.assertEqual(100, snapshot.units_est)

    @patch("server.connectors.sales.amazon.fetch_page")
    def test_storefront_expand_retries_gate_and_discovers_asins(self, fetch_page):
        fetch_page.side_effect = [
            {
                "html": "<html><title>Amazon.com</title></html>",
                "text": "Amazon.com Click the button below to continue shopping",
            },
            {
                "html": '<div data-asin="B000000000"><a href="/dp/B000000000">Example</a></div>',
                "text": "Example",
            },
        ]

        refs = ScrapeAmazonProvider(max_pages=1).expand(
            None,
            {"url": "https://www.amazon.com/s?k=plaud+note", "channel": "amazon"},
        )

        self.assertEqual(["B000000000"], [ref.asin for ref in refs])
        self.assertEqual(2, fetch_page.call_count)


if __name__ == "__main__":
    unittest.main()
