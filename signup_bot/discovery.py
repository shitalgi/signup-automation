"""Find every signup page reachable from a base URL (links + common paths)."""
from __future__ import annotations

import logging
import re
from collections import deque
from urllib.parse import urljoin, urlsplit, urldefrag

from playwright.sync_api import Error as PWError

from . import detect
from .config import Config

log = logging.getLogger("signup_bot")

SIGNUP_RE = re.compile(r"sign[\s_-]?up|register|registration|create[\s_-]?(an[\s_-]?)?account|join|get[\s_-]?started|onboard|enrol", re.I)
SKIP_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|pdf|zip|mp4|woff2?)$", re.I)
LINKS_JS = "() => [...document.querySelectorAll('a[href]')].map(a => ({href: a.href, text: (a.innerText||a.getAttribute('aria-label')||'').trim()}))"


def _norm(u: str) -> str:
    u = urldefrag(u)[0]
    p = urlsplit(u)
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/') or '/'}"        # drop query: /register?step=x is a step, not a page


def looks_like_signup(sc: detect.Scan, url: str) -> bool:
    if not sc.fields or sc.captcha_visible and not sc.fields:
        return False
    signal = SIGNUP_RE.search(urlsplit(url).path) or any(SIGNUP_RE.search(b["text"]) for b in sc.buttons) \
        or SIGNUP_RE.search(sc.text[:400])
    kinds = [f["type"] for f in sc.fields]
    has_confirm = "password" in kinds and kinds.count("password") >= 2
    login_only = "password" in kinds and not has_confirm and len(sc.fields) <= 3 and not SIGNUP_RE.search(urlsplit(url).path)
    return bool((signal or has_confirm) and not login_only)


def discover(browser, base_url: str, cfg: Config, extra_seeds: list[str] | None = None) -> list[str]:
    origin = urlsplit(base_url)
    ctx = browser.new_context()
    ctx.set_default_timeout(8000)
    page = ctx.new_page()
    seen, found = set(), []
    q = deque([(base_url, 0)] + [(urljoin(base_url, s), 0) for s in (extra_seeds or [])])
    queued = {_norm(u) for u, _ in q}
    if cfg.probe_common_paths:
        for p in cfg.common_paths:
            u = urljoin(base_url, p)
            if _norm(u) not in queued:
                q.append((u, 1)); queued.add(_norm(u))
    visited = 0
    try:
        while q and visited < cfg.max_pages:
            url, depth = q.popleft()
            if _norm(url) in seen:
                continue
            seen.add(_norm(url))
            visited += 1
            try:
                r = page.goto(url, wait_until="domcontentloaded", timeout=cfg.nav_timeout * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except PWError:
                    pass
                if r is not None and r.status >= 400:
                    continue
                final = _norm(page.url)
                if urlsplit(final).netloc != origin.netloc:
                    continue
                sc = detect.scan(page)
                if looks_like_signup(sc, page.url) and final not in found:
                    found.append(final)
                    log.info("signup page: %s", final)
                links = page.evaluate(LINKS_JS) if depth < 2 else []
            except PWError as e:
                log.warning("discovery: %s failed: %s", url, str(e).splitlines()[0])
                continue
            for l in links:
                href = l["href"]
                if not href.startswith("http") or SKIP_EXT.search(urlsplit(href).path):
                    continue
                if cfg.same_origin_only and urlsplit(href).netloc != origin.netloc:
                    continue
                n = _norm(href)
                if n in queued:
                    continue
                if depth == 0 or SIGNUP_RE.search(l["text"]) or SIGNUP_RE.search(urlsplit(href).path):
                    queued.add(n)
                    q.append((href, depth + 1))
    finally:
        ctx.close()
    return found
