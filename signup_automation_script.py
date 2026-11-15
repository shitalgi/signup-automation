#!/usr/bin/env python3
"""Fully automated signup runner.

Examples
  python signup_automation_script.py --url https://your-test-env.example --discover
  python signup_automation_script.py --url https://your-test-env.example --paths /register /signup
Exit codes: 0 all passed | 1 at least one failure | 2 no failures but some pages blocked by CAPTCHA/rate limit
Only run against environments you are authorised to test.
"""
import argparse
import logging
import os
import sys
import time
import uuid
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from signup_bot import report
from signup_bot.config import Config
from signup_bot.discovery import discover
from signup_bot.engine import Engine
from signup_bot.registry import Registry


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="base URL of the (authorised) test environment")
    ap.add_argument("--paths", nargs="*", default=[], help="signup paths to test explicitly")
    ap.add_argument("--discover", action="store_true", help="crawl for signup pages automatically")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--registry", default="signup_registry.json")
    ap.add_argument("--new-account", action="store_true", help="create a fresh account even if one is recorded")
    ap.add_argument("--email-domain", default="example.com")
    ap.add_argument("--email-local", default="qa.signup")
    ap.add_argument("--phone-cc", default="977")
    ap.add_argument("--phone-prefix", default="98")
    ap.add_argument("--phone-len", type=int, default=10)
    ap.add_argument("--country", default="Nepal")
    ap.add_argument("--min-interval", type=float, default=2.0, help="seconds between submissions")
    ap.add_argument("--step-timeout", type=float, default=15.0)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(os.path.join(a.out, "run.log"))])
    cfg = Config(email_domain=a.email_domain, email_local=a.email_local, phone_cc=a.phone_cc,
                 phone_local_prefix=a.phone_prefix, phone_local_len=a.phone_len, country_hint=a.country,
                 headless=not a.headed, out_dir=a.out, registry_path=a.registry, new_account=a.new_account,
                 min_interval=a.min_interval, step_timeout=a.step_timeout)
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    reg = Registry(cfg.registry_path)
    engine = Engine(cfg, reg, run_id)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=cfg.headless)
        targets = [urljoin(a.url, p) for p in a.paths]
        if a.discover or not targets:
            for t in discover(browser, a.url, cfg, a.paths):
                if t not in targets:
                    targets.append(t)
        logging.info("%d signup target(s): %s", len(targets), targets)
        results = [engine.run_flow(browser, t) for t in targets]
        browser.close()

    jpath, mpath = report.write(results, a.out, run_id, a.url)
    print(open(mpath).read())
    groups = [report.group(r.status) for r in results]
    if not results or "FAIL" in groups:
        return 1
    return 2 if "BLOCKED" in groups else 0


if __name__ == "__main__":
    sys.exit(main())
