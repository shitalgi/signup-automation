import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from signup_bot import datagen, detect
from signup_bot.config import Config


def F(**k):
    base = dict(type="text", name="", idattr="", placeholder="", label="", autocomplete="", groupLabel="",
                required=True, pattern=None, minlength=None, maxlength=None, min=None, max=None, options=[])
    base.update(k)
    return base


class Classify(unittest.TestCase):
    def test_kinds(self):
        cases = [
            (F(type="email"), "email"), (F(type="tel"), "phone"), (F(type="password", label="Password"), "password"),
            (F(type="password", label="Confirm Password"), "password_confirm"),
            (F(type="password", name="pw2", placeholder="Re-enter password"), "password_confirm"),
            (F(label="First Name"), "first_name"), (F(label="Last Name"), "last_name"),
            (F(label="Agency Name"), "company"), (F(label="Full name"), "full_name"),
            (F(label="Phone Number"), "phone"), (F(label="Username"), "username"),
            (F(type="date", label="Date of birth"), "dob"), (F(type="url"), "website"),
            (F(type="checkbox"), "checkbox"), (F(type="file"), "file"),
            (F(type="select", label="Country"), "country"), (F(type="textarea", label="Business Address"), "message"),
            (F(label="Verification code"), "otp"), (F(autocomplete="given-name"), "first_name"),
        ]
        for f, want in cases:
            self.assertEqual(datagen.kind_of(f), want, f)


class Data(unittest.TestCase):
    def setUp(self):
        self.ctx = datagen.Context(Config(), "abc123def4")

    def test_unique_per_tag(self):
        other = datagen.Context(Config(), "zzz999yyy8")
        self.assertNotEqual(self.ctx.email_candidates()[0], other.email_candidates()[0])
        self.assertNotEqual(self.ctx.username, other.username)

    def test_password_complexity_and_confirm_match(self):
        p = self.ctx.value_for(F(type="password", label="Password"), "password")
        c = self.ctx.value_for(F(type="password", label="Confirm"), "password_confirm")
        self.assertEqual(p, c)
        self.assertTrue(any(x.isupper() for x in p) and any(x.islower() for x in p)
                        and any(x.isdigit() for x in p) and any(not x.isalnum() for x in p))

    def test_constraints(self):
        v = self.ctx.value_for(F(label="Username", maxlength="8"), "username")
        self.assertLessEqual(len(v), 8)
        v = self.ctx.value_for(F(type="number", label="Years", min="10", max="20"), "years")
        self.assertTrue(10 <= int(v) <= 20)
        v = self.ctx.value_for(F(label="Mobile", pattern=r"\+\d{13}"), "phone")
        self.assertRegex(v, r"^\+\d{13}$")

    def test_retry_rotates_candidates(self):
        a = self.ctx.value_for(F(type="tel"), "phone")
        self.ctx.attempt["phone"] = 2
        self.assertNotEqual(a, self.ctx.value_for(F(type="tel"), "phone"))
        self.assertTrue(self.ctx.value_for(F(type="tel"), "phone").startswith("+977"))

    def test_marketing_checkbox_left_unchecked_terms_checked(self):
        self.assertFalse(self.ctx.should_check(F(type="checkbox", label="Subscribe to newsletter", required=False)))
        self.assertTrue(self.ctx.should_check(F(type="checkbox", label="I agree to the Terms", required=False)))

    def test_otp_never_guessed(self):
        self.assertIs(self.ctx.value_for(F(label="OTP"), "otp"), datagen.SKIP)


class Buttons(unittest.TestCase):
    def sc(self, buttons, fields=None):
        return detect.Scan("u", fields or [], buttons, [], [], False, False, "")

    def b(self, text, y=0, **k):
        return dict(id=0, text=text, type=k.get("type", ""), disabled=False, formIdx=k.get("formIdx", -1), y=y)

    def test_picks_forward_button(self):
        sc = self.sc([self.b("Back", 10), self.b("Login", 0), self.b("Next", 20)])
        self.assertEqual(detect.choose_action(sc)["text"], "Next")

    def test_ignores_social_and_login(self):
        sc = self.sc([self.b("Continue with Google"), self.b("Sign in")])
        self.assertIsNone(detect.choose_action(sc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
