from __future__ import annotations

import json
import os
import time
from collections import Counter

from .engine import FlowResult

GROUPS = {
    "PASSED": "PASS", "PASSED_PENDING_VERIFICATION": "PASS",
    "BLOCKED_CAPTCHA": "BLOCKED", "BLOCKED_RATE_LIMIT": "BLOCKED",
    "SKIPPED_DUPLICATE": "SKIPPED",
}


def group(status: str) -> str:
    return GROUPS.get(status, "FAIL")


def write(results: list[FlowResult], out_dir: str, run_id: str, base_url: str = "") -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    jpath, mpath = os.path.join(out_dir, "results.json"), os.path.join(out_dir, "report.md")
    with open(jpath, "w") as f:
        json.dump({"run_id": run_id, "base_url": base_url, "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "results": [r.to_dict() for r in results]}, f, indent=2)
    c = Counter(group(r.status) for r in results)
    lines = [f"# Signup automation report", "",
             f"Run `{run_id}` - {time.strftime('%Y-%m-%d %H:%M:%S')} - base `{base_url}`", "",
             f"**{len(results)} pages: {c['PASS']} passed, {c['FAIL']} failed, {c['BLOCKED']} blocked by security controls, {c['SKIPPED']} skipped**", "",
             "| # | Page | Result | Group | Steps | Time | Detail |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append(f"| {i} | `{r.target}` | {r.status} | {group(r.status)} | {len(r.steps)} | {r.duration}s | "
                     f"{(r.detail or '').replace('|', '/')[:140]} |")
    lines += ["", "## Evidence", ""]
    for r in results:
        if r.evidence:
            lines.append(f"- `{r.target}`: " + "; ".join(r.evidence))
    with open(mpath, "w") as f:
        f.write("\n".join(lines) + "\n")
    return jpath, mpath
