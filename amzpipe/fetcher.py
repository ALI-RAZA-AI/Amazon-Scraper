"""Page fetching: disk cache, polite delays, retries, CAPTCHA detection, and an
automatic switch from plain HTTP requests to a headless browser when blocked.

fetch.backend:
  auto       -> requests first; after `fallback_after` blocked attempts, switch to
                Playwright (if installed) for the rest of the run
  requests   -> plain HTTP only
  playwright -> headless Chromium only
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from urllib.parse import unquote, urlsplit

import requests

from .paths import resolve
from .transforms import MARKETPLACE_CURRENCY

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

CAPTCHA_MARKERS = ("validateCaptcha", "Enter the characters you see below",
                   "Sorry, we just need to make sure you're not a robot",
                   "api-services-support@amazon.com")

# Page language per marketplace. Without these cookies Amazon may show prices
# converted into the visitor's local currency (by IP), which would silently
# corrupt the price column.
MARKETPLACE_LANG = {"amazon.com": "en_US", "amazon.co.uk": "en_GB", "amazon.ae": "en_AE",
                    "amazon.in": "en_IN", "amazon.ca": "en_CA", "amazon.com.au": "en_AU"}
# Default delivery location per marketplace (fetch.delivery_zip: auto). From an IP
# outside the marketplace's country Amazon otherwise says "cannot be shipped to your
# selected delivery location" and hides the price, the breadcrumbs and the reviews.
MARKETPLACE_ZIP = {"amazon.com": "10001", "amazon.co.uk": "SW1A 1AA", "amazon.ca": "M5V 3L9",
                   "amazon.de": "10115", "amazon.fr": "75001", "amazon.it": "00118", "amazon.es": "28001",
                   "amazon.in": "110001", "amazon.com.au": "2000", "amazon.co.jp": "100-0001"}
NOT_DELIVERABLE = "cannot be shipped to your selected delivery location"
ASIN_IN_URL = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


class FetchError(RuntimeError):
    """One page could not be fetched; the run continues with the next one."""


class BlockedError(FetchError):
    """Amazon keeps refusing requests; the run stops (and exports what it has)."""


class NotFoundError(FetchError):
    pass


class CacheMissError(FetchError):
    pass


class Fetcher:
    def __init__(self, cfg: dict, marketplace: str = "amazon.com"):
        self.cfg = cfg
        self.marketplace = marketplace
        self.cache = resolve(cfg.get("cache_dir", "data/cache"))
        self.cache.mkdir(parents=True, exist_ok=True)
        self.backend = cfg.get("backend", "auto")
        if self.backend not in ("auto", "requests", "playwright"):
            raise ValueError(f"fetch.backend must be auto, requests or playwright (got {self.backend!r})")
        self.proxies = cfg.get("proxies") or []
        self.session = requests.Session()
        self.user_agent = random.choice(USER_AGENTS)
        self.use_browser = self.backend == "playwright"
        self._browser_ok = None
        self._warmed = False
        self._location_tries = 0
        self.location_set = False
        self._last = 0.0
        self._pw = self._browser = self._context = None
        self.stats = {"cache": 0, "requests": 0, "browser": 0, "blocked": 0}
        if self.use_browser and not self._browser_available():
            raise RuntimeError("fetch.backend is 'playwright' but Playwright is not installed: "
                               "pip install playwright && playwright install chromium")

    # ------------------------------------------------------------- helpers
    def cache_path(self, url: str):
        h = hashlib.sha1(url.encode()).hexdigest()
        m = ASIN_IN_URL.search(url)
        return self.cache / (f"{m.group(1)}_{h[:8]}.html" if m else f"{h}.html")

    def _sleep(self):
        wait = random.uniform(self.cfg.get("min_delay", 2), self.cfg.get("max_delay", 5))
        elapsed = time.time() - self._last
        if elapsed < wait:
            time.sleep(wait - elapsed)
        self._last = time.time()

    def _headers(self):
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }

    def _cookies(self) -> dict:
        c = {}
        if self.marketplace in MARKETPLACE_CURRENCY:
            c["i18n-prefs"] = MARKETPLACE_CURRENCY[self.marketplace]
        if self.marketplace in MARKETPLACE_LANG:
            c["lc-main"] = MARKETPLACE_LANG[self.marketplace]
        c.update(self.cfg.get("cookies") or {})
        return c

    def _proxy(self):
        return random.choice(self.proxies) if self.proxies else None

    def _browser_available(self) -> bool:
        if self._browser_ok is None:
            try:
                import playwright.sync_api  # noqa: F401
                self._browser_ok = True
            except ImportError:
                log.warning("Headless-browser fallback unavailable: pip install playwright && "
                            "playwright install chromium")
                self._browser_ok = False
        return self._browser_ok

    @staticmethod
    def is_captcha(html: str) -> bool:
        return any(m in html for m in CAPTCHA_MARKERS)

    # --------------------------------------------------------------- fetch
    def get(self, url: str, use_cache: bool = True) -> str:
        cp = self.cache_path(url)
        if use_cache and cp.exists():
            self.stats["cache"] += 1
            return cp.read_text(encoding="utf-8", errors="ignore")
        if self.cfg.get("cache_only"):
            raise CacheMissError(f"Not in cache: {url}")

        retries = self.cfg.get("max_retries", 4)
        fallback_after = self.cfg.get("fallback_after", 2)
        failures = attempt = 0
        while attempt < retries:
            attempt += 1
            self._sleep()
            try:
                html = self._get_browser(url) if self.use_browser else self._get_requests(url)
            except NotFoundError:
                raise
            except Exception as e:  # network errors, browser errors
                log.warning("Fetch error %s (attempt %d/%d): %s", url, attempt, retries, e)
                html = None
            if html and not self.is_captcha(html):
                cp.write_text(html, encoding="utf-8")
                return html
            failures += 1
            self.stats["blocked"] += 1
            if html:
                log.warning("CAPTCHA page for %s (attempt %d/%d)", url, attempt, retries)
            if (self.backend == "auto" and not self.use_browser and failures >= fallback_after
                    and self._browser_available()):
                log.warning("Plain requests are being blocked; switching to the headless browser "
                            "for the rest of this run.")
                self.use_browser = True
                attempt = 0  # fresh retry budget for the browser
                continue
            self.user_agent = random.choice(USER_AGENTS)  # new identity after a block
            time.sleep(min(60, 2 ** attempt + random.random() * 3))
        raise BlockedError(f"Could not fetch {url} after {retries} attempts (blocked or CAPTCHA). "
                           "Try longer delays, proxies, or fetch.backend: playwright.")

    def _warm_up(self):
        """Visit the home page once so the session carries normal Amazon cookies,
        then set the delivery location (see MARKETPLACE_ZIP)."""
        self._warmed = True
        for k, v in self._cookies().items():
            self.session.cookies.set(k, v, domain=f".{self.marketplace}")
        proxy = self._proxy()
        try:
            r = self.session.get(f"https://www.{self.marketplace}/", headers=self._headers(),
                                 timeout=self.cfg.get("timeout", 25),
                                 proxies={"http": proxy, "https": proxy} if proxy else None)
            self._set_location(r.text)
        except requests.RequestException as e:
            log.debug("warm-up request failed: %s", e)

    def _set_location(self, html: str) -> bool:
        """Same calls as the site's "Deliver to" box: get a CSRF token, post the ZIP code.
        `html` is any Amazon page with the location widget (home page or product page).
        Returns True when the location was changed by this call."""
        zip_code = self.cfg.get("delivery_zip", "auto")
        if zip_code == "auto":
            zip_code = MARKETPLACE_ZIP.get(self.marketplace)
        if not zip_code or self.location_set or self._location_tries >= 2:
            return False
        base = f"https://www.{self.marketplace}"
        m = re.search(r'name="glow-validation-token"[^>]*?value="([^"]+)"', html)
        if not m:
            return False
        self._location_tries += 1
        ajax = {**self._headers(), "X-Requested-With": "XMLHttpRequest", "Accept": "*/*",
                "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "same-origin"}
        timeout = self.cfg.get("timeout", 25)
        r = self.session.get(f"{base}/portal-migration/hz/glow/get-rendered-address-selections?deviceType=desktop"
                             "&pageType=Gateway&storeContext=NoStoreName&actionSource=desktop-modal",
                             headers={**ajax, "anti-csrftoken-a2z": m.group(1)}, timeout=timeout)
        csrf = re.search(r'CSRF_TOKEN\s*:\s*"([^"]+)"', r.text)
        if not csrf:
            log.warning("Could not set the delivery location (no CSRF token, HTTP %s)", r.status_code)
            return False
        r = self.session.post(f"{base}/portal-migration/hz/glow/address-change?actionSource=glow",
                              headers={**ajax, "anti-csrftoken-a2z": csrf.group(1),
                                       "Content-Type": "application/json", "Accept": "application/json"},
                              json={"locationType": "LOCATION_INPUT", "zipCode": zip_code, "deviceType": "web",
                                    "storeContext": "generic", "pageType": "Gateway", "actionSource": "glow"},
                              timeout=timeout)
        self.location_set = r.ok and re.search(r'"isValidAddress"\s*:\s*1', r.text) is not None
        if self.location_set:
            log.info("Delivery location set to %s on %s", zip_code, self.marketplace)
        else:
            log.warning("Amazon did not accept delivery location %r (HTTP %s); prices may be hidden",
                        zip_code, r.status_code)
        return self.location_set

    def _get_requests(self, url):
        if not self._warmed:
            self._warm_up()
        self.stats["requests"] += 1
        proxy = self._proxy()
        r = self.session.get(url, headers=self._headers(), timeout=self.cfg.get("timeout", 25),
                             proxies={"http": proxy, "https": proxy} if proxy else None)
        if r.status_code == 404:
            raise NotFoundError(f"Page not found (404): {url}")
        if r.status_code in (429, 500, 503):
            log.warning("HTTP %s for %s", r.status_code, url)
            return None
        r.raise_for_status()
        if NOT_DELIVERABLE in r.text and self._set_location(r.text):
            return self._get_requests(url)  # same page again, now for the new delivery location
        return r.text

    def _get_browser(self, url):
        if self._context is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            launch = {"headless": True}
            proxy = self._proxy()
            if proxy:
                u = urlsplit(proxy)
                launch["proxy"] = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
                if u.username:
                    launch["proxy"].update(username=unquote(u.username), password=unquote(u.password or ""))
            self._browser = self._pw.chromium.launch(**launch)
            self._context = self._browser.new_context(user_agent=self.user_agent, locale="en-US")
            if not self._warmed:  # delivery location + cookies via a quick HTTP session first
                try:
                    self._warm_up()
                except Exception as e:
                    log.debug("warm-up for browser failed: %s", e)
            cookies = dict(self._cookies())
            cookies.update({c.name: c.value for c in self.session.cookies if self.marketplace in (c.domain or "")})
            self._context.add_cookies([{"name": k, "value": v, "domain": f".{self.marketplace}", "path": "/"}
                                       for k, v in cookies.items()])
            # images / fonts / media aren't needed for extraction (image URLs are in the HTML)
            self._context.route("**/*", lambda route: route.abort()
                                if route.request.resource_type in ("image", "media", "font")
                                else route.continue_())
        self.stats["browser"] += 1
        page = self._context.new_page()
        try:
            resp = page.goto(url, timeout=self.cfg.get("timeout", 25) * 1000, wait_until="domcontentloaded")
            if resp is not None and resp.status == 404:
                raise NotFoundError(f"Page not found (404): {url}")
            if resp is not None and resp.status in (429, 503):
                log.warning("HTTP %s for %s (browser)", resp.status, url)
                return None
            page.wait_for_timeout(1500)
            return page.content()
        finally:
            page.close()

    def close(self):
        for obj, method in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass
        self._context = self._browser = self._pw = None
        self.session.close()
