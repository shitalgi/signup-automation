"""Field classification and valid-test-data generation.

Classification uses only generic signals (type, name, id, label, placeholder,
autocomplete) - nothing is keyed to a particular site.
"""
from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass, field

from .config import Config

SKIP = object()  # sentinel: leave field alone

# Order matters: first match wins.
KIND_PATTERNS: list[tuple[str, str]] = [
    ("password_confirm", r"(confirm|repeat|re-?type|re-?enter|again|verify|match).{0,12}pass|pass.{0,12}(confirm|again|repeat)|confirm_?pw"),
    ("password", r"pass(word|code)?\b|\bpwd\b|new-password"),
    ("email", r"e-?mail"),
    ("phone", r"phone|mobile|\btel\b|telephone|contact.?(no|number)|whatsapp"),
    ("first_name", r"first.?name|given.?name|fname|forename"),
    ("last_name", r"last.?name|sur.?name|family.?name|lname"),
    ("username", r"user.?name|handle|login.?id|nick"),
    ("company", r"agency|company|organi[sz]ation|business|institution|employer|firm"),
    ("full_name", r"full.?name|your name|contact.?person|\bname\b"),
    ("otp", r"\botp\b|verification code|one.?time|captcha|security code"),
    ("dob", r"birth|\bdob\b|date of"),
    ("website", r"web.?site|\burl\b|portfolio|linkedin|link"),
    ("zip", r"\bzip|postal|post.?code|pin.?code"),
    ("city", r"\bcity|\btown"),
    ("state", r"\bstate\b|province|region|district"),
    ("country", r"country|nation"),
    ("address", r"address|street"),
    ("years", r"years?|experience|how many|number of|count|students|age\b"),
    ("license", r"licen[sc]e|registration.?(no|number)|\bpan\b|\bvat\b|\btax"),
    ("role", r"\brole\b|job|position|designation|title"),
    ("message", r"message|description|about|comment|bio|notes?|experience|reason|tell us"),
]

# Which error-message words implicate which field kind (for targeted retries).
KIND_ERROR_HINT = {
    "phone": r"phone|mobile|\btel|country code|\+\d",
    "password": r"pass",
    "password_confirm": r"pass|match",
    "email": r"e-?mail|already|taken|exist|registered|in use",
    "username": r"user|already|taken",
    "first_name": r"first|name",
    "last_name": r"last|name",
    "full_name": r"name",
    "dob": r"birth|date|age|18",
    "website": r"url|website|link",
    "company": r"agency|company|organi",
    "zip": r"zip|postal",
    "years": r"year|number|experience",
}

TERMS_RE = re.compile(r"terms|privacy|agree|accept|consent|conditions|policy|i have read|i understand|certify|confirm|accurate|acknowledge|declare|authori[sz]e", re.I)
MARKETING_RE = re.compile(r"newsletter|marketing|promo|updates|offers|subscribe", re.I)
PLACEHOLDER_OPT_RE = re.compile(r"^\s*(select|choose|please|--|—|pick)|^$", re.I)


def haystack(f: dict) -> str:
    return " ".join(str(f.get(k) or "") for k in
                    ("name", "idattr", "label", "placeholder", "autocomplete", "groupLabel")).lower()


def kind_of(f: dict) -> str:
    t, h = f["type"], haystack(f)
    ac = (f.get("autocomplete") or "").lower()
    if t == "file":
        return "file"
    if t == "checkbox" or t == "radio":
        return t
    if t == "select":
        base = _by_pattern(h)
        return base if base in ("country", "state", "role", "city") else "select"
    if t == "password":
        return "password_confirm" if re.search(KIND_PATTERNS[0][1], h) else "password"
    if t == "email" or ac == "email":
        return "email"
    if t == "tel" or ac.startswith("tel"):
        return "phone"
    if t == "url":
        return "website"
    if t == "date":
        return "dob" if re.search(r"birth|dob", h) else "date"
    if t in ("number", "range"):
        return "years" if _by_pattern(h) in ("years", "unknown") else "number"
    ac_map = {"given-name": "first_name", "family-name": "last_name", "username": "username",
              "organization": "company", "name": "full_name"}
    if ac in ac_map:
        return ac_map[ac]
    if t == "textarea":
        return "message"
    return _by_pattern(h)


def _by_pattern(h: str) -> str:
    for kind, pat in KIND_PATTERNS:
        if re.search(pat, h):
            return kind
    return "unknown"


