"""End-to-end test: discover + run every signup page of the mock site, assert outcomes,
assert server-side that no duplicate accounts were created, and write the report."""
import json
import logging
import os
import shutil
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from playwright.sync_api import sync_playwright

from signup_bot import report
from signup_bot.config import Config
from signup_bot.discovery import discover
from signup_bot.engine import Engine
from signup_bot.registry import Registry
from tests.mock_site import Server

EXPECTED = {
    "/register": "PASSED_PENDING_VERIFICATION",   # consent page + 4-step wizard, like the recorded app
    "/signup-basic": "PASSED",
    "/signup-mixed": "PASSED",
    "/signup-captcha": "BLOCKED_CAPTCHA",
    "/signup-validation": "PASSED",
    "/signup-conflict": "PASSED",
    "/signup-500": "FAILED_SUBMISSION",
    "/signup-slow": "UNCERTAIN",
    "/signup-ratelimit": "BLOCKED_RATE_LIMIT",
}
failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def state(srv):
    return json.load(urllib.request.urlopen(srv.url + "/__state"))


def main():
    work = tempfile.mkdtemp(prefix="e2e_")
    out = os.path.join(os.path.dirname(__file__), "..", "reports")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(os.path.join(out, "run.log")), logging.StreamHandler()])
    cfg = Config(out_dir=out, registry_path=os.path.join(work, "reg.json"), min_interval=0.3,
                 step_timeout=6, nav_timeout=10, probe_common_paths=True)
    srv = Server().start()
    reg = Registry(cfg.registry_path)
    eng = Engine(cfg, reg, "e2e")
    with sync_playwright() as pw:
        br = pw.chromium.launch()

        print("\n[1] discovery")
        found = discover(br, srv.url, cfg)
        paths = sorted(f.replace(srv.url, "") for f in found)
        print("   found:", paths)
        check(set(paths) == set(EXPECTED), f"discovered exactly the {len(EXPECTED)} signup pages (no login/contact decoys)")

        print("\n[2] run every signup page")
        results = [eng.run_flow(br, f) for f in found]
        by = {r.target.replace(srv.url, ""): r for r in results}
        for path, want in EXPECTED.items():
            r = by.get(path)
            check(r is not None and r.status == want, f"{path}: expected {want}, got {r.status if r else None}"
                  + (f" ({r.detail})" if r and r.status != want else ""))

        print("\n[3] server-side verification (ground truth)")
        st = state(srv)
        forms = [a["form"] for a in st["accounts"]]
        emails = [a["email"].lower() for a in st["accounts"]]
        for form in ("wizard", "basic", "mixed", "signup-validation", "signup-conflict"):
            check(forms.count(form) == 1, f"exactly one '{form}' account created (got {forms.count(form)})")
        for form in ("signup-captcha", "signup-500", "signup-slow", "signup-ratelimit"):
            check(forms.count(form) == 0, f"no account created on '{form}'")
        check(len(emails) == len(set(emails)), "all created emails are unique")
        check(st["hits"].get("/api/signup-captcha", 0) == 0, "CAPTCHA page: nothing was submitted")
        check(st["hits"].get("/api/signup-500", 0) == 1, "500 page: submitted once, not retried")
        check(st["hits"].get("/api/signup-slow", 0) == 1, "hanging page: submitted once, not retried (no duplicate risk)")
        check(st["hits"].get("/api/signup-ratelimit", 0) == 2, "429 page: one polite retry, then stopped")
        check(st["hits"].get("/api/signup-conflict", 0) == 2, "conflict page: rejected once, retried with new identity")
        check(st["hits"].get("/api/signup-validation", 0) >= 2, "strict-phone page: adapted using the error message")
        mixed = next(a for a in st["accounts"] if a["form"] == "mixed")
        check(not mixed["data"].get("newsletter") and not mixed["data"].get("honeypot"),
              "optional newsletter left unchecked, honeypot untouched")

        print("\n[3b] recorded registry")
        recs = {os.path.basename(a["target"]): a["status"] for a in reg.all()}
        check(recs.get("signup-slow") == "UNCERTAIN", "hanging page remembered as UNCERTAIN (blocks duplicates)")

        print("\n[4] second run: no duplicate accounts")
        n_before = len(state(srv)["accounts"])
        results2 = [eng.run_flow(br, f) for f in found]
        by2 = {r.target.replace(srv.url, ""): r.status for r in results2}
        for p in ("/register", "/signup-basic", "/signup-mixed", "/signup-validation", "/signup-conflict", "/signup-slow"):
            check(by2[p] == "SKIPPED_DUPLICATE", f"{p}: skipped on re-run (got {by2[p]})")
        check(len(state(srv)["accounts"]) == n_before, "re-run created zero additional accounts")

        print("\n[5] --new-account produces a distinct account")
        cfg.new_account = True
        r = eng.run_flow(br, srv.url + "/signup-basic")
        cfg.new_account = False
        check(r.status == "PASSED", "new-account run passes")
        emails = [a["email"].lower() for a in state(srv)["accounts"]]
        check(len(emails) == len(set(emails)) and len(emails) == n_before + 1, "new account has a unique email")

        print("\n[6] unreachable / missing pages")
        r = eng.run_flow(br, srv.url + "/does-not-exist")
        check(r.status == "FAILED_NAVIGATION", f"404 page reported as FAILED_NAVIGATION (got {r.status})")
        r = eng.run_flow(br, srv.url + "/contact")
        check(r.status.startswith("FAILED_") and not r.passed, f"non-signup contact form never reported as a signup success (got {r.status})")
        br.close()

    jpath, mpath = report.write(results, out, "e2e", srv.url)
    print("\n" + open(mpath).read())
    srv.stop()
    shutil.rmtree(work, ignore_errors=True)
    print(f"\n{'ALL CHECKS PASSED' if not failures else str(len(failures)) + ' CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
