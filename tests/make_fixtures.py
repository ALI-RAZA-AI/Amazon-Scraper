"""Generate Amazon-like HTML fixtures (same IDs/classes as real pages) for offline tests."""
from pathlib import Path

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)

CAROUSEL = """<div id="similarities_feature_div"><div class="a-carousel">
<span class="a-price"><span class="a-offscreen">$9.99</span></span> Some other product</div></div>"""

# Top reviews in both layouts Amazon uses (US list + "from other countries"),
# a duplicate, and one broken review (no body) that must be skipped.
# The second review mentions "$19.99" — review text must never feed the price.
REVIEWS = """<div id="cm-cr-dp-review-list">
<div id="R1TESTREVIEW01" data-hook="review"><span class="a-profile-name">Jane D.</span>
<a data-hook="review-title" href="#"><i data-hook="review-star-rating"><span class="a-icon-alt">5.0 out of 5 stars</span></i><span class="a-letter-space"></span><span>Best earbuds I have owned</span></a>
<span data-hook="review-date">Reviewed in the United States on March 3, 2024</span>
<a data-hook="format-strip">Color: Midnight Black</a> <span data-hook="avp-badge">Verified Purchase</span>
<span data-hook="review-body"><div><span>Great sound and the noise cancelling works on the train. Read more</span></div></span>
<span data-hook="helpful-vote-statement">One person found this helpful</span></div>
<div id="R9NOBODY0001" data-hook="review"><span class="a-profile-name">Broken</span></div>
</div>
<div id="cm-cr-global-review-list">
<div id="R2TESTREVIEW02" data-hook="review"><span class="a-profile-name">Sam K.</span>
<i data-hook="cmps-review-star-rating"><span class="a-icon-alt">3.0 out of 5 stars</span></i>
<span data-hook="review-title"><span class="cr-original-review-content">Decent but bulky</span></span>
<span data-hook="review-date">Reviewed in the United Kingdom on 5 January 2024</span>
<span data-hook="review-body"><span class="cr-original-review-content">The case is big. I paid $19.99 on sale.</span></span>
<span data-hook="helpful-vote-statement">12 people found this helpful</span></div>
<div id="R1TESTREVIEW01" data-hook="review"><span data-hook="review-body"><span>duplicate</span></span></div>
</div>"""

P1 = f"""<html><head><title>Amazon.com</title></head><body>
<input type="hidden" id="ASIN" value="B0TEST0001">
<div id="wayfinding-breadcrumbs_feature_div"><ul>
<li><a>Electronics</a></li><li><a>Headphones, Earbuds &amp; Accessories</a></li><li><a>Earbud Headphones</a></li></ul></div>
<span id="productTitle">  SoundCore Pro Wireless Earbuds, Bluetooth 5.3, Active Noise Cancelling, 40H Playtime  </span>
<a id="bylineInfo">Visit the SoundCore Store</a>
<div id="averageCustomerReviews"><span id="acrPopover" title="4.4 out of 5 stars"></span>
<span id="acrCustomerReviewText">12,483 ratings</span></div>
<div id="corePrice_feature_div"><span class="a-price"><span class="a-price-symbol">$</span><span class="a-offscreen">$49.99</span></span>
<span class="basisPrice"><span class="a-offscreen">$79.99</span></span></div>
<div id="availability"><span> In Stock </span></div>
<div id="variation_color_name"><span class="selection">Midnight Black</span></div>
<div id="feature-bullets"><ul>
<li><span class="a-list-item">Active noise cancelling reduces ambient noise by up to 95%.</span></li>
<li><span class="a-list-item">40 hours total playtime with the charging case.</span></li>
<li><span class="a-list-item">IPX5 water resistant for workouts.</span></li></ul></div>
<div id="productDescription"><p>Premium wireless earbuds with deep bass and clear calls.</p></div>
<table id="productDetails_techSpec_section_1">
<tr><th>Brand</th><td>SoundCore</td></tr><tr><th>Item Weight</th><td>1.9 ounces</td></tr>
<tr><th>Product Dimensions</th><td>2.4 x 2.1 x 1.1 inches</td></tr><tr><th>Connectivity</th><td>Bluetooth 5.3</td></tr>
<tr><th>Best Sellers Rank</th><td>#1,234 in Electronics (See Top 100 in Electronics) #12 in Earbud Headphones</td></tr>
<tr><th>Date First Available</th><td>March 3, 2024</td></tr></table>
<script>var data = {{"colorImages": {{"initial": [{{"hiRes":"https://m.media-amazon.com/images/I/71aaa._AC_SL1500_.jpg"}},{{"hiRes":"https://m.media-amazon.com/images/I/71bbb._AC_SL1500_.jpg"}}]}}}};</script>
{CAROUSEL}
{REVIEWS}</body></html>"""

