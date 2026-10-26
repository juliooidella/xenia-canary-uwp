#!/usr/bin/env python3
"""Minimal Xbox Device Portal client for Xenia Canary UWP A/B runs (stdlib only).

Endpoints (Windows Device Portal core REST API, Xbox family):
  GET    /api/app/packagemanager/packages
  POST   /api/taskmanager/app?appid=<hex64>&package=<hex64>
  DELETE /api/taskmanager/app?package=<hex64>[&forcestop=yes]
  GET    /api/filesystem/apps/files?knownfolderid=LocalAppData&packagefullname=..&path=..
  GET    /api/filesystem/apps/file?knownfolderid=LocalAppData&packagefullname=..&filename=..&path=..
  POST   /api/filesystem/apps/file?knownfolderid=LocalAppData&packagefullname=..&extract=false&path=..
  DELETE /api/filesystem/apps/file?knownfolderid=LocalAppData&packagefullname=..&filename=..&path=..

"hex64" means the UTF-8 string base64-encoded. The Xbox portal listens on
https://<console-ip>:11443 with the username/password set in Dev Home; it uses a
self-signed certificate (verification is disabled here) and CSRF protection
(a GET first yields the CSRF-Token cookie, which is echoed as X-CSRF-Token).

The Xenia UWP port stores its files under the package's LocalState folder
(xenia-canary-uwp/UWPUtil.cpp GetLocalState -> ApplicationData.LocalFolder), so
`config/<TITLEID>.config.toml`, `patches/` and `xenia.log` live in
LocalAppData/<package>/LocalState.

Examples:
  devportal.py --host 192.168.0.50 --user dev --password secret packages
  devportal.py ... find xenia            # package full name + PRAID
  devportal.py ... ls --package <pfn> --path LocalState
  devportal.py ... push --package <pfn> --path LocalState/config ./variants/base/config/5553080B.config.toml
  devportal.py ... launch --package <pfn> --appid <praid>
  devportal.py ... stop --package <pfn>
  devportal.py ... pull --package <pfn> --path LocalState xenia.log ./runs/base.log
  devportal.py ... run-variant --package <pfn> --appid <praid> --title 5553080B \
        --config ./variants/base/config/5553080B.config.toml --wait 240 --out ./runs/base.log
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path


def hex64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


class DevicePortal:
    def __init__(self, host: str, user: str, password: str, port: int = 11443, verify_tls: bool = False):
        self.base = f"https://{host}:{port}"
        self.cookies = CookieJar()
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        pw = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        pw.add_password(None, self.base, user, password)
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.HTTPBasicAuthHandler(pw),
            urllib.request.HTTPCookieProcessor(self.cookies),
        )
        self._csrf: str | None = None

    # -- low level ---------------------------------------------------------
    def _request(self, method: str, path: str, params: dict | None = None, data: bytes | None = None,
                 headers: dict | None = None, timeout: int = 60) -> bytes:
        if method != "GET" and self._csrf is None:
            self._fetch_csrf()
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        if self._csrf and method != "GET":
            req.add_header("X-CSRF-Token", self._csrf)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {body[:500]}") from None

    def _fetch_csrf(self) -> None:
        self._request("GET", "/api/os/info")
        for c in self.cookies:
            if c.name == "CSRF-Token":
                self._csrf = c.value
        if self._csrf is None:
            self._csrf = ""  # portal without CSRF enforcement

    def _json(self, method: str, path: str, params: dict | None = None) -> dict:
        raw = self._request(method, path, params)
        return json.loads(raw.decode("utf-8")) if raw else {}

    # -- apps --------------------------------------------------------------
    def packages(self) -> list[dict]:
        return self._json("GET", "/api/app/packagemanager/packages").get("InstalledPackages", [])

    def find_package(self, needle: str) -> list[dict]:
        n = needle.lower()
        return [p for p in self.packages()
                if n in p.get("PackageFullName", "").lower() or n in p.get("Name", "").lower()]

    def launch(self, package_full_name: str, appid: str) -> None:
        self._request("POST", "/api/taskmanager/app",
                      {"appid": hex64(appid), "package": hex64(package_full_name)})

    def stop(self, package_full_name: str, force: bool = True) -> None:
        params = {"package": hex64(package_full_name)}
        if force:
            params["forcestop"] = "yes"
        self._request("DELETE", "/api/taskmanager/app", params)

    # -- files -------------------------------------------------------------
    @staticmethod
    def _path(path: str) -> str:
        # The Xbox portal wants the sub-path rooted at the package folder, with
        # backslashes: "LocalState" -> "\LocalState", "LocalState/config" -> "\LocalState\config".
        path = path.replace("/", "\\").strip("\\")
        return "\\" + path if path else "\\"

    def ls(self, package_full_name: str, path: str = "LocalState") -> list[dict]:
        return self._json("GET", "/api/filesystem/apps/files",
                          {"knownfolderid": "LocalAppData", "packagefullname": package_full_name,
                           "path": self._path(path)}).get("Items", [])

    def pull(self, package_full_name: str, path: str, filename: str, dest: Path) -> int:
        data = self._request("GET", "/api/filesystem/apps/file",
                             {"knownfolderid": "LocalAppData", "packagefullname": package_full_name,
                              "path": self._path(path), "filename": filename}, timeout=600)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(data)

    def push(self, package_full_name: str, path: str, local: Path, remote_name: str | None = None) -> None:
        remote_name = remote_name or local.name
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(remote_name)[0] or "application/octet-stream"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{remote_name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + local.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        self._request("POST", "/api/filesystem/apps/file",
                      {"knownfolderid": "LocalAppData", "packagefullname": package_full_name,
                       "extract": "false", "path": self._path(path)},
                      data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                      timeout=600)

    def delete(self, package_full_name: str, path: str, filename: str) -> None:
        self._request("DELETE", "/api/filesystem/apps/file",
                      {"knownfolderid": "LocalAppData", "packagefullname": package_full_name,
                       "path": self._path(path), "filename": filename})


def run_variant(dp: DevicePortal, package: str, appid: str, title: str, config: Path, wait: int,
                out: Path, log_name: str = "xenia.log") -> Path:
    """Push a per-title config, (re)start Xenia, wait, stop it and pull the log.

    The game itself still has to be started from the Xenia UI on the console
    (or via the port's automatic-launch setting), so `wait` must cover that.
    """
    try:
        dp.stop(package)
    except RuntimeError:
        pass
    dp.push(package, "LocalState\\config", config, f"{title.upper()}.config.toml")
    try:
        dp.delete(package, "LocalState", log_name)
    except RuntimeError:
        pass
    dp.launch(package, appid)
    time.sleep(wait)
    dp.stop(package)
    time.sleep(3)
    dp.pull(package, "LocalState", log_name, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=11443)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("packages")
    p = sub.add_parser("find"); p.add_argument("needle")
    p = sub.add_parser("ls"); p.add_argument("--package", required=True); p.add_argument("--path", default="LocalState")
    p = sub.add_parser("pull"); p.add_argument("--package", required=True); p.add_argument("--path", default="LocalState")
    p.add_argument("filename"); p.add_argument("dest", type=Path)
    p = sub.add_parser("push"); p.add_argument("--package", required=True); p.add_argument("--path", default="LocalState")
    p.add_argument("local", type=Path); p.add_argument("--name")
    p = sub.add_parser("launch"); p.add_argument("--package", required=True); p.add_argument("--appid", required=True)
    p = sub.add_parser("stop"); p.add_argument("--package", required=True)
    p = sub.add_parser("run-variant")
    p.add_argument("--package", required=True); p.add_argument("--appid", required=True)
    p.add_argument("--title", required=True); p.add_argument("--config", required=True, type=Path)
    p.add_argument("--wait", type=int, default=240); p.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    dp = DevicePortal(args.host, args.user, args.password, args.port)
    if args.cmd == "packages":
        for pk in dp.packages():
            print(pk.get("PackageFullName"), "|", pk.get("Name"), "|",
                  [a.get("AppId") for a in pk.get("AppEntries", [])])
    elif args.cmd == "find":
        for pk in dp.find_package(args.needle):
            print(pk.get("PackageFullName"), "|", [a.get("AppId") for a in pk.get("AppEntries", [])])
    elif args.cmd == "ls":
        for it in dp.ls(args.package, args.path):
            print(("d " if it.get("Type") == 16 else "f "), it.get("Name"), it.get("Size", ""))
    elif args.cmd == "pull":
        n = dp.pull(args.package, args.path, args.filename, args.dest)
        print(f"{n} bytes -> {args.dest}")
    elif args.cmd == "push":
        dp.push(args.package, args.path, args.local, args.name)
        print("uploaded")
    elif args.cmd == "launch":
        dp.launch(args.package, args.appid); print("launched")
    elif args.cmd == "stop":
        dp.stop(args.package); print("stopped")
    elif args.cmd == "run-variant":
        out = run_variant(dp, args.package, args.appid, args.title, args.config, args.wait, args.out)
        print(f"log saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
