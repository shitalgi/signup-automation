# Signup automation

Generic Playwright framework: discover signup pages -> detect fields -> fill valid unique test data -> submit ->
handle multi-step flows / validation errors -> verify -> report. No page-specific selectors.

    python signup_automation_script.py --url https://YOUR-AUTHORISED-TEST-ENV --discover      # crawl + test all signup pages
    python signup_automation_script.py --url https://... --paths /register /signup            # explicit pages
    python tests/test_unit.py && python -m tests.test_e2e                       # self-tests against the mock site

Options worth knowing: `--email-domain` / `--email-local` (use a real catch-all inbox if you need email verification),
`--phone-cc/--phone-prefix/--phone-len/--country` (defaults: Nepal +977), `--min-interval` (rate-limit courtesy),
`--new-account` (create another account even if the registry has one for that page).

Outputs in `--out` (default `reports/`): `report.md`, `results.json`, `run.log`, `screenshots/`.
Statuses: PASSED, PASSED_PENDING_VERIFICATION, BLOCKED_CAPTCHA, BLOCKED_RATE_LIMIT, SKIPPED_DUPLICATE,
UNCERTAIN (submitted, outcome unverifiable; never retried), FAILED_* (VALIDATION, SUBMISSION, TIMEOUT, NAVIGATION, NO_FORM, NO_SUBMIT, NO_PROGRESS).
Exit code: 0 all pass, 1 any failure, 2 only CAPTCHA/rate-limit blocks.

Safety: visible CAPTCHA => stop before submitting (never bypassed); HTTP 429 => one Retry-After wait then stop;
`signup_registry.json` prevents duplicate accounts (ambiguous outcomes are remembered as UNCERTAIN and never resubmitted);
OTP/verification-code fields are never guessed; honeypots and optional marketing checkboxes are left alone.
Known limits: email/SMS verification needs a mailbox/number you control; custom non-ARIA widgets and
multi-page flows that require a human-only step are reported, not forced.

## Prerequisites and environment
- Python 3.10+ (developed on 3.13; works on Windows, macOS, Linux), Playwright for Python 1.63.0, Chromium (installed by Playwright, no separate driver needed)
- Setup:

      pip install -r requirements.txt
      playwright install chromium

## How to run
    python signup_automation_script.py --url <BASE_URL> --discover        # find and test every signup page
    python signup_automation_script.py --url <BASE_URL> --paths /register # specific page(s)
    python tests/test_unit.py                                             # unit tests
    python -m tests.test_e2e                                              # e2e tests on the bundled mock site

## Test data and accounts used
No pre-existing accounts are needed. Each run generates unique data per account:
- Email: `qa.signup+<random-tag>@example.com` (example.com is reserved, mail never reaches a real person)
- Name: `Qa Tester<letters>`; password: random 14+ chars with upper/lower/digit/symbol
- Phone: Nepal format (+977, starts with 98); change with --phone-cc / --phone-prefix / --phone-len
- Agency/address/website/etc.: fixed dummy values (`QA Test Agency <letters>`, `https://example.com`)
- Uploads: a generated 1px PNG or minimal PDF
Created accounts are recorded in `signup_registry.json` (used to prevent duplicates).
