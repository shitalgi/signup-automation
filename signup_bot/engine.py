"""Generic signup flow engine: detect -> fill -> submit -> assess -> repeat."""
from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from urllib.parse import urlsplit

from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout

from . import datagen, detect
from .config import Config
from .registry import Registry

log = logging.getLogger("signup_bot")

PASS_STATUSES = {"PASSED", "PASSED_PENDING_VERIFICATION"}
ANALYTICS_RE = re.compile(r"google-analytics|googletagmanager|_vercel/(insights|speed-insights)|vitals\.vercel|"
                          r"sentry|segment\.|hotjar|clarity\.ms|facebook\.|doubleclick|mixpanel|posthog", re.I)

# Tiny valid files for upload fields.
PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
PDF_MIN = b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


@dataclass
class FlowResult:
    target: str
    status: str = "FAILED_UNKNOWN"
    detail: str = ""
    email: str = ""
    final_url: str = ""
    steps: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    http: list = field(default_factory=list)
    screenshot: str = ""
    duration: float = 0.0
    attempts: int = 0
    pre_recorded: bool = False

    @property
    def passed(self) -> bool:
        return self.status in PASS_STATUSES

    def to_dict(self):
        d = asdict(self)
        d["passed"] = self.passed
        return d


class Engine:
    def __init__(self, cfg: Config, registry: Registry, run_id: str | None = None):
        self.cfg = cfg
        self.reg = registry
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self._last_submit = 0.0
        self._seq = 0

    # ------------------------------------------------------------ public
    def run_flow(self, browser, url: str) -> FlowResult:
        t0 = time.time()
        res = FlowResult(target=url)
        existing = None if self.cfg.new_account else self.reg.existing(url)
        if existing:
            res.status = "SKIPPED_DUPLICATE"
            res.email = existing["email"]
            res.detail = f"account already recorded for this target ({existing['status']}); use --new-account to override"
            log.info("[%s] %s", url, res.detail)
            return res

        ctx_pw = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        ctx_pw.set_default_timeout(8000)
        page = ctx_pw.new_page()
        net = _Net(page)
        tag = self.reg.new_tag()
        ctx = datagen.Context(self.cfg, tag)
        res.email = ctx.email_candidates()[0]
        try:
            self._flow(page, net, ctx, res, url, t0)
        except PWTimeout as e:
            self._finish(res, "FAILED_TIMEOUT", f"timeout: {str(e).splitlines()[0]}", net)
        except PWError as e:
            self._finish(res, "FAILED_UNKNOWN", f"browser error: {str(e).splitlines()[0]}", net)
        except Exception as e:  # never let one flow kill the run
            log.exception("[%s] unexpected error", url)
            self._finish(res, "FAILED_UNKNOWN", f"{type(e).__name__}: {e}", net)
        finally:
            res.duration = round(time.time() - t0, 2)
            res.http = [e.brief() for e in net.events][-25:]
            try:
                res.final_url = page.url
                os.makedirs(os.path.join(self.cfg.out_dir, "screenshots"), exist_ok=True)
                slug = re.sub(r"[^a-z0-9]+", "_", urlsplit(url).path.lower()).strip("_") or "root"
                res.screenshot = os.path.join(self.cfg.out_dir, "screenshots", f"{slug}.png")
                page.screenshot(path=res.screenshot, full_page=True)
            except Exception:
                res.screenshot = ""
            reg_status = res.status if (res.passed or res.status == "UNCERTAIN") else "FAILED"
            if res.pre_recorded:
                self.reg.record(url, ctx.tag, res.email, reg_status, self.run_id)
            ctx_pw.close()
        log.info("[%s] => %s (%s) in %.1fs", url, res.status, res.detail, res.duration)
        return res

    # ------------------------------------------------------------- flow
    def _flow(self, page, net, ctx, res: FlowResult, url, t0):
        cfg = self.cfg
        resp = page.goto(url, wait_until="domcontentloaded", timeout=cfg.nav_timeout * 1000)
        if resp is not None and resp.status >= 400:
            return self._finish(res, "FAILED_NAVIGATION", f"page returned HTTP {resp.status}", net)
        self._settle(page)
        rate_hits = 0
        pre_recorded = False

        for step_no in range(1, cfg.max_steps + 1):
            if time.time() - t0 > cfg.flow_timeout:
                return self._finish(res, "FAILED_TIMEOUT", "flow timeout exceeded", net)
            sc = self._scan_with_wait(page, wait_for_fields=(step_no == 1))
            if sc.captcha_visible:
                return self._finish(res, "BLOCKED_CAPTCHA",
                                    "CAPTCHA widget present; not bypassed, flow stopped before submit", net)
            if not sc.fields:
                if step_no == 1:
                    return self._finish(res, "FAILED_NO_FORM", "no fillable fields found on page", net)
                return self._terminal(res, sc, None, net)

            step = {"step": step_no, "url": sc.url, "fields": [], "attempts": 0, "result": ""}
            res.steps.append(step)
            advanced = False

            for attempt in range(1, cfg.max_attempts + 1):
                step["attempts"] = attempt
                res.attempts += 1
                filled = self._fill_all(page, sc, ctx)
                step["fields"] = filled
                btn = detect.choose_action(sc)
                before = sc
                self._throttle()
                net.reset()
                if not pre_recorded:   # crash-safe: assume an account may exist from the first submit on
                    self.reg.record(url, ctx.tag, res.email, "UNCERTAIN", self.run_id)
                    pre_recorded = True
                    res.pre_recorded = True
                clicked = self._click(page, btn, sc)
                if not clicked:
                    step["result"] = "no_action_button"
                    return self._finish(res, "FAILED_NO_SUBMIT", "could not find or click a submit/next button", net)
                verdict, after, info = self._await_outcome(page, net, before)
                step["result"] = verdict
                log.info("[%s] step %d attempt %d -> %s %s", url, step_no, attempt, verdict, info)

                if verdict == "captcha":
                    return self._finish(res, "BLOCKED_CAPTCHA", info or "CAPTCHA challenge appeared; not bypassed", net)
                if verdict == "rate_limited":
                    rate_hits += 1
                    if rate_hits > 1:
                        return self._finish(res, "BLOCKED_RATE_LIMIT", "HTTP 429 twice; stopping (not circumventing)", net)
                    time.sleep(min(net.retry_after() or 2.0, cfg.max_retry_after))
                    sc = self._scan_with_wait(page)
                    continue
                if verdict == "server_error":
                    return self._finish(res, "FAILED_SUBMISSION", info, net)
                if verdict == "success":
                    return self._terminal(res, after, before, net)
                if verdict == "uncertain":
                    return self._finish(res, "UNCERTAIN", info, net)
                if verdict == "advanced":
                    sc, advanced = after, True
                    break
                # errors / rejected / no_progress -> adapt and retry
                errs = after.error_texts if after else []
                step["errors"] = errs[:5]
                self._adapt(ctx, sc, errs, res)
                sc = after if after and after.fields else sc
                if verdict == "no_progress" and attempt >= 2:
                    break
            if not advanced:
                errs = step.get("errors") or []
                return self._finish(res, "FAILED_VALIDATION" if errs else "FAILED_NO_PROGRESS",
                                    "; ".join(errs[:3]) or f"page did not progress after {cfg.max_attempts} attempts "
                                    f"(native invalid: {[n['name'] for n in sc.native_invalid][:5]})", net)
            # `advanced`: if the new view has no fields it is terminal
            if not sc.fields:
                return self._terminal(res, sc, before, net)
        self._finish(res, "FAILED_NO_PROGRESS", f"exceeded {cfg.max_steps} steps", net)

    # ----------------------------------------------------------- filling
    def _fill_all(self, page, sc, ctx) -> list:
        out, seen_groups = [], set()
        for f in sc.fields:
            kind = datagen.kind_of(f)
            try:
                loc = page.locator(f'[data-sb-id="{f["id"]}"]')
                v = self._fill_one(page, loc, f, kind, ctx, seen_groups)
                if v is not datagen.SKIP:
                    out.append({"label": f["label"] or f["name"] or f["placeholder"], "kind": kind,
                                "value": "***" if "password" in kind else v})
            except PWError as e:
                log.warning("could not fill %s (%s): %s", f["label"] or f["name"], kind, str(e).splitlines()[0])
        # give client-side validators a beat (enables disabled "Next" buttons)
        time.sleep(0.3)
        return out

    def _fill_one(self, page, loc, f, kind, ctx, seen_groups):
        t = f["type"]
        if t == "checkbox" or t == "role-checkbox" or f.get("role") == "checkbox":
            want = ctx.should_check(f)
            self._set_checked(page, loc, want)
            return want
        if t == "radio" or f.get("role") == "radio":
            g = f["name"] or f["groupLabel"] or f["id"]
            if g in seen_groups:
                return datagen.SKIP
            seen_groups.add(g)
            self._set_checked(page, loc, True)
            return "selected"
        if t == "select":
            v = ctx.choose_option(f, kind)
            if v is None:
                return datagen.SKIP
            loc.select_option(value=v)
            return v
        if f.get("role") == "combobox" and t not in ("text", "email"):
            loc.click()
            opt = page.locator('[role="option"]:visible').first
            opt.click(timeout=2500)
            return "option"
        if t == "file":
            loc.set_input_files(self._dummy_file(f))
            return "file"
        v = ctx.value_for(f, kind)
        if v is datagen.SKIP:
            return v
        loc.fill(v)
        got = loc.input_value()
        if got == "" and v != "":          # custom inputs that ignore fill(): type like a user
            loc.click()
            loc.press_sequentially(v, delay=20)
        loc.evaluate("e => e.blur()")
        return v

    def _set_checked(self, page, loc, want: bool):
        def state():
            try:
                return loc.is_checked()
            except PWError:
                return loc.get_attribute("aria-checked") == "true"
        if state() == want:
            return
        for how in ("normal", "force", "label"):
            try:
                if how == "normal":
                    loc.set_checked(want, timeout=1500)
                elif how == "force":
                    loc.set_checked(want, force=True, timeout=1500)
                else:
                    loc.evaluate("e => (e.labels && e.labels[0] ? e.labels[0] : e).click()")
            except PWError:
                continue
            if state() == want:
                return

    def _dummy_file(self, f) -> str:
        accept = f.get("accept") or ""
        d = tempfile.mkdtemp(prefix="signup_bot_")
        use_pdf = "pdf" in (f.get("label", "") + accept).lower() and "image" not in accept.lower()
        p = os.path.join(d, "qa-test-document." + ("pdf" if use_pdf else "png"))
        with open(p, "wb") as fh:
            fh.write(PDF_MIN if use_pdf else PNG_1PX)
        return p

    # -------------------------------------------------- submit / outcome
    def _throttle(self):
        wait = self.cfg.min_interval - (time.time() - self._last_submit)
        if wait > 0:
            time.sleep(wait)
        self._last_submit = time.time()

    def _click(self, page, btn, sc) -> bool:
        if btn is not None:
            loc = page.locator(f'[data-sb-btn="{btn["id"]}"]')
            deadline = time.time() + 3
            while btn["disabled"] and time.time() < deadline:      # wait for validators to enable it
                time.sleep(0.3)
                try:
                    btn["disabled"] = loc.is_disabled()
                except PWError:
                    break
            try:
                loc.click(timeout=4000)
                return True
            except PWError:
                try:
                    loc.click(timeout=2000, force=True)
                    return True
                except PWError:
                    pass
        # fallback: press Enter in the last text-like field
        for f in reversed(sc.fields):
            if f["type"] in ("text", "email", "password", "tel", "number", "url"):
                try:
                    page.locator(f'[data-sb-id="{f["id"]}"]').press("Enter", timeout=2000)
                    return True
                except PWError:
                    break
        return False

    def _await_outcome(self, page, net, before: detect.Scan):
        """Poll until something decisive happens. Returns (verdict, scan, info)."""
        deadline = time.time() + self.cfg.step_timeout
        before_err = set(before.error_texts)
        quiet_since = None
        sc = None
        while time.time() < deadline:
            time.sleep(0.4)
            try:
                sc = detect.scan(page)
            except PWError:
                continue                                        # mid-navigation
            if any(e.status == 429 for e in net.events):
                return "rate_limited", sc, "HTTP 429"
            if sc.captcha_visible and not before.captcha_visible:
                return "captcha", sc, ""
            new_errs = [e for e in sc.error_texts if e not in before_err and detect.ERR_RE.search(e)]
            if any(detect.CAPTCHA_TEXT_RE.search(e) for e in new_errs):
                return "captcha", sc, "server demanded CAPTCHA/human verification: " + new_errs[0]
            fivexx = [e for e in net.events if e.method != "GET" and e.status >= 500]
            if fivexx:
                return "server_error", sc, f"HTTP {fivexx[0].status} from {fivexx[0].short_url()}" + \
                    (f" - page says: {new_errs[0]}" if new_errs else "")
            if net.failed_submit():
                return "uncertain", sc, "submit request failed at network level; outcome unknown (not retried to avoid duplicates)"
            if _new_match(detect.SUCCESS_RE, sc.text, before.text) or _new_match(detect.VERIFY_RE, sc.text, before.text):
                return "success", sc, ""
            if new_errs:
                return "errors", sc, "; ".join(new_errs[:2])
            rej = [e for e in net.events if e.method != "GET" and 400 <= e.status < 500]
            if rej:
                time.sleep(0.8)                                  # let the UI render the message
                sc = detect.scan(page)
                errs = [e for e in sc.error_texts if e not in before_err]
                return "errors", sc, f"HTTP {rej[0].status} from {rej[0].short_url()} {'; '.join(errs[:2])}"
            if sc.url != before.url or sc.sig != before.sig:
                time.sleep(0.8)                                  # settle, then re-read the new view
                try:
                    sc = detect.scan(page)
                except PWError:
                    pass
                if _new_match(detect.SUCCESS_RE, sc.text, before.text) or _new_match(detect.VERIFY_RE, sc.text, before.text):
                    return "success", sc, ""
                return "advanced", sc, f"-> {sc.url}"
            if net.ok_post():
                quiet_since = quiet_since or time.time()
                if time.time() - quiet_since > self.cfg.quiet_after_post:
                    return "uncertain", sc, "request accepted (2xx) but the page showed no confirmation"
        if net.pending_submit():
            return "uncertain", sc or before, "submit request sent but no response before timeout; not retried to avoid a duplicate account"
        return "no_progress", sc or before, ""

    def _adapt(self, ctx: datagen.Context, sc, errs: list[str], res):
        """Use error text to decide what to change on the next attempt."""
        blob = " ".join(errs).lower()
        kinds = {datagen.kind_of(f) for f in sc.fields}
        blamed = {k for k in kinds if k in datagen.KIND_ERROR_HINT and re.search(datagen.KIND_ERROR_HINT[k], blob)}
        if re.search(datagen.KIND_ERROR_HINT["email"], blob) and re.search(r"already|taken|exist|registered|in use", blob):
            ctx.renew_tag(self.reg.new_tag())         # fresh identity; the rejected one never became an account
            res.email = ctx.email_candidates()[0]
            log.info("email rejected as taken -> new identity %s", res.email)
            blamed.discard("email")
        ctx.failed_rounds += 1
        if re.search(datagen.TERMS_RE, blob) or ctx.failed_rounds >= 2:   # a stubborn unchecked box is a common cause
            ctx.force_all_checkboxes = True
        if not blamed and not ctx.force_all_checkboxes:
            blamed = {k for k in kinds if k not in ("checkbox", "radio", "select", "file", "password_confirm")}
        for k in blamed:
            if k == "password_confirm":
                continue
            ctx.attempt[k] = ctx.attempt.get(k, 0) + 1

    def _terminal(self, res, sc, before, net):
        text = sc.text
        ev = []
        posts = [e for e in net.events if e.method != "GET" and 200 <= e.status < 400]
        if posts:
            ev.append(f"{posts[-1].method} {posts[-1].short_url()} -> {posts[-1].status}")
        if before is not None and sc.url != before.url:
            ev.append(f"navigated to {urlsplit(sc.url).path}")
        m_ver = detect.VERIFY_RE.search(text)
        m_ok = detect.SUCCESS_RE.search(text)
        if m_ver or (not m_ok and detect.VERIFY_URL_RE.search(urlsplit(sc.url).path)):
            ev.append(f"confirmation text/URL: {(m_ver.group(0) if m_ver else sc.url)!r}")
            res.evidence = ev
            return self._finish(res, "PASSED_PENDING_VERIFICATION",
                                "registration accepted; awaiting email/OTP/approval (external step)", net)
        if m_ok:
            ev.append(f"success text: {m_ok.group(0)!r}")
            res.evidence = ev
            return self._finish(res, "PASSED", "registration confirmed by success message", net)
        if posts and detect.SUCCESS_URL_RE.search(urlsplit(sc.url).path):
            res.evidence = ev + [f"success-like URL {urlsplit(sc.url).path}"]
            return self._finish(res, "PASSED", "registration accepted (inferred from 2xx + redirect)", net)
        res.evidence = ev
        return self._finish(res, "UNCERTAIN", "flow ended but no confirmation could be verified", net)

    def _finish(self, res, status, detail, net=None):
        res.status, res.detail = status, detail

    def _scan_with_wait(self, page, wait_for_fields=False) -> detect.Scan:
        deadline = time.time() + (6 if wait_for_fields else 2)
        while True:
            try:
                sc = detect.scan(page)
                if sc.fields or time.time() > deadline:
                    return sc
            except PWError:
                if time.time() > deadline:
                    raise
            time.sleep(0.4)

    def _settle(self, page):
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except PWTimeout:
            pass


