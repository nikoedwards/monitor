import unittest
from unittest.mock import patch

from server.connectors.sales.amazon import ScrapeAmazonProvider, _extract_price, _strip_html


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


if __name__ == "__main__":
    unittest.main()
