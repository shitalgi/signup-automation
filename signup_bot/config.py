from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Config:
    # --- test-data policy -------------------------------------------------
    # example.com is reserved: mail sent there never reaches a real person.
    email_domain: str = "example.com"
    email_local: str = "qa.signup"
    # Defaults describe a Nepal mobile number (the app in the recording uses +977).
    phone_cc: str = "977"
    phone_local_prefix: str = "98"
    phone_local_len: int = 10
    country_hint: str = "Nepal"

    # --- behaviour --------------------------------------------------------
    headless: bool = True
    max_steps: int = 12            # wizard steps per flow
    max_attempts: int = 4          # fill/submit attempts per step
    nav_timeout: float = 20.0      # seconds
    step_timeout: float = 15.0     # wait for an outcome after clicking
    quiet_after_post: float = 2.0  # seconds of silence after a 2xx POST
    flow_timeout: float = 180.0
    min_interval: float = 2.0      # min seconds between submit clicks (rate-limit courtesy)
    max_retry_after: float = 30.0

    # --- safety / dedupe --------------------------------------------------
    registry_path: str = "signup_registry.json"
    new_account: bool = False      # False => skip targets that already have an account
    same_origin_only: bool = True

    # --- discovery --------------------------------------------------------
    probe_common_paths: bool = True
    max_pages: int = 30
    common_paths: list[str] = field(default_factory=lambda: [
        "/signup", "/sign-up", "/register", "/registration", "/join",
        "/create-account", "/get-started", "/onboarding",
    ])

    out_dir: str = "reports"
