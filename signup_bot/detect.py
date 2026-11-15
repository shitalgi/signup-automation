"""Single-pass DOM scan: fields, buttons, errors, CAPTCHA presence, page text."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SCAN_JS = r"""
() => {
  document.querySelectorAll('[data-sb-id],[data-sb-btn]').forEach(e => {
    e.removeAttribute('data-sb-id'); e.removeAttribute('data-sb-btn'); });
  const vis = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
      && el.closest('[aria-hidden="true"],[hidden]') === null;
  };
  const txt = el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  const forms = [...document.forms];
  const labelOf = el => {
    const parts = [];
    if (el.labels) for (const l of el.labels) parts.push(txt(l));
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(/\s+/).forEach(id => { const n = document.getElementById(id); if (n) parts.push(txt(n)); });
    if (el.getAttribute('aria-label')) parts.push(el.getAttribute('aria-label'));
    if (!parts.length) {
      let p = el.parentElement;
      for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
        const l = p.querySelector('label');
        if (l && !l.contains(el)) { parts.push(txt(l)); break; }
        const prev = p.previousElementSibling;
        if (prev && !prev.querySelector('input,select,textarea') && txt(prev) && txt(prev).length < 60) {
          parts.push(txt(prev)); break; }
      }
    }
    return parts.join(' ').trim();
  };

  const fields = []; let n = 0;
  document.querySelectorAll('input,select,textarea,[role="checkbox"],[role="radio"],[role="combobox"]').forEach(el => {
    const tag = el.tagName.toLowerCase(), role = el.getAttribute('role');
    let type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase()
             : (tag === 'select' || tag === 'textarea') ? tag : (role || 'text');
    if (['hidden','submit','button','image','reset','search'].includes(type)) return;
    let visible = vis(el);
    if (!visible && (type === 'checkbox' || type === 'radio'))
      visible = (el.labels ? [...el.labels].some(vis) : false) || vis(el.parentElement);
    if (!visible && type === 'file')
      visible = vis(el.parentElement) || (el.labels ? [...el.labels].some(vis) : false);
    if (!visible) return;
    if (el.disabled || el.readOnly || el.getAttribute('aria-disabled') === 'true') return;
    const r = el.getBoundingClientRect();
    if (r.left + scrollX < -500 || r.top + scrollY < -500) return;            // off-screen honeypot
    const nm = (el.getAttribute('name') || '') + ' ' + el.id;
    if (el.tabIndex === -1 && /honey|trap|bot[-_]?field|hp[-_]/i.test(nm)) return;  // honeypot
    el.setAttribute('data-sb-id', String(n));
    const fs = el.closest('fieldset');
    const form = el.form || el.closest('form');
    fields.push({
      id: n++, tag, type, role,
      name: el.getAttribute('name') || '', idattr: el.id || '',
      placeholder: el.getAttribute('placeholder') || '', label: labelOf(el),
      groupLabel: fs && fs.querySelector('legend') ? txt(fs.querySelector('legend')) : '',
      required: !!el.required || el.getAttribute('aria-required') === 'true',
      pattern: el.getAttribute('pattern'), minlength: el.getAttribute('minlength'),
      maxlength: el.getAttribute('maxlength'), min: el.getAttribute('min'), max: el.getAttribute('max'),
      accept: el.getAttribute('accept') || '', autocomplete: el.getAttribute('autocomplete') || '', value: el.value || '',
      checked: tag === 'input' ? !!el.checked : el.getAttribute('aria-checked') === 'true',
      formIdx: form ? forms.indexOf(form) : -1,
      options: tag === 'select' ? [...el.options].slice(0, 300).map(o => ({value: o.value, text: o.text.trim(), disabled: o.disabled})) : [],
    });
  });

  const buttons = []; let b = 0;
  document.querySelectorAll('button,input[type=submit],input[type=button],[role=button],a[class*=btn i],a[class*=button i]').forEach(el => {
    if (!vis(el)) return;
    const text = el.tagName === 'INPUT' ? el.value : (txt(el) || el.getAttribute('aria-label') || el.title || '');
    const form = el.form || el.closest('form');
    el.setAttribute('data-sb-btn', String(b));
    buttons.push({id: b++, text: text.slice(0, 80), type: (el.getAttribute('type') || '').toLowerCase(),
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      formIdx: form ? forms.indexOf(form) : -1, y: el.getBoundingClientRect().top + scrollY});
  });

  const errors = []; const seen = new Set();
  const add = (t, src) => { t = (t || '').replace(/\s+/g, ' ').trim();
    if (t.length >= 3 && t.length < 220 && !seen.has(t)) { seen.add(t); errors.push({text: t, src}); } };
  document.querySelectorAll('[role=alert],[aria-live=assertive],[class*=error i],[class*=invalid i],[class*=toast i],[class*=alert i],[class*=danger i],[data-error],.text-red-500,.text-destructive')
    .forEach(el => { if (vis(el)) add(txt(el), 'class'); });
  document.querySelectorAll('[aria-invalid=true]').forEach(el => {
    const id = el.getAttribute('aria-errormessage') || el.getAttribute('aria-describedby');
    const m = id && document.getElementById(id.split(' ')[0]);
    add((m && txt(m)) || el.validationMessage, 'aria-invalid'); });
  const nativeInvalid = [];
  document.querySelectorAll('input:invalid,select:invalid,textarea:invalid').forEach(el => {
    if (el.hasAttribute('data-sb-id')) nativeInvalid.push({id: +el.getAttribute('data-sb-id'),
      name: el.name || el.id, msg: el.validationMessage}); });

  const capSel = '.g-recaptcha,[data-sitekey],.h-captcha,.cf-turnstile,iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="turnstile"],iframe[title*="recaptcha" i],[class*=captcha i],[id*=captcha i]';
  const caps = [...document.querySelectorAll(capSel)];
  return {
    url: location.href, fields, buttons, errors, nativeInvalid,
    captchaPresent: caps.length > 0,
    captchaVisible: caps.some(c => vis(c) && c.getBoundingClientRect().height > 20),
    text: (document.body ? document.body.innerText : '').slice(0, 8000),
  };
}
"""


@dataclass
class Scan:
    url: str
    fields: list
    buttons: list
    errors: list
    native_invalid: list
    captcha_present: bool
    captcha_visible: bool
    text: str
    sig: str = ""

    @property
    def error_texts(self) -> list[str]:
        return [e["text"] for e in self.errors]


def scan(page) -> Scan:
    d = page.evaluate(SCAN_JS)
    sig_parts = sorted(f"{f['type']}|{f['name']}|{f['idattr']}|{f['label']}|{f['placeholder']}"
                       for f in d["fields"])
    return Scan(d["url"], d["fields"], d["buttons"], d["errors"], d["nativeInvalid"],
                d["captchaPresent"], d["captchaVisible"], d["text"], "\n".join(sig_parts))


# ---------------------------------------------------------------- patterns
ERR_RE = re.compile(r"invalid|error|fail|required|already|taken|exist|must|incorrect|too (short|long|weak)|"
                    r"not (valid|match|allowed|available)|should|cannot|can't|unable|wrong|try again|"
                    r"mismatch|denied|enter a|please (enter|provide|select|choose|check)", re.I)
VERIFY_RE = re.compile(r"verify your e-?mail|check your (e-?mail|inbox)|confirmation (e-?mail|link)|"
                       r"we('ve| have) sent|verification (code|link|e-?mail)|enter the (code|otp)|"
                       r"pending (approval|review)|under review|awaiting (approval|verification)|activate your account", re.I)
SUCCESS_RE = re.compile(r"account (has been |was |is )?(successfully )?created|registration (is |was )?(successful|complete|received|submitted)|"
                        r"(successfully|successful(ly)?) (registered|created|signed up|submitted)|"
                        r"thank you for (registering|signing up|joining)|sign-?up (successful|complete)|"
                        r"you('re| are) (all set|registered|in)|welcome aboard|application (has been )?(received|submitted)", re.I)
CAPTCHA_TEXT_RE = re.compile(r"captcha|are you (a )?(robot|human)|human verification|bot (detected|check)", re.I)
SUCCESS_URL_RE = re.compile(r"success|thank|welcome|dashboard|verify|confirm|complete|done|pending", re.I)
VERIFY_URL_RE = re.compile(r"verify|confirm|otp|pending", re.I)

BTN_POS = re.compile(r"sign\s*up|register|create( an)? account|join|submit|continue|next|finish|complete|"
                     r"get started|save|proceed|done|agree|apply|send|start", re.I)
BTN_NEG = re.compile(r"\bback\b|previous|cancel|log\s*in|login|sign\s*in|forgot|google|facebook|apple|github|"
                     r"microsoft|linkedin|\bsso\b|skip|reset|clear|close|menu|go to|demo|book|learn|contact|about", re.I)


def choose_action(sc: Scan):
    """Pick the primary forward button; None if nothing plausible."""
    form_idxs = {f["formIdx"] for f in sc.fields if f["formIdx"] >= 0}
    best, best_score = None, 0
    for b in sc.buttons:
        t = b["text"]
        if BTN_NEG.search(t):
            continue
        s = 0
        if BTN_POS.search(t):
            s += 3
        if b["type"] == "submit":
            s += 3
        if form_idxs:
            s += 2 if b["formIdx"] in form_idxs else -1
        if s <= 0 or (not BTN_POS.search(t) and b["type"] != "submit"):
            continue
        s += b["y"] / 1e6           # tie-break: lower on the page wins
        if s > best_score:
            best, best_score = b, s
    return best
