"""Local mock application used to test the automation end-to-end.

It mirrors the structure seen in the recorded app (home -> login -> consent page ->
4-step agency wizard with a +977 phone field) and adds variants that exercise
every behaviour of the engine: native form POST, masks, radios/selects/date,
CAPTCHA, server-side rejection, email conflict, 500s, hangs and 429s.
Server-side validation is real, and /__state exposes created accounts so tests
can assert there are no duplicates.
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ACCOUNTS: list[dict] = []
HITS: dict[str, int] = {}
LOCK = threading.Lock()

CSS = """<style>body{font-family:sans-serif;max-width:640px;margin:30px auto}label{display:block;margin-top:10px}
input,select,textarea{display:block;padding:6px;width:320px}.error{color:#c00}.hide{display:none}
.cb{position:relative}.cb input{position:absolute;opacity:0;width:1px;height:1px}.cb span{display:inline-block;border:1px solid #333;padding:2px 6px}
button{margin-top:14px;padding:8px 18px}.ph{display:flex;gap:6px;align-items:center}.ph input{width:240px}</style>"""


def page(title, body, script=""):
    return f"<!doctype html><html><head><meta charset=utf-8><title>{title}</title>{CSS}</head><body>{body}<script>{script}</script></body></html>"


HOME = page("Mock Partner", """
<nav><a href="/">Home</a> | <a href="/login">Login</a> | <a href="/contact">Contact Us</a> | <a href="/register"><button>Get Started</button></a></nav>
<h1>Empowering Agents</h1><p>Create accounts:</p><ul>
<li><a href="/signup-basic">Sign up - basic</a></li><li><a href="/signup-mixed">Sign up - mixed controls</a></li>
<li><a href="/signup-captcha">Sign up - captcha</a></li><li><a href="/signup-validation">Sign up - strict phone</a></li>
<li><a href="/signup-conflict">Sign up - email conflict</a></li><li><a href="/signup-500">Sign up - broken backend</a></li>
<li><a href="/signup-slow">Sign up - hangs</a></li><li><a href="/signup-ratelimit">Sign up - rate limited</a></li></ul>""")

LOGIN = page("Login", """<h1>Log in</h1><form onsubmit="return false"><label>Email<input type=email name=email></label>
<label>Password<input type=password name=password></label><button>Log In</button></form>
<p>Don't have an account? <a href="/signup-basic">Sign Up</a></p>""")

CONTACT = page("Contact", """<h1>Contact us</h1><form onsubmit="return false"><label>Name<input name=name></label>
<label>Email<input type=email name=email></label><label>Message<textarea name=msg></textarea></label><button>Send</button></form>""")


def generic_form(title, action_js, extra="", captcha=False):
    cap = '<div class="g-recaptcha" data-sitekey="mock" style="width:300px;height:78px;border:1px solid #999">reCAPTCHA</div>' if captcha else ""
    return page(title, f"""<h1>{title}</h1><form id=f novalidate>
<label for=n>Full name</label><input id=n name=full_name required>
<label for=e>Email address</label><input id=e type=email name=email required>
{extra}
<label for=p>Password</label><input id=p type=password name=password required minlength=8>
<label for=c>Confirm password</label><input id=c type=password name=confirm required>
<label class=cb><input type=checkbox id=t required><span>I agree to the Terms of Service</span></label>
{cap}<p class=error id=err></p><button type=submit>Create account</button></form>""", action_js)


def post_js(endpoint, ok_text="Account created successfully. Welcome!"):
    return """
const f=document.getElementById('f'),err=document.getElementById('err');
f.addEventListener('submit',async ev=>{ev.preventDefault();err.textContent='';
 const d=Object.fromEntries([...f.querySelectorAll('input,select')].filter(i=>i.name).map(i=>[i.name,i.value]));
 d.terms=document.getElementById('t').checked;
 try{const r=await fetch('%s',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
 const j=await r.json().catch(()=>({}));
 if(r.ok){f.outerHTML='<h2>%s</h2>';}
 else err.textContent=(j.errors?Object.values(j.errors).join(' '):(j.error||'Something went wrong. Please try again.'));
 }catch(e){err.textContent='Network error'}});""" % (endpoint, ok_text)


PHONE_EXTRA = '<label for=ph>Phone number</label><input id=ph name=phone placeholder="+97798XXXXXXXX" required>'

REGISTER_CONSENT = page("Register Your Agency", """<a href="/login">Go to Login</a><h1>Register Your Agency</h1>
<p>Before you get started, please review our terms and conditions.</p>
<label class=cb><input type=checkbox id=agree><span>I agree to the <a href="#">Terms of Service</a> and <a href="#">Privacy Policy</a></span></label>
<button id=go disabled>Continue</button>""",
                        "const a=document.getElementById('agree'),g=document.getElementById('go');a.onchange=()=>g.disabled=!a.checked;g.onclick=()=>location.href='/register?step=setup';")

WIZARD = page("Complete Your Agent Profile", """<a href="/login">Go to Login</a><h1>Complete Your Agent Profile in Steps</h1>
<div id=tabs><b>1. Set up your Account</b> | 2. Agency Details | 3. Professional Experience | 4. Verification and Preferences</div>
<div class=step id=s1><h3>Provide your personal details.</h3>
<label>First Name<input id=fn placeholder="Enter first name"></label><label>Last Name<input id=ln placeholder="Enter last name"></label>
<label>Email Address<input id=em placeholder="Enter email address"></label>
<label>Phone Number</label><div class=ph><span>NP +977</span><input id=phn placeholder="98XXXXXXXX" maxlength=10></div>
<label>Password<input id=pw type=password></label><label>Confirm Password<input id=pw2 type=password></label></div>
<div class="step hide" id=s2><h3>Agency details</h3><label>Agency Name<input id=ag></label><label>Your role<select id=role><option value="">Select role</option><option>Owner</option><option>Counsellor</option></select></label>
<label>Country<select id=ctry><option value="">Select country</option><option>India</option><option>Nepal</option></select></label>
<label>Agency Website<input id=web type=url placeholder="https://"></label><label>Business Address<textarea id=addr></textarea></label></div>
<div class="step hide" id=s3><h3>Professional experience</h3><label>Years of experience<input id=yrs type=number min=0 max=50></label>
<fieldset><legend>Have you recruited students before?</legend><label><input type=radio name=rec value=yes>Yes</label><label><input type=radio name=rec value=no>No</label></fieldset>
<label>Tell us about your experience<textarea id=exp></textarea></label></div>
<div class="step hide" id=s4><h3>Verification and Preferences</h3><label>Business registration document<input type=file id=doc accept="image/*,.pdf"></label>
<label class=cb><input type=checkbox id=news><span>Send me product updates</span></label>
<label class=cb><input type=checkbox id=conf><span>I confirm the information provided is accurate</span></label></div>
<p class=error id=err></p><button id=back class=hide>Back</button> <button id=next>Next</button>""", """
let cur=1;const $=id=>document.getElementById(id),err=$('err');
function show(n){cur=n;document.querySelectorAll('.step').forEach((s,i)=>s.classList.toggle('hide',i+1!==n));$('back').classList.toggle('hide',n===1);$('next').textContent=n===4?'Submit':'Next';}
function check(n){const need={1:['fn','ln','em','phn','pw','pw2'],2:['ag','role','ctry','web','addr'],3:['yrs','exp'],4:[]}[n];
 for(const id of need) if(!$(id).value.trim()) return 'Please fill in all required fields ('+id+')';
 if(n===1){if(!/^[^@\\s]+@[^@\\s]+\\.[a-z]{2,}$/i.test($('em').value))return 'Enter a valid email address';
  if(!/^9\\d{9}$/.test($('phn').value))return 'Phone number must be 10 digits starting with 9';
  const p=$('pw').value;if(!(p.length>=8&&/[A-Z]/.test(p)&&/[a-z]/.test(p)&&/\\d/.test(p)&&/[^A-Za-z0-9]/.test(p)))return 'Password must have 8+ chars, upper, lower, number and symbol';
  if(p!==$('pw2').value)return 'Passwords do not match';}
 if(n===3&&!document.querySelector('input[name=rec]:checked'))return 'Please select an option';
 if(n===4){if(!$('doc').files.length)return 'Please upload a document';if(!$('conf').checked)return 'You must confirm the information is accurate';}
 return '';}
$('back').onclick=()=>show(cur-1);
$('next').onclick=async()=>{err.textContent='';const m=check(cur);if(m){err.textContent=m;return;}
 if(cur<4){show(cur+1);return;}
 const body={first:$('fn').value,last:$('ln').value,email:$('em').value,phone:'+977'+$('phn').value,password:$('pw').value,agency:$('ag').value,
  role:$('role').value,country:$('ctry').value,website:$('web').value,address:$('addr').value,years:$('yrs').value,
  recruited:document.querySelector('input[name=rec]:checked').value,file:$('doc').files[0].name,news:$('news').checked};
 const r=await fetch('/api/register-agency',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const j=await r.json().catch(()=>({}));if(r.ok)location.href='/register/success';else err.textContent=j.error||'Registration failed';};
""")

SUCCESS = page("Registered", "<h1>Registration submitted</h1><p>Please verify your email: we've sent a confirmation link to your inbox.</p>")

MIXED = page("Sign up mixed", """<h1>Join us</h1><form id=f novalidate>
<label for=u>Username</label><input id=u name=username required pattern="[a-z0-9_]{5,16}" maxlength=16>
<label for=e>Email</label><input id=e type=email name=email required>
<label for=d>Date of birth</label><input id=d type=date name=dob required>
<label for=ct>Country</label><select id=ct name=country required><option value="" disabled selected>Select country</option><option value=in>India</option><option value=np>Nepal</option></select>
<fieldset><legend>Gender</legend><label><input type=radio name=gender value=f required>Female</label><label><input type=radio name=gender value=m>Male</label><label><input type=radio name=gender value=o>Other</label></fieldset>
<label for=ph>Mobile</label><input id=ph name=phone required placeholder="(555) 555-5555">
<label for=p>Password</label><input id=p type=password name=password required pattern="(?=.*\\d)(?=.*[a-z])(?=.*[A-Z]).{8,}" minlength=8>
<label class=cb><input type=checkbox id=nl name=newsletter><span>Subscribe to our newsletter</span></label>
<label class=cb><input type=checkbox id=t required><span>I accept the privacy policy</span></label>
<input type=text name=website_url tabindex=-1 autocomplete=off style="position:absolute;left:-9999px">
<p class=error id=err></p><button type=submit>Sign up</button></form>""", """
const ph=document.getElementById('ph');ph.addEventListener('input',()=>{const d=ph.value.replace(/\\D/g,'').slice(0,10);
 ph.value=d.length>6?`(${d.slice(0,3)}) ${d.slice(3,6)}-${d.slice(6)}`:d.length>3?`(${d.slice(0,3)}) ${d.slice(3)}`:d;});
const f=document.getElementById('f'),err=document.getElementById('err');
f.addEventListener('submit',async ev=>{ev.preventDefault();err.textContent='';
 if(!f.checkValidity()){err.textContent='Please correct the highlighted fields';return;}
 const d={username:u.value,email:e.value,dob:dob.value||d_.value,country:ct.value,gender:document.querySelector('[name=gender]:checked').value,
  phone:ph.value.replace(/\\D/g,''),password:p.value,newsletter:nl.checked,honeypot:document.querySelector('[name=website_url]').value};
 const r=await fetch('/api/signup-mixed',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
 const j=await r.json().catch(()=>({}));if(r.ok)f.outerHTML='<h2>Sign-up complete. You are all set!</h2>';else err.textContent=j.error||'Error';});
const d_=document.getElementById('d');""".replace("dob.value||d_.value", "d_.value"))

STATIC = {
    "/": HOME, "/login": LOGIN, "/contact": CONTACT,
    "/signup-captcha": generic_form("Sign up (captcha)", post_js("/api/signup-captcha"), captcha=True),
    "/signup-validation": generic_form("Sign up (strict phone)", post_js("/api/signup-validation"), PHONE_EXTRA),
    "/signup-conflict": generic_form("Sign up (conflict)", post_js("/api/signup-conflict")),
    "/signup-500": generic_form("Sign up (broken)", post_js("/api/signup-500")),
    "/signup-slow": generic_form("Sign up (slow)", post_js("/api/signup-slow")),
    "/signup-ratelimit": generic_form("Sign up (limited)", post_js("/api/signup-ratelimit")),
    "/signup-mixed": MIXED, "/register/success": SUCCESS,
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
PW_RE = re.compile(r"^(?=.*\d)(?=.*[a-z])(?=.*[A-Z]).{8,}$")


def basic_page(error="", values=None):
    v = values or {}
    return page("Sign up basic", f"""<h1>Sign up</h1><form method=post action="/signup-basic">
<label for=n>Full name</label><input id=n name=full_name required value="{v.get('full_name','')}">
<label for=e>Email</label><input id=e type=email name=email required>
<label for=p>Password</label><input id=p type=password name=password required minlength=8 pattern="(?=.*\\d)(?=.*[a-z])(?=.*[A-Z]).{{8,}}">
<label for=c>Re-enter password</label><input id=c type=password name=confirm_password required>
<label><input type=checkbox name=terms required> I agree to the terms</label>
<p class=error>{error}</p><button type=submit>Register</button></form>""")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _json(self, code, obj, headers=None):
        self._send(code, json.dumps(obj), "application/json", headers)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/__state":
            return self._json(200, {"accounts": ACCOUNTS, "hits": HITS})
        if u.path == "/signup-basic":
            return self._send(200, basic_page())
        if u.path == "/register":
            return self._send(200, WIZARD if parse_qs(u.query).get("step") == ["setup"] else REGISTER_CONSENT)
        if u.path in STATIC:
            return self._send(200, STATIC[u.path])
        self._send(404, "<h1>404</h1>")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode() if n else ""

    def do_POST(self):
        u = urlsplit(self.path)
        raw = self._body()
        with LOCK:
            HITS[u.path] = HITS.get(u.path, 0) + 1
            hits = HITS[u.path]
        if u.path == "/signup-basic":
            d = {k: v[0] for k, v in parse_qs(raw).items()}
            if not EMAIL_RE.match(d.get("email", "")):
                return self._send(200, basic_page("Invalid email"))
            if d.get("password") != d.get("confirm_password"):
                return self._send(200, basic_page("Passwords do not match"))
            if not PW_RE.match(d.get("password", "")) or not d.get("terms"):
                return self._send(200, basic_page("Weak password or terms not accepted"))
            self._create("basic", d)
            return self._send(200, page("Done", "<h1>Account created successfully</h1>"))
        try:
            d = json.loads(raw or "{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        ep = u.path.rsplit("/", 1)[-1]
        if ep == "signup-500":
            return self._json(500, {"error": "Internal error"})
        if ep == "signup-slow":
            time.sleep(40)
            return self._json(200, {})
        if ep == "signup-ratelimit":
            return self._json(429, {"error": "Too many requests"}, {"Retry-After": "1"})
        if ep == "signup-captcha":
            return self._json(400, {"error": "CAPTCHA token missing"})
        if ep == "register-agency":
            if not EMAIL_RE.match(d.get("email", "")) or not PW_RE.match(d.get("password", "")) \
                    or not re.match(r"^\+9779\d{9}$", d.get("phone", "")):
                return self._json(422, {"error": "Invalid email, password or phone"})
            if not d.get("file") or not d.get("agency") or not d.get("recruited"):
                return self._json(422, {"error": "Missing fields"})
            if self._exists(d["email"]):
                return self._json(409, {"error": "Email already registered"})
            self._create("wizard", d)
            return self._json(201, {"ok": True})
        if ep == "signup-mixed":
            if d.get("honeypot"):
                return self._json(400, {"error": "bot"})
            if len(d.get("phone", "")) != 10 or not re.match(r"^[a-z0-9_]{5,16}$", d.get("username", "")):
                return self._json(422, {"error": "Invalid phone or username"})
            if d.get("newsletter"):
                return self._json(422, {"error": "newsletter should have stayed unchecked in this test"})
            self._create("mixed", d)
            return self._json(201, {"ok": True})
        # generic forms
        if not EMAIL_RE.match(d.get("email", "")) or not PW_RE.match(d.get("password", "")) \
                or d.get("password") != d.get("confirm") or not d.get("terms"):
            return self._json(422, {"error": "Invalid input"})
        if ep == "signup-validation" and not str(d.get("phone", "")).startswith("+"):
            return self._json(422, {"errors": {"phone": "Phone must start with a country code, e.g. +977..."}})
        if ep == "signup-conflict" and hits == 1:
            return self._json(409, {"errors": {"email": "This email is already registered."}})
        if self._exists(d["email"]):
            return self._json(409, {"error": "Email already registered"})
        self._create(ep, d)
        self._json(201, {"ok": True})

    def _exists(self, email):
        with LOCK:
            return any(a["email"].lower() == email.lower() for a in ACCOUNTS)

    def _create(self, form, d):
        with LOCK:
            ACCOUNTS.append({"form": form, "email": d.get("email", ""), "data": {k: v for k, v in d.items() if k != "password"}})


class Server:
    def __init__(self, port=0):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.t.start()
        return self

    def stop(self):
        self.httpd.shutdown()


if __name__ == "__main__":
    s = Server(8765).start()
    print("mock site on", s.url)
    while True:
        time.sleep(3600)
