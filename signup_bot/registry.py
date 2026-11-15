"""Persistent registry of accounts we created. Prevents duplicate test accounts.

A target that has a PASSED / PENDING_VERIFICATION / UNCERTAIN record is skipped
on later runs unless --new-account is passed. UNCERTAIN counts because the
account may exist even though we could not confirm it.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit

try:
    import fcntl
    def _lock_file(f): fcntl.flock(f, fcntl.LOCK_EX)
    def _unlock_file(f): fcntl.flock(f, fcntl.LOCK_UN)
except ImportError:          # Windows
    import msvcrt
    def _lock_file(f):
        f.write(" "); f.flush(); f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    def _unlock_file(f):
        f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

BLOCKING = {"PASSED", "PASSED_PENDING_VERIFICATION", "UNCERTAIN"}


def target_key(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path.rstrip('/') or '/'}"


class Registry:
    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path):
            self._write({"accounts": []})

    @contextmanager
    def _lock(self):
        with open(self.path + ".lock", "w") as lf:
            _lock_file(lf)
            try:
                yield
            finally:
                _unlock_file(lf)

    def _read(self) -> dict:
        with open(self.path) as f:
            return json.load(f)

    def _write(self, data: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    def existing(self, url: str) -> dict | None:
        key = target_key(url)
        with self._lock():
            for a in self._read()["accounts"]:
                if a["target"] == key and a["status"] in BLOCKING:
                    return a
        return None

    def new_tag(self) -> str:
        """Return a tag never used before in this registry."""
        with self._lock():
            used = {a["tag"] for a in self._read()["accounts"]}
            while True:
                tag = uuid.uuid4().hex[:10]
                if tag not in used:
                    return tag

    def record(self, url: str, tag: str, email: str, status: str, run_id: str) -> None:
        """Insert or update the record for (target, tag)."""
        key = target_key(url)
        with self._lock():
            data = self._read()
            for a in data["accounts"]:
                if a["target"] == key and a["tag"] == tag:
                    a.update(status=status, email=email, updated=time.time())
                    break
            else:
                data["accounts"].append(dict(target=key, tag=tag, email=email,
                                             status=status, run_id=run_id,
                                             updated=time.time()))
            self._write(data)

    def all(self) -> list[dict]:
        with self._lock():
            return self._read()["accounts"]