# Air fryer: price NOT in the normal price block (layout variant) -> only stated in description.
# Specs in "detail bullets" layout; material only mentioned in bullets.
P2 = f"""<html><body>
<div id="wayfinding-breadcrumbs_feature_div"><ul><li><a>Home &amp; Kitchen</a></li><li><a>Kitchen &amp; Dining</a></li><li><a>Air Fryers</a></li></ul></div>
<span id="productTitle">CrispChef 6-Quart Digital Air Fryer with 8 Presets</span>
<a id="bylineInfo">Brand: CrispChef</a>
<div id="averageCustomerReviews"><span class="a-icon-alt">4.6 out of 5 stars</span></div>
<span id="acrCustomerReviewText">3,201 ratings</span>
<div id="apex_desktop"><span class="a-size-medium">Deal price: $89.99 with free delivery</span></div>
<div id="feature-bullets"><ul>
<li><span class="a-list-item">6-quart nonstick basket made of ceramic-coated steel, dishwasher safe.</span></li>
<li><span class="a-list-item">8 one-touch presets for fries, chicken, fish and more.</span></li></ul></div>
<div id="productDescription">Cook crispy food with up to 95% less oil. Matte black finish.</div>
<div id="detailBullets_feature_div"><ul>
<li><span>Product Dimensions : 14.2 x 11.8 x 12.6 inches</span></li>
<li><span>Item Weight : 10.4 pounds</span></li>
<li><span>Date First Available : 12 Jan. 2023</span></li>
<li><span>Best Sellers Rank : #5,678 in Home &amp; Kitchen</span></li>
<li><span>Manufacturer : CrispChef Appliances</span></li></ul></div>
<img id="landingImage" data-old-hires="https://m.media-amazon.com/images/I/61fryer._AC_SL1500_.jpg" src="x.jpg">
{CAROUSEL}</body></html>"""

# Yoga mat: data mostly from JSON-LD, no spec table at all.
P3 = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"FlexFit Non-Slip Yoga Mat 6mm",
 "brand":{"@type":"Brand","name":"FlexFit"},"description":"Extra thick 6mm exercise mat made of eco-friendly TPE. Weighs 2.2 pounds. Color: Ocean Blue.",
 "offers":{"@type":"Offer","price":"24.95","priceCurrency":"USD"},
 "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.7","reviewCount":"860"}}
</script></head><body>
<div id="feature-bullets"><ul>
<li><span class="a-list-item">Non-slip textured surface on both sides.</span></li>
<li><span class="a-list-item">Made of eco-friendly TPE, free of latex and PVC.</span></li>
<li><span class="a-list-item">Includes carrying strap; 72 x 24 inches.</span></li></ul></div>
</body></html>"""

# Current (2026) layout, modelled on live pages: .a-offscreen inside .priceToPay is
# empty and the visible price is split over spans; a device-comparison card
# elsewhere has its own .a-price; reviews are only summarised (histogram with a 0%
# row rendered as <span>, "Customers say" text, aspect buttons), no individual reviews.
HIST = "".join(
    f"""<div data-csa-c-content-id="customerReviews-histogram-{k}"><{tag} aria-label="{pct} percent of reviews have {n} stars">
<div aria-hidden="true">{n} star</div></{tag}></div>"""
    for k, n, pct, tag in (("fiveStar", 5, 87, "a"), ("fourStar", 4, 8, "a"), ("threeStar", 3, 2, "a"),
                           ("twoStar", 2, 0, "span"), ("oneStar", 1, 3, "a")))
P5 = f"""<html><body>
<input type="hidden" id="ASIN" value="B0TEST0005">
<span id="productTitle">Acme NoiseAway Pro Wireless Earbuds with USB-C Charging Case</span>
<a id="bylineInfo">Visit the Acme Store</a>
<div id="averageCustomerReviews"><span id="acrPopover" title="4.7 out of 5 stars"></span>
<span id="acrCustomerReviewText">(28,863)</span></div>
<div id="corePriceDisplay_desktop_feature_div"><span class="a-price priceToPay"><span class="a-offscreen"> </span>
<span aria-hidden="true"><span class="a-price-symbol">$</span><span class="a-price-whole">228<span class="a-price-decimal">.</span></span><span class="a-price-fraction">36</span></span></span></div>
<div id="feature-bullets"><ul><li><span class="a-list-item">Active noise cancellation and transparency mode.</span></li></ul></div>
<div id="amazon-device-desktop-dp_feature_div"><span class="a-price"><span class="a-offscreen">$4499</span></span></div>
<div id="reviewsMedley">{HIST}
<div data-testid="overall-summary"><span>Customers find these earbuds sound great and appreciate the noise cancellation.</span></div>
<button id="cr-product-insights-detail-page_button_0_POSITIVE_sound quality">Sound quality</button>
<button id="cr-product-insights-detail-page_button_1_POSITIVE_value for money">Value for money</button>
<button id="cr-product-insights-detail-page_button_2_MIXED_fit">Fit</button></div>
</body></html>"""

# Broken / blocked-looking page with no title -> must be rejected
P4 = """<html><body><div id="feature-bullets"><ul><li><span class="a-list-item">x</span></li></ul></div></body></html>"""

SEARCH = """<html><body>
<div data-component-type="s-search-result" data-asin="B0TEST0001"><a href="/dp/B0TEST0001">a</a></div>
<div data-component-type="s-search-result" data-asin="B0SPONSOR1" class="AdHolder"><span class="puis-sponsored-label-text">Sponsored</span></div>
<div data-component-type="s-search-result" data-asin="B0TEST0002"><a href="/dp/B0TEST0002">b</a></div>
<div data-component-type="s-search-result" data-asin="B0TEST0001"></div>
<a class="s-pagination-next s-pagination-disabled">Next</a></body></html>"""

for name, html in {"B0TEST0001.html": P1, "B0TEST0002.html": P2, "B0TEST0003.html": P3,
                   "B0TEST0004.html": P4, "B0TEST0005.html": P5}.items():
    (OUT / name).write_text(html, encoding="utf-8")
(Path(__file__).parent / "search_page.html").write_text(SEARCH, encoding="utf-8")
print("fixtures written to", OUT)