def _new_match(rx, now: str, before: str):
    m = rx.search(now)
    return bool(m) and not rx.search(before)


class _Net:
    """Collects non-GET traffic so we can verify submissions."""

    class Ev:
        def __init__(self, method, url, status=0, headers=None):
            self.method, self.url, self.status, self.headers = method, url, status, headers or {}

        def short_url(self):
            u = urlsplit(self.url)
            return u.path or "/"

        def brief(self):
            return f"{self.method} {self.short_url()} -> {self.status}"

    def __init__(self, page):
        self.events: list[_Net.Ev] = []
        self.sent = 0
        self.done = 0
        self.failures = 0
        self.submitted = False          # ever sent a state-changing request in this flow
        page.on("request", self._on_req)
        page.on("response", self._on_resp)
        page.on("requestfailed", self._on_fail)

    def _counts(self, req):
        return req.method != "GET" and req.resource_type in ("xhr", "fetch", "document") \
            and not ANALYTICS_RE.search(req.url)

    def _on_req(self, req):
        if self._counts(req):
            self.sent += 1
            self.submitted = True

    def _on_resp(self, r):
        req = r.request
        if self._counts(req):
            self.done += 1
            self.events.append(_Net.Ev(req.method, r.url, r.status, r.headers))

    def _on_fail(self, req):
        if self._counts(req):
            self.failures += 1

    def reset(self):
        self.events, self.sent, self.done, self.failures = [], 0, 0, 0

    def ok_post(self):
        return any(e.method != "GET" and 200 <= e.status < 400 for e in self.events)

    def pending_submit(self):
        return self.sent > self.done + self.failures

    def failed_submit(self):
        return self.failures > 0

    def retry_after(self):
        for e in self.events:
            if e.status == 429:
                try:
                    return float(e.headers.get("retry-after", ""))
                except ValueError:
                    return None
        return None