@dataclass
class Context:
    """Per-flow identity. All values derive from `tag`, so they are unique per account."""
    cfg: Config
    tag: str
    attempt: dict = field(default_factory=dict)   # kind -> retry counter
    force_all_checkboxes: bool = False
    failed_rounds: int = 0

    def __post_init__(self):
        self.rng = random.Random(self.tag)
        letters = "".join(self.rng.choice(string.ascii_lowercase) for _ in range(5))
        self.suffix = letters
        self.first = "Qa"
        self.last = "Tester" + letters.capitalize()
        self.username = f"qa{self.tag[:8]}"
        self.company = f"QA Test Agency {letters.capitalize()}"
        digits = "".join(self.rng.choice(string.digits) for _ in range(self.cfg.phone_local_len))
        self.phone_local = (self.cfg.phone_local_prefix + digits)[: self.cfg.phone_local_len]

    def renew_tag(self, tag: str):
        self.tag = tag
        self.__post_init__()

    # -- value candidates ------------------------------------------------
    def email_candidates(self):
        c = self.cfg
        return [f"{c.email_local}+{self.tag}@{c.email_domain}",
                f"{c.email_local}.{self.tag}@{c.email_domain}"]

    def password_candidates(self):
        r = random.Random(self.tag + "pw")
        core = "".join(r.choice(string.ascii_letters + string.digits) for _ in range(10))
        return [f"Qa1!{core}x", f"Qa1{core}xyz", f"Qa1@{core}-Zz9#long"]

    def phone_candidates(self):
        cc, loc = self.cfg.phone_cc, self.phone_local
        return [loc, cc + loc, f"+{cc}{loc}", f"+{cc} {loc}"]

    def candidates(self, kind: str) -> list:
        c = self.cfg
        return {
            "email": self.email_candidates(),
            "password": self.password_candidates(),
            "password_confirm": self.password_candidates(),
            "phone": self.phone_candidates(),
            "first_name": [self.first, "Qatest"],
            "last_name": [self.last, "Tester"],
            "full_name": [f"{self.first} {self.last}", "Qa Tester"],
            "username": [self.username, f"qa_{self.tag[:6]}x", f"qauser{self.suffix}"],
            "company": [self.company, "QA Test Agency"],
            "website": ["https://example.com", "http://example.com", "example.com"],
            "address": ["12 Test Street", "12 Test St"],
            "city": ["Kathmandu", "Testville"],
            "state": ["Bagmati", "Test State"],
            "zip": ["44600", "12345"],
            "years": ["5", "3"],
            "license": ["QA" + "".join(ch for ch in self.tag if ch.isdigit()).ljust(6, "7")[:8], "QA123456"],
            "role": ["Manager", "Director"],
            "message": ["Automated QA test entry. Please ignore.", "Automated QA test."],
            "dob": ["1990-01-15", "1985-06-20"],
            "date": ["1990-01-15"],
            "number": ["5", "1"],
            "country": [c.country_hint],
            "unknown": ["QA Test", "QATest1"],
        }.get(kind, ["QA Test"])

    # -- value selection -------------------------------------------------
    def value_for(self, f: dict, kind: str):
        if kind == "otp":
            return SKIP                          # needs a real mailbox / phone; never guessed
        if kind == "file":
            return "FILE"
        if kind in ("checkbox", "radio", "select"):
            return SKIP                          # handled structurally by the engine
        if not f.get("required") and kind in ("unknown",):
            return SKIP
        cands = self.candidates(kind)
        idx = self.attempt.get(kind, 0) % len(cands)
        if kind == "password_confirm":
            idx = self.attempt.get("password", 0) % len(cands)
        v = cands[idx]
        if f.get("pattern"):          # prefer a candidate satisfying the field's own pattern
            for j in range(len(cands)):
                c = fit(cands[(idx + j) % len(cands)], f)
                if pattern_ok(c, f["pattern"]):
                    v = c
                    break
        if kind in ("years", "number"):
            v = _clamp_number(v, f)
        if kind in ("dob", "date") and f["type"] != "date":
            v = _date_for_placeholder(f.get("placeholder") or "", v)
        return fit(v, f)

    def should_check(self, f: dict) -> bool:
        label = f"{f.get('label') or ''} {f.get('name') or ''}"
        if f.get("required"):
            return True
        if MARKETING_RE.search(label) and not TERMS_RE.search(label):
            return self.force_all_checkboxes
        return bool(TERMS_RE.search(label)) or self.force_all_checkboxes

    def choose_option(self, f: dict, kind: str) -> str | None:
        opts = [o for o in f.get("options", []) if not o["disabled"] and o["value"] != ""
                and not PLACEHOLDER_OPT_RE.search(o["text"])]
        if not opts:
            return None
        if kind == "country":
            for o in opts:
                if self.cfg.country_hint.lower() in o["text"].lower():
                    return o["value"]
        return opts[0]["value"]


def _clamp_number(v: str, f: dict) -> str:
    try:
        n = float(v)
        lo = float(f["min"]) if f.get("min") not in (None, "") else None
        hi = float(f["max"]) if f.get("max") not in (None, "") else None
        if lo is not None and n < lo:
            n = lo
        if hi is not None and n > hi:
            n = hi
        return str(int(n)) if n == int(n) else str(n)
    except (ValueError, TypeError):
        return v


def _date_for_placeholder(ph: str, iso: str) -> str:
    y, m, d = iso.split("-")
    p = ph.lower()
    if "dd" in p and "mm" in p:
        sep = "/" if "/" in p else "-" if "-" in p else "."
        return sep.join([d, m, y] if p.index("dd") < p.index("mm") else [m, d, y])
    if "mm" in p and "dd" in p:
        return f"{m}/{d}/{y}"
    return iso


def fit(v: str, f: dict) -> str:
    """Make v respect maxlength / minlength / pattern where we can."""
    try:
        mx = int(f["maxlength"]) if f.get("maxlength") not in (None, "", -1) else None
        mn = int(f["minlength"]) if f.get("minlength") not in (None, "", -1) else None
    except (ValueError, TypeError):
        mx = mn = None
    if mn and len(v) < mn:
        v = v + "a1" * ((mn - len(v)) // 2 + 1)
    if mx and len(v) > mx:
        v = v[:mx]
    return v


def pattern_ok(v: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    try:
        return re.fullmatch(pattern, v) is not None
    except re.error:
        return True
