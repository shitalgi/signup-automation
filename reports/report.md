# Signup automation report

Run `20261114-105101-08f5` - 2026-11-14 10:52:08 - base `https://authorized-partner.vercel.app`

**3 pages: 1 passed, 2 failed, 0 blocked by security controls, 0 skipped**

| # | Page | Result | Group | Steps | Time | Detail |
|---|---|---|---|---|---|---|
| 1 | `https://authorized-partner.vercel.app/register` | PASSED_PENDING_VERIFICATION | PASS | 2 | 7.08s | registration accepted; awaiting email/OTP/approval (external step) |
| 2 | `https://authorized-partner.vercel.app/contact-us` | FAILED_VALIDATION | FAIL | 1 | 21.04s | Required |
| 3 | `https://authorized-partner.vercel.app/login` | FAILED_VALIDATION | FAIL | 1 | 9.68s | No active account found with the given credentials |

## Evidence

- `https://authorized-partner.vercel.app/register`: POST /api/tempuser/ -> 201; confirmation text/URL: 'Verification code'
