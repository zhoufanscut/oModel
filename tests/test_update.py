"""test_update.py — `omodel --update`: version maths, install detection, and the binary swap.

**Nothing here touches the network.** `update._open` is the module's single network seam by
design, and every test that needs a response monkeypatches that (the `_Net` helper below); a URL
no test declared raises, so an accidental real fetch fails loudly rather than quietly reaching
api.github.com from CI. The exceptions are deliberate: `TestTokenHandling` and the HTTP-error
tests patch `update._opener` one level *lower*, because `_open` is the code under test there.

The swap tests are the point of this file. They build a real gzip tarball whose `omodel` is a
tiny shell script that answers `--version`, point `apply_update` at a real file on disk, and
assert the two properties that matter for something that replaces the program you are running:

  * on success the target file IS the new binary (and keeps its old permissions);
  * on ANY failure — bad checksum, a binary that won't run, one that reports the wrong version,
    a tarball with no `omodel` in it — the target is **byte-for-byte untouched**.

POSIX-only, like the binaries themselves (`release.yml` publishes linux-x64 + darwin-arm64).
"""
from __future__ import annotations

import io
import json
import os
import stat
import tarfile
import time

import pytest

from omodel import cli, update

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ASSET = "omodel-linux-x64"
TARBALL = f"{ASSET}.tar.gz"
DOWNLOAD = f"https://github.com/{update.REPO}/releases/download/v9.9.9/{TARBALL}"
SUMS = f"{DOWNLOAD}.sha256"


def _release_json(tag="v9.9.9", with_checksum=True, assets=True):
    """The subset of GitHub's `releases/latest` payload that `latest_release` reads."""
    files = []
    if assets:
        files.append({"name": TARBALL, "browser_download_url": DOWNLOAD})
        if with_checksum:
            files.append({"name": f"{TARBALL}.sha256", "browser_download_url": SUMS})
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/{update.REPO}/releases/tag/{tag}",
        "published_at": "2026-07-31T00:00:00Z",
        "assets": files,
    }


def _make_tarball(path, version="9.9.9", prints=None, exit_code=0, member="omodel", extra=None):
    """A release-shaped tarball whose `omodel` is a shell script answering `--version`.

    `prints`/`exit_code` are how the smoke-test failures are staged; `member` renames the binary
    (to test an asset that carries none); `extra` adds a hostile path-traversal member."""
    stage = path.parent / "stage"
    stage.mkdir(exist_ok=True)
    script = stage / "omodel"
    reported = version if prints is None else prints
    script.write_text(f"#!/bin/sh\necho '{reported}'\nexit {exit_code}\n")
    script.chmod(0o755)
    notice = stage / "NOTICE"
    notice.write_text("omo attribution\n")
    with tarfile.open(str(path), "w:gz") as tar:
        tar.add(str(script), arcname=member)
        tar.add(str(notice), arcname="NOTICE")   # the real tarball ships LICENSE + NOTICE too
        if extra is not None:
            tar.add(str(notice), arcname=extra)
    return path


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class _Net:
    """Stub for `update._open`. Routes are URL -> bytes/str/Exception; anything else raises."""

    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def open(self, url, timeout):
        self.urls.append(url)
        if url not in self.routes:
            raise AssertionError(f"test tried to fetch an undeclared URL: {url}")
        payload = self.routes[url]
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        return io.BytesIO(payload)


@pytest.fixture
def binary_install(tmp_path, monkeypatch):
    """A fake installed binary + an `Install` pointing at it, with the platform pinned to
    linux-x64 so the asset name is deterministic on whatever runs the suite."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    target = bin_dir / "omodel"
    target.write_text("#!/bin/sh\necho 'old binary'\n")
    target.chmod(0o755)
    monkeypatch.setattr(update, "platform_asset", lambda: ASSET)
    return update.Install("binary", str(target)), target


def _serving(tmp_path, **kwargs):
    """(_Net, release) serving a freshly built tarball + its real checksum."""
    tarball = _make_tarball(tmp_path / TARBALL, **kwargs)
    data = tarball.read_bytes()
    net = _Net({DOWNLOAD: data, SUMS: f"{_sha256(tarball)}  {TARBALL}\n"})
    release = update.Release(
        tag="v9.9.9", version="9.9.9", url="https://example.invalid/r", published_at="",
        assets={TARBALL: DOWNLOAD, f"{TARBALL}.sha256": SUMS},
    )
    return net, release


# ---------------------------------------------------------------------------
# Version maths
# ---------------------------------------------------------------------------

class TestVersions:
    @pytest.mark.parametrize(("text", "expected"), [
        ("v0.3.0", (0, 3, 0)),
        ("0.3.0", (0, 3, 0)),
        ("V1.2.3", (1, 2, 3)),
        ("0.4", (0, 4)),
        ("v0.4.0-rc1", (0, 4, 0)),
        ("0.4.0rc1", (0, 4, 0)),
        ("1.0.0+build7", (1, 0, 0)),
        ("nightly", ()),
        ("", ()),
        (None, ()),
    ])
    def test_parse(self, text, expected):
        assert update.parse_version(text) == expected

    @pytest.mark.parametrize(("candidate", "current", "expected"), [
        ("v0.3.1", "0.3.0", True),
        ("v0.4.0", "0.3.9", True),
        ("v1.0.0", "0.99.99", True),
        ("v0.3.0", "0.3.0", False),
        ("v0.2.9", "0.3.0", False),
        # Short vs long: 0.4 == 0.4.0, and 0.4.1 beats it.
        ("v0.4", "0.4.0", False),
        ("v0.4.1", "0.4", True),
        # An unparseable tag never claims an update — it would swap the binary for nothing.
        ("nightly", "0.3.0", False),
    ])
    def test_is_newer(self, candidate, current, expected):
        assert update.is_newer(candidate, current) is expected


class TestPlatformAsset:
    @pytest.mark.parametrize(("system", "machine", "expected"), [
        ("Linux", "x86_64", "omodel-linux-x64"),
        ("Linux", "amd64", "omodel-linux-x64"),
        ("Darwin", "arm64", "omodel-darwin-arm64"),
        # The two the release matrix deliberately does NOT build (DESIGN §Packaging).
        ("Darwin", "x86_64", None),
        ("Linux", "aarch64", None),
        ("Windows", "AMD64", None),
    ])
    def test_matrix(self, monkeypatch, system, machine, expected):
        monkeypatch.setattr(update.platform, "system", lambda: system)
        monkeypatch.setattr(update.platform, "machine", lambda: machine)
        assert update.platform_asset() == expected


# ---------------------------------------------------------------------------
# Install detection
# ---------------------------------------------------------------------------

class TestDetectInstall:
    def test_frozen_binary_resolves_symlinks(self, tmp_path, monkeypatch):
        real = tmp_path / "omodel"
        real.write_text("binary")
        link = tmp_path / "omodel-link"
        os.symlink(str(real), str(link))
        monkeypatch.setattr(update.sys, "frozen", True, raising=False)
        monkeypatch.setattr(update.sys, "_MEIPASS", str(tmp_path / "_MEI123"), raising=False)
        monkeypatch.setattr(update.sys, "executable", str(link))

        install = update.detect_install()
        assert install.kind == "binary"
        assert install.self_updatable
        # realpath: we replace the file the symlink points at, leaving the symlink itself alone.
        assert install.path == os.path.realpath(str(real))

    def test_frozen_without_meipass_is_not_ours(self, monkeypatch):
        """`sys.frozen` alone is a convention other freezers and embedders also set, and what we
        do with a `binary` verdict is `os.replace` over `sys.executable`. Mistaking a real
        interpreter for our binary would overwrite THAT — and the smoke test cannot catch it,
        because it validates the download, never the target. `_MEIPASS` is PyInstaller's."""
        monkeypatch.setattr(update.sys, "frozen", True, raising=False)
        monkeypatch.delattr(update.sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(update.sys, "executable", "/usr/bin/python3")
        monkeypatch.setattr(update.sys, "prefix", "/usr")
        monkeypatch.setattr(update, "_source_checkout", lambda: None)

        install = update.detect_install()
        assert install.kind == "pip"          # a printed command: the safe way to be wrong
        assert not install.self_updatable

    @pytest.mark.parametrize(("prefix", "kind"), [
        ("/home/u/.local/pipx/venvs/omodel", "pipx"),
        ("/home/u/.local/share/pipx/venvs/omodel", "pipx"),
        ("/home/u/.local/share/uv/tools/omodel", "uv"),
        # A `uvx --from git+… omodel` run (README documents it) executes from uv's cache, which
        # has no `tools` component. Classifying it `pip` printed `<uv's python> -m pip install`
        # — and uv-created environments generally have no pip, so that command cannot run.
        ("/home/u/.cache/uv/archive-v0/abc123", "uv"),
        ("/home/u/proj/.venv", "pip"),
        ("/usr", "pip"),
    ])
    def test_managed_installs(self, monkeypatch, prefix, kind):
        monkeypatch.setattr(update.sys, "frozen", False, raising=False)
        monkeypatch.setattr(update.sys, "prefix", prefix)
        # Isolate from the checkout the suite itself runs out of.
        monkeypatch.setattr(update, "_source_checkout", lambda: None)
        install = update.detect_install()
        assert install.kind == kind
        assert not install.self_updatable

    def test_source_checkout(self, monkeypatch):
        monkeypatch.setattr(update.sys, "frozen", False, raising=False)
        monkeypatch.setattr(update.sys, "prefix", "/home/u/proj/.venv")
        monkeypatch.setattr(update, "_source_checkout", lambda: "/home/u/proj/oModel")
        assert update.detect_install() == update.Install("source", "/home/u/proj/oModel")

    def test_this_suite_runs_from_a_checkout(self):
        """The repo's own tests run out of `src/` beside a .git — a sanity check that
        `_source_checkout` recognizes the real layout, not just the mocked one."""
        assert update._source_checkout() is not None

    @pytest.mark.parametrize(("kind", "fragment"), [
        ("pipx", "pipx install --force"),
        ("uv", "uv tool install --force"),
        ("pip", "-m pip install --upgrade"),
    ])
    def test_commands_pin_the_tag(self, kind, fragment):
        command = update.Install(kind).command_for("v9.9.9")
        assert fragment in command
        # Pinned, not "upgrade and hope": pipx/pip re-resolving a git spec can land on the
        # default branch, or decide the installed version already satisfies it.
        assert "@v9.9.9" in command

    def test_source_command_checks_out_the_tag(self):
        command = update.Install("source", "/tmp/repo").command_for("v9.9.9")
        assert "git -C /tmp/repo" in command and "v9.9.9" in command


# ---------------------------------------------------------------------------
# The GitHub API
# ---------------------------------------------------------------------------

class TestLatestRelease:
    def test_parses_tag_and_assets(self, monkeypatch):
        net = _Net({update.API_LATEST: json.dumps(_release_json())})
        monkeypatch.setattr(update, "_open", net.open)

        release = update.latest_release()
        assert release.tag == "v9.9.9"
        assert release.version == "9.9.9"          # what the binary's --version prints
        assert release.assets[TARBALL] == DOWNLOAD
        assert f"{TARBALL}.sha256" in release.assets

    def test_missing_tag_is_an_error(self, monkeypatch):
        net = _Net({update.API_LATEST: json.dumps({"assets": []})})
        monkeypatch.setattr(update, "_open", net.open)
        with pytest.raises(update.UpdateError) as excinfo:
            update.latest_release()
        assert excinfo.value.kind == "no_release"

    def test_garbage_body_is_an_error(self, monkeypatch):
        net = _Net({update.API_LATEST: "<html>rate limited</html>"})
        monkeypatch.setattr(update, "_open", net.open)
        with pytest.raises(update.UpdateError) as excinfo:
            update.latest_release()
        assert excinfo.value.kind == "bad_response"

    def test_http_errors_are_classified(self, monkeypatch):
        import urllib.error

        for code, kind in ((403, "rate_limited"), (429, "rate_limited"),
                           (404, "not_found"), (500, "http_error")):
            monkeypatch.setattr(update, "_opener", None)

            def _raise(request, timeout, _code=code):
                raise urllib.error.HTTPError(update.API_LATEST, _code, "no", {}, None)

            monkeypatch.setattr(update, "_opener", type("O", (), {"open": staticmethod(_raise)}))
            with pytest.raises(update.UpdateError) as excinfo:
                update.latest_release()
            assert excinfo.value.kind == kind

    def test_a_transfer_cut_short_is_an_error_not_a_traceback(self, monkeypatch):
        """`http.client.IncompleteRead` descends from HTTPException, NOT OSError — so a chunked
        body cut mid-transfer used to sail through every `except OSError` in this module, out of
        the verb's `except UpdateError`, and onto the terminal as a traceback with an empty
        `--json` stdout. Every read is guarded now."""
        import http.client

        class _Torn(io.BytesIO):
            def read(self, *args):
                raise http.client.IncompleteRead(b"{", 999)

        monkeypatch.setattr(update, "_open", lambda url, timeout: _Torn(b""))
        with pytest.raises(update.UpdateError) as excinfo:
            update.latest_release()
        assert excinfo.value.kind == "network"

    def test_non_https_urls_are_refused(self, monkeypatch):
        """`build_opener` keeps urllib's File/FTP/Data handlers, and asset URLs come straight out
        of the release JSON — so a `file:///…` there would otherwise be opened as happily as an
        https one."""
        for url in ("file:///etc/passwd", "http://api.github.com/x", "ftp://h/x"):
            with pytest.raises(update.UpdateError) as excinfo:
                update._open(url, 5)
            assert excinfo.value.kind == "bad_url"

    def test_network_failure_is_an_error(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(update, "_opener", type("O", (), {
            "open": staticmethod(lambda request, timeout: (_ for _ in ()).throw(
                urllib.error.URLError("no route to host")))
        }))
        with pytest.raises(update.UpdateError) as excinfo:
            update.latest_release()
        assert excinfo.value.kind == "network"


class TestTokenHandling:
    """A `$GITHUB_TOKEN` exists only to dodge the 60/hour unauthenticated API limit. It must
    reach api.github.com and nowhere else — asset downloads are public, and GitHub redirects
    them to objects.githubusercontent.com, where urllib would otherwise re-send the header."""

    def _capture(self, monkeypatch):
        seen = {}

        class _Opener:
            @staticmethod
            def open(request, timeout):
                seen["headers"] = dict(request.headers)
                return io.BytesIO(b"{}")

        monkeypatch.setattr(update, "_opener", _Opener)
        return seen

    def test_api_call_carries_the_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        seen = self._capture(monkeypatch)
        update._open(update.API_LATEST, 5)
        assert seen["headers"].get("Authorization") == "Bearer ghp_secret"

    def test_asset_download_never_carries_the_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        seen = self._capture(monkeypatch)
        update._open(DOWNLOAD, 5)
        assert not [k for k in seen["headers"] if k.lower() == "authorization"]

    def test_redirect_to_another_host_strips_auth(self):
        import urllib.request

        handler = update._StripAuthOnRedirect()
        request = urllib.request.Request(
            update.API_LATEST, headers={"Authorization": "Bearer ghp_secret"})
        redirected = handler.redirect_request(
            request, io.BytesIO(b""), 302, "Found", {},
            "https://objects.githubusercontent.com/thing",
        )
        assert not [k for k in redirected.headers if k.lower() == "authorization"]

    @pytest.mark.parametrize("newurl", [
        "http://api.github.com/repos/x/y",          # same host, downgraded
        "http://objects.githubusercontent.com/a",   # and cross-host
        "file:///etc/passwd",
    ])
    def test_a_non_https_redirect_is_not_followed(self, newurl):
        """`_open`'s https check only sees the FIRST url. Without this a 302 could still walk
        the update onto plain http — or onto a `file://` handler urllib's default opener carries
        — and would carry the token there in cleartext. None makes urllib raise HTTPError."""
        import urllib.request

        handler = update._StripAuthOnRedirect()
        request = urllib.request.Request(
            update.API_LATEST, headers={"Authorization": "Bearer ghp_secret"})
        assert handler.redirect_request(
            request, io.BytesIO(b""), 302, "Found", {}, newurl,
        ) is None

    def test_redirect_within_the_same_host_keeps_auth(self):
        import urllib.request

        handler = update._StripAuthOnRedirect()
        request = urllib.request.Request(
            update.API_LATEST, headers={"Authorization": "Bearer ghp_secret"})
        redirected = handler.redirect_request(
            request, io.BytesIO(b""), 302, "Found", {},
            f"{update.API_ROOT}repos/x/y/releases/44",
        )
        assert [k for k in redirected.headers if k.lower() == "authorization"]


# ---------------------------------------------------------------------------
# The swap
# ---------------------------------------------------------------------------

class TestApplyUpdate:
    def test_replaces_the_binary_and_keeps_its_mode(self, tmp_path, monkeypatch, binary_install):
        install, target = binary_install
        target.chmod(0o700)                      # a deliberately private install stays private
        net, release = _serving(tmp_path)
        monkeypatch.setattr(update, "_open", net.open)
        steps = []

        result = update.apply_update(release, install, on_step=steps.append)

        assert result.version == "9.9.9"
        assert result.verified is True
        assert result.path == str(target)
        assert "echo '9.9.9'" in target.read_text()
        assert stat.S_IMODE(target.stat().st_mode) == 0o700
        assert any("Downloading" in s for s in steps)
        assert any("Verifying" in s for s in steps)

    def test_no_temp_files_are_left_behind(self, tmp_path, monkeypatch, binary_install):
        install, target = binary_install
        net, release = _serving(tmp_path)
        monkeypatch.setattr(update, "_open", net.open)
        update.apply_update(release, install)
        assert sorted(p.name for p in target.parent.iterdir()) == ["omodel"]

    def test_checksum_mismatch_leaves_the_binary_alone(self, tmp_path, monkeypatch,
                                                      binary_install):
        install, target = binary_install
        before = target.read_bytes()
        tarball = _make_tarball(tmp_path / TARBALL)
        net = _Net({DOWNLOAD: tarball.read_bytes(), SUMS: f"{'0' * 64}  {TARBALL}\n"})
        monkeypatch.setattr(update, "_open", net.open)
        release = update.Release("v9.9.9", "9.9.9", "u", "", {
            TARBALL: DOWNLOAD, f"{TARBALL}.sha256": SUMS})

        with pytest.raises(update.UpdateError) as excinfo:
            update.apply_update(release, install)

        assert excinfo.value.kind == "checksum_mismatch"
        assert target.read_bytes() == before
        assert sorted(p.name for p in target.parent.iterdir()) == ["omodel"]

    def test_release_without_a_checksum_installs_but_says_so(self, tmp_path, monkeypatch,
                                                            binary_install):
        """`install.sh` warns and continues when a release publishes no `.sha256`; so does this.
        The verdict is reported (`verified: false`) rather than implied."""
        install, target = binary_install
        tarball = _make_tarball(tmp_path / TARBALL)
        net = _Net({DOWNLOAD: tarball.read_bytes()})
        monkeypatch.setattr(update, "_open", net.open)
        release = update.Release("v9.9.9", "9.9.9", "u", "", {TARBALL: DOWNLOAD})
        steps = []

        result = update.apply_update(release, install, on_step=steps.append)

        assert result.verified is False
        assert any("warning" in s for s in steps)
        assert "echo '9.9.9'" in target.read_text()

    def test_a_binary_that_will_not_run_is_not_installed(self, tmp_path, monkeypatch,
                                                         binary_install):
        """The glibc-too-old case, which is the documented failure of the prebuilt linux asset."""
        install, target = binary_install
        before = target.read_bytes()
        net, release = _serving(tmp_path, exit_code=1,
                                prints="omodel: /lib/libc.so.6: version GLIBC_2.39 not found")
        monkeypatch.setattr(update, "_open", net.open)

        with pytest.raises(update.UpdateError) as excinfo:
            update.apply_update(release, install)

        assert excinfo.value.kind == "smoke_failed"
        assert "GLIBC" in str(excinfo.value)
        assert target.read_bytes() == before

    def test_a_binary_reporting_the_wrong_version_is_not_installed(self, tmp_path, monkeypatch,
                                                                   binary_install):
        install, target = binary_install
        before = target.read_bytes()
        net, release = _serving(tmp_path, prints="0.1.0")
        monkeypatch.setattr(update, "_open", net.open)

        with pytest.raises(update.UpdateError) as excinfo:
            update.apply_update(release, install)

        assert excinfo.value.kind == "version_mismatch"
        assert target.read_bytes() == before

    def test_tarball_without_the_binary_is_rejected(self, tmp_path, monkeypatch, binary_install):
        install, target = binary_install
        before = target.read_bytes()
        net, release = _serving(tmp_path, member="omodel-linux-x64")   # bare-asset name, not ours
        monkeypatch.setattr(update, "_open", net.open)

        with pytest.raises(update.UpdateError) as excinfo:
            update.apply_update(release, install)

        assert excinfo.value.kind == "bad_asset"
        assert target.read_bytes() == before

    def test_a_traversal_member_is_never_written(self, tmp_path, monkeypatch, binary_install):
        """We extract ONE member to a path we choose, so `../` members have nowhere to go —
        the portable form of 3.12's `filter='data'` (the floor is 3.9)."""
        install, target = binary_install
        net, release = _serving(tmp_path, extra="../../evil.sh")
        monkeypatch.setattr(update, "_open", net.open)

        update.apply_update(release, install)

        assert not (target.parent.parent / "evil.sh").exists()
        assert not (tmp_path / "evil.sh").exists()
        assert sorted(p.name for p in target.parent.iterdir()) == ["omodel"]

    def test_a_download_cut_short_is_an_error(self, monkeypatch, binary_install):
        """The blocker's other half: a torn transfer during the ASSET download, not the API
        call. `IncompleteRead` is an HTTPException, not an OSError."""
        import http.client

        install, target = binary_install
        before = target.read_bytes()

        class _Torn(io.BytesIO):
            def read(self, *args):
                raise http.client.IncompleteRead(b"", 999)

        monkeypatch.setattr(update, "_open", lambda url, timeout: _Torn(b""))
        release = update.Release("v9.9.9", "9.9.9", "u", "", {TARBALL: DOWNLOAD})
        with pytest.raises(update.UpdateError) as excinfo:
            update.apply_update(release, install)
        assert excinfo.value.kind == "network"
        assert target.read_bytes() == before
        assert sorted(p.name for p in target.parent.iterdir()) == ["omodel"]

    @pytest.mark.parametrize(("link_type", "label"), [
        (tarfile.SYMTYPE, "symlink"),
        (tarfile.LNKTYPE, "hardlink"),
    ])
    def test_a_link_member_named_omodel_is_rejected(self, tmp_path, monkeypatch, binary_install,
                                                    link_type, label):
        """A tarball whose `omodel` is a link, not a file. `candidate.isfile()` is what rejects
        it — pinned here so a later "simplification" of the member loop can't quietly accept a
        link to /etc/passwd (or to the target itself). Parametrized so a failure names which."""
        install, target = binary_install
        before = target.read_bytes()
        path = tmp_path / TARBALL
        with tarfile.open(str(path), "w:gz") as tar:
            info = tarfile.TarInfo("omodel")
            info.type = link_type
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        net = _Net({DOWNLOAD: path.read_bytes()})
        monkeypatch.setattr(update, "_open", net.open)
        release = update.Release("v9.9.9", "9.9.9", "u", "", {TARBALL: DOWNLOAD})

        with pytest.raises(update.UpdateError) as excinfo:
            update.apply_update(release, install)

        assert excinfo.value.kind == "bad_asset", label
        assert target.read_bytes() == before

    def test_an_empty_checksum_file_does_not_verify(self, tmp_path, monkeypatch, binary_install):
        install, target = binary_install
        tarball = _make_tarball(tmp_path / TARBALL)
        net = _Net({DOWNLOAD: tarball.read_bytes(), SUMS: "\n"})
        monkeypatch.setattr(update, "_open", net.open)
        release = update.Release("v9.9.9", "9.9.9", "u", "", {
            TARBALL: DOWNLOAD, f"{TARBALL}.sha256": SUMS})
        steps = []

        result = update.apply_update(release, install, on_step=steps.append)

        assert result.verified is False          # reported, never implied
        assert any("warning" in s for s in steps)
        assert "echo '9.9.9'" in target.read_text()

    def test_preflight_refuses_before_any_download(self, monkeypatch, binary_install):
        """What `cli` calls ahead of the confirm prompt, so nobody is asked to approve a swap
        that was never possible."""
        install, _ = binary_install
        net = _Net({})                      # any fetch is an assertion failure
        monkeypatch.setattr(update, "_open", net.open)
        monkeypatch.setattr(update, "platform_asset", lambda: None)
        release = update.Release("v9.9.9", "9.9.9", "u", "", {TARBALL: DOWNLOAD})

        with pytest.raises(update.UpdateError) as excinfo:
            update.preflight(install, release)
        assert excinfo.value.kind == "unsupported_platform"
        assert net.urls == []

    def test_preflight_sees_a_missing_asset(self, binary_install):
        install, _ = binary_install
        release = update.Release("v9.9.9", "9.9.9", "u", "", {"omodel-darwin-arm64.tar.gz": "u"})
        with pytest.raises(update.UpdateError) as excinfo:
            update.preflight(install, release)
        assert excinfo.value.kind == "missing_asset"

    def test_stale_temp_dirs_are_swept_but_live_ones_survive(self, tmp_path, monkeypatch,
                                                             binary_install):
        """A run killed between mkdtemp and its cleanup leaves a dir holding a whole release
        tarball. Sweep those — but never a CONCURRENT run's, which is the other reason one
        exists; deleting its download mid-flight would be a self-inflicted failure."""
        install, target = binary_install
        stale = target.parent / ".omodel-update-old"
        stale.mkdir()
        (stale / "junk").write_text("x" * 100)
        os.utime(str(stale / "junk"), (0, 0))
        os.utime(str(stale), (0, 0))                       # ancient, inside and out
        live = target.parent / ".omodel-update-live"
        live.mkdir()
        (live / "downloading").write_text("x")             # mtime = now

        net, release = _serving(tmp_path)
        monkeypatch.setattr(update, "_open", net.open)
        update.apply_update(release, install)

        assert not stale.exists()
        assert live.exists()

    def test_a_slow_download_is_not_mistaken_for_an_abandoned_one(self, tmp_path, monkeypatch,
                                                                  binary_install):
        """The case the age gate exists for, and the one a directory's own mtime cannot see: a
        directory's mtime advances when an ENTRY is created or removed, never when a file inside
        it is written. So a work dir created two hours ago whose archive is being written RIGHT
        NOW is a live download — `DOWNLOAD_TIMEOUT` is per socket operation, not per transfer, so
        a big asset on a bad link legitimately runs for hours — and sweeping it would delete a
        concurrent run's work out from under it."""
        _, target = binary_install
        slow = target.parent / ".omodel-update-slow"
        slow.mkdir()
        archive = slow / "omodel-linux-x64.tar.gz"
        archive.write_bytes(b"partial download")
        old = time.time() - 7200
        os.utime(str(slow), (old, old))                    # dir: created two hours ago
        # archive keeps its just-now mtime — bytes are still arriving

        update._sweep_stale(str(target.parent))

        assert slow.exists() and archive.exists()

    def test_an_unreadable_temp_dir_is_left_alone(self, tmp_path, binary_install):
        """`_touched_at` reports "brand new" when it cannot look — never delete on ignorance."""
        _, target = binary_install
        assert time.time() - update._touched_at(str(target.parent / "nope")) < 1

    def test_missing_asset_for_this_platform(self, tmp_path, monkeypatch, binary_install):
        install, _ = binary_install
        release = update.Release("v9.9.9", "9.9.9", "u", "", {"omodel-darwin-arm64.tar.gz": "u"})
        with pytest.raises(update.UpdateError) as excinfo:
            update.apply_update(release, install)
        assert excinfo.value.kind == "missing_asset"

    def test_unsupported_platform(self, monkeypatch, binary_install):
        install, _ = binary_install
        monkeypatch.setattr(update, "platform_asset", lambda: None)
        release = update.Release("v9.9.9", "9.9.9", "u", "", {TARBALL: DOWNLOAD})
        with pytest.raises(update.UpdateError) as excinfo:
            update.apply_update(release, install)
        assert excinfo.value.kind == "unsupported_platform"

    def test_a_non_binary_install_is_never_swapped(self, tmp_path):
        release = update.Release("v9.9.9", "9.9.9", "u", "", {TARBALL: DOWNLOAD})
        with pytest.raises(update.UpdateError) as excinfo:
            update.apply_update(release, update.Install("pipx"))
        assert excinfo.value.kind == "not_self_updatable"

    def test_the_smoke_test_runs_in_a_clean_environment(self, monkeypatch):
        """The parent here is a frozen binary and the child is a *different* frozen binary —
        the one case PyInstaller's env breaks. Inheriting `LD_LIBRARY_PATH` (pointed at THIS
        build's unpacked libs) or `_MEIPASS2` (pointed at this build's archive) would fail the
        smoke test on a perfectly good release, and a failed smoke test refuses to install."""
        monkeypatch.setattr(update.sys, "frozen", True, raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI-old/lib")
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/mine/lib")
        monkeypatch.setenv("DYLD_LIBRARY_PATH", "/tmp/_MEI-old/lib")   # no _ORIG → invented
        monkeypatch.setenv("_MEIPASS2", "/tmp/_MEI-old")
        monkeypatch.setenv("_PYI_ARCHIVE_FILE", "/usr/bin/omodel")
        monkeypatch.setenv("PATH", "/usr/bin")                          # untouched passthrough

        env = update._child_env()

        assert env["LD_LIBRARY_PATH"] == "/opt/mine/lib"    # the user's own value, restored
        assert "LD_LIBRARY_PATH_ORIG" not in env
        assert "DYLD_LIBRARY_PATH" not in env               # the bootloader's, dropped
        assert "_MEIPASS2" not in env and "_PYI_ARCHIVE_FILE" not in env
        assert env["PATH"] == "/usr/bin"

    def test_child_env_leaves_a_normal_run_alone(self, monkeypatch):
        """Not frozen (a dev checkout, or the test suite itself): nothing to undo, so
        `LD_LIBRARY_PATH` is the user's and stays."""
        monkeypatch.setattr(update.sys, "frozen", False, raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/mine/lib")
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
        assert update._child_env()["LD_LIBRARY_PATH"] == "/opt/mine/lib"

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                        reason="root bypasses the directory permission check")
    def test_unwritable_install_dir_refuses_before_downloading(self, tmp_path, monkeypatch,
                                                               binary_install):
        install, target = binary_install
        net = _Net({})                      # any fetch at all is an assertion failure
        monkeypatch.setattr(update, "_open", net.open)
        target.parent.chmod(0o500)
        try:
            release = update.Release("v9.9.9", "9.9.9", "u", "", {TARBALL: DOWNLOAD})
            with pytest.raises(update.UpdateError) as excinfo:
                update.apply_update(release, install)
        finally:
            target.parent.chmod(0o700)
        assert excinfo.value.kind == "not_writable"
        assert net.urls == []               # refused before spending a byte of bandwidth


# ---------------------------------------------------------------------------
# The CLI verb
# ---------------------------------------------------------------------------

def _run(capsys, argv):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, out


@pytest.fixture
def serving_update(tmp_path, monkeypatch, binary_install):
    """The whole happy path staged: a binary install, and a network serving v9.9.9."""
    install, target = binary_install
    tarball = _make_tarball(tmp_path / TARBALL)
    net = _Net({
        update.API_LATEST: json.dumps(_release_json()),
        DOWNLOAD: tarball.read_bytes(),
        SUMS: f"{_sha256(tarball)}  {TARBALL}\n",
    })
    monkeypatch.setattr(update, "_open", net.open)
    monkeypatch.setattr(update, "detect_install", lambda: install)
    return target


def _tty(monkeypatch, answer):
    """Make stdin look interactive and have it answer the confirm prompt."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)


class TestUpdateFlag:
    def test_up_to_date(self, capsys, monkeypatch):
        import omodel
        net = _Net({update.API_LATEST: json.dumps(_release_json(tag=f"v{omodel.__version__}"))})
        monkeypatch.setattr(update, "_open", net.open)

        code, out = _run(capsys, ["--update", "--json"])
        payload = json.loads(out)
        assert code == cli.EXIT_OK
        assert payload["update_available"] is False
        assert payload["changed"] is False
        assert payload["ok"] is True and payload["schema"] == cli.SCHEMA

    def test_json_reports_without_installing(self, capsys, monkeypatch, serving_update):
        """`--update --json` IS the check: a caller reading a payload can't answer a prompt, so
        it reports and stops. This is why there is no separate `--update-check`."""
        monkeypatch.setattr(update, "apply_update", _never_called)

        code, out = _run(capsys, ["--update", "--json"])
        payload = json.loads(out)
        assert code == cli.EXIT_OK
        assert payload["update_available"] is True
        assert payload["latest"] == "9.9.9" and payload["tag"] == "v9.9.9"
        assert payload["changed"] is False and payload["confirmed"] is False
        assert "old binary" in serving_update.read_text()

    def test_no_tty_reports_and_says_how(self, capsys, monkeypatch, serving_update):
        """Piped, redirected or in CI: `input()` would only raise, so say what's there and how
        to take it."""
        monkeypatch.setattr(update, "apply_update", _never_called)
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)

        code, out = _run(capsys, ["--update"])
        assert code == cli.EXIT_OK
        assert "9.9.9 is available" in out
        assert "--update --yes" in out
        assert "old binary" in serving_update.read_text()

    def test_prompt_declined_installs_nothing(self, capsys, monkeypatch, serving_update):
        monkeypatch.setattr(update, "apply_update", _never_called)
        _tty(monkeypatch, "n")

        code, out = _run(capsys, ["--update"])
        assert code == cli.EXIT_OK          # declining is an answer, not a failure
        assert "Cancelled." in out
        assert "old binary" in serving_update.read_text()

    @pytest.mark.parametrize("answer", ["", "no", "N", "later", "Y ES"])
    def test_only_yes_means_yes(self, capsys, monkeypatch, serving_update, answer):
        monkeypatch.setattr(update, "apply_update", _never_called)
        _tty(monkeypatch, answer)
        code, _ = _run(capsys, ["--update"])
        assert code == cli.EXIT_OK
        assert "old binary" in serving_update.read_text()

    @pytest.mark.parametrize("answer", ["y", "Y", "yes", " YES "])
    def test_confirmed_installs(self, capsys, monkeypatch, serving_update, answer):
        _tty(monkeypatch, answer)
        code, out = _run(capsys, ["--update"])
        assert code == cli.EXIT_OK
        assert "Updated omodel" in out
        assert "echo '9.9.9'" in serving_update.read_text()

    def test_ctrl_c_at_the_prompt_is_a_no(self, capsys, monkeypatch, serving_update):
        monkeypatch.setattr(update, "apply_update", _never_called)
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)

        def _interrupt(prompt=""):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", _interrupt)
        code, out = _run(capsys, ["--update"])
        assert code == cli.EXIT_OK
        assert "Cancelled." in out
        assert "old binary" in serving_update.read_text()

    def test_yes_skips_the_prompt(self, capsys, monkeypatch, serving_update):
        def _no_prompt(prompt=""):
            raise AssertionError("--yes must not prompt")

        monkeypatch.setattr("builtins.input", _no_prompt)
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)

        code, out = _run(capsys, ["--update", "--yes"])
        assert code == cli.EXIT_OK
        assert "Downloading" in out and "Updated omodel" in out
        assert "echo '9.9.9'" in serving_update.read_text()

    def test_managed_install_refuses_with_the_command(self, capsys, monkeypatch):
        net = _Net({update.API_LATEST: json.dumps(_release_json())})
        monkeypatch.setattr(update, "_open", net.open)
        monkeypatch.setattr(update, "detect_install", lambda: update.Install("pipx"))
        monkeypatch.setattr(update, "apply_update", _never_called)

        code, out = _run(capsys, ["--update", "--json"])
        payload = json.loads(out)
        # Exit 3, not 1: nothing is broken — the caller just has to run something else. And it
        # refuses BEFORE the prompt: there is nothing to confirm if we can't do it either way.
        assert code == cli.EXIT_REJECTED
        assert payload["ok"] is False
        assert payload["error"] == "not_self_updatable"
        assert "pipx install --force" in payload["command"] and "@v9.9.9" in payload["command"]

    def test_network_failure_is_exit_1(self, capsys, monkeypatch):
        def _boom(url, timeout):
            raise update.UpdateError("network", "could not reach api.github.com")

        monkeypatch.setattr(update, "_open", _boom)
        code, out = _run(capsys, ["--update", "--json"])
        payload = json.loads(out)
        # Exit 1: omodel failed. A script branches on 1-vs-3 exactly as elsewhere.
        assert code == cli.EXIT_ERROR
        assert payload["error"] == "network"

    def test_an_unforeseen_exception_still_emits_json(self, capsys, monkeypatch, serving_update):
        """The backstop. `--json` promises ONE object on stdout; an exception type nobody
        anticipated must not turn that into a traceback and an empty stdout (which is exactly
        what `http.client.IncompleteRead` did before it was caught in update.py)."""
        def _boom(*args, **kwargs):
            raise RuntimeError("something nobody predicted")

        monkeypatch.setattr(update, "apply_update", _boom)
        code = cli.main(["--update", "--yes", "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert code == cli.EXIT_ERROR
        assert payload["ok"] is False
        assert payload["error"] == "failed"
        assert "RuntimeError" in payload["message"]      # a bug still looks like a bug
        # ...and the traceback goes to STDERR, so the object on stdout stays parseable while
        # whoever has to fix it still gets a file and a line number.
        assert "Traceback" in captured.err
        assert "RuntimeError" in captured.err

    def test_the_backstop_traceback_never_touches_stdout(self, capsys, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("nope")

        monkeypatch.setattr(update, "latest_release", _boom)
        code = cli.main(["--update", "--json"])
        captured = capsys.readouterr()
        assert code == cli.EXIT_ERROR
        json.loads(captured.out)                          # still exactly one object
        assert "Traceback" in captured.err

    def test_impossible_updates_refuse_before_the_prompt(self, capsys, monkeypatch,
                                                         serving_update):
        """Nobody should be asked to confirm a swap that could never have happened."""
        def _no_prompt(prompt=""):
            raise AssertionError("must not ask about an impossible update")

        monkeypatch.setattr("builtins.input", _no_prompt)
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(update, "platform_asset", lambda: None)
        monkeypatch.setattr(update, "apply_update", _never_called)

        code, _ = _run(capsys, ["--update"])
        assert code == cli.EXIT_REJECTED
        assert "old binary" in serving_update.read_text()

    BASE_KEYS = ("current", "latest", "tag", "update_available", "install", "path", "url",
                 "changed", "confirmed", "ok", "schema")

    def test_payload_shape_is_stable(self, capsys, monkeypatch, serving_update):
        """The same keys whether or not anything happened, so a consumer needn't work out which
        branch produced the payload before reading it."""
        monkeypatch.setattr(update, "apply_update", _never_called)
        _, out = _run(capsys, ["--update", "--json"])
        payload = json.loads(out)
        for key in self.BASE_KEYS:
            assert key in payload, key
        assert payload["confirmed"] is False and payload["changed"] is False

    def test_end_to_end_swap(self, capsys, monkeypatch, serving_update):
        code, out = _run(capsys, ["--update", "--yes", "--json"])
        payload = json.loads(out)
        assert code == cli.EXIT_OK
        for key in self.BASE_KEYS:
            assert key in payload, key       # the install path carries the same shape
        assert payload["confirmed"] is True
        assert payload["changed"] is True
        assert payload["installed"] == "9.9.9"
        assert payload["verified"] is True
        assert payload["path"] == str(serving_update)
        assert "echo '9.9.9'" in serving_update.read_text()

    def test_json_mode_prints_exactly_one_object(self, capsys, monkeypatch, serving_update):
        """Progress lines are prose-only — `--json` must stay parseable by `json.loads(out)`."""
        _, out = _run(capsys, ["--update", "--yes", "--json"])
        json.loads(out)                      # raises if a step line leaked onto stdout

    def test_prose_mode_narrates(self, capsys, monkeypatch, serving_update):
        code, out = _run(capsys, ["--update", "--yes"])
        assert code == cli.EXIT_OK
        assert "Downloading" in out and "Updated omodel" in out and "9.9.9" in out

    def test_force_reinstalls_the_same_version(self, tmp_path, capsys, monkeypatch,
                                               binary_install):
        import omodel
        install, target = binary_install
        version = omodel.__version__
        tarball = _make_tarball(tmp_path / TARBALL, version=version)
        net = _Net({
            update.API_LATEST: json.dumps(_release_json(tag=f"v{version}")),
            DOWNLOAD: tarball.read_bytes(),
            SUMS: f"{_sha256(tarball)}  {TARBALL}\n",
        })
        monkeypatch.setattr(update, "_open", net.open)
        monkeypatch.setattr(update, "detect_install", lambda: install)

        code, out = _run(capsys, ["--update", "--force", "--yes", "--json"])
        assert code == cli.EXIT_OK
        assert json.loads(out)["changed"] is True
        assert f"echo '{version}'" in target.read_text()

    def test_force_still_asks(self, tmp_path, capsys, monkeypatch, binary_install):
        """`--force` changes what counts as an update, not whether you agreed to it."""
        import omodel
        install, target = binary_install
        tarball = _make_tarball(tmp_path / TARBALL, version=omodel.__version__)
        net = _Net({
            update.API_LATEST: json.dumps(_release_json(tag=f"v{omodel.__version__}")),
            DOWNLOAD: tarball.read_bytes(),
            SUMS: f"{_sha256(tarball)}  {TARBALL}\n",
        })
        monkeypatch.setattr(update, "_open", net.open)
        monkeypatch.setattr(update, "detect_install", lambda: install)
        monkeypatch.setattr(update, "apply_update", _never_called)
        _tty(monkeypatch, "n")

        code, out = _run(capsys, ["--update", "--force"])
        assert code == cli.EXIT_OK and "Cancelled." in out
        assert "old binary" in target.read_text()


def _never_called(*args, **kwargs):
    raise AssertionError("apply_update must not run here")


class TestUpdateIsNotAnAgentVerb:
    """`--update` is a FLAG, not a subcommand, because the subcommands are the agent surface —
    and an agent replacing the binary it is running mid-task is not a model change."""

    def test_it_is_not_a_subcommand(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["update"])
        assert excinfo.value.code == cli.EXIT_USAGE

    def test_agent_guide_does_not_advertise_it(self, capsys):
        cli.main(["agent-guide"])
        assert "--update" not in capsys.readouterr().out

    def test_but_it_is_in_the_top_level_help(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["--help"])
        assert "--update" in capsys.readouterr().out


class TestFlagsAreNotSilentlyIgnored:
    """Moving `--update`'s modifiers onto the MAIN parser bought a cost: argparse accepts every
    top-level flag on every run, so combinations that used to exit 2 on an unrecognized argument
    started parsing cleanly and doing something else. Each case below previously exited 2 and
    must again — being told is strictly better than being ignored.

    Scope is only the flags this change ADDED: `omodel --check show` was accepted-and-ignored
    long before it, and quietly tightening that would be a behaviour change hidden in a fix."""

    @pytest.mark.parametrize("argv", [
        ["--update", "show"],            # ran `show`, ignored the update entirely
        ["--update", "--print"],
        ["--update", "--check"],         # the two different "checks"
        ["--json", "--print"],           # printed prose to a caller awaiting JSON
        ["--json", "--check"],
        ["--json"],
        ["--yes", "show"],
        ["--yes"],
        ["--force", "clear", "cat:quick"],
        ["--force"],
        # `omodel agent-guide --json` exits 2 (it prints a document and never took --json), so
        # the pre-verb spelling must too rather than being the one silently-ignored survivor.
        ["--json", "agent-guide"],
    ])
    def test_ignored_combinations_exit_2(self, capsys, argv):
        assert cli.main(argv) == cli.EXIT_USAGE
        assert "usage error" in capsys.readouterr().err

    def test_the_check_collision_says_which_check_it_means(self, capsys):
        cli.main(["--update", "--check"])
        err = capsys.readouterr().err
        assert "--update --json" in err          # how to check without installing

    @pytest.mark.parametrize("argv", [
        ["--version"],                   # short-circuits before the guard, as it always has
        ["--update", "--yes", "--json"],
        ["--update", "--force"],
    ])
    def test_legitimate_combinations_are_untouched(self, monkeypatch, capsys, argv):
        monkeypatch.setattr(update, "_open",
                            lambda url, timeout: (_ for _ in ()).throw(
                                update.UpdateError("network", "stubbed")))
        assert cli.main(argv) != cli.EXIT_USAGE

    def test_json_before_a_subcommand_still_works(self, tmp_path, capsys, monkeypatch):
        """The exact case the `--json` branch of the guard could have broken. `--config`'s rule
        made both orders work; that must survive a guard aimed at a different flag."""
        from unittest.mock import MagicMock, patch

        config = tmp_path / "c.jsonc"
        config.write_text('{"agents": {}, "categories": {}}\n')
        result = MagicMock(returncode=0, stdout="opencode/glm-5\n", stderr="")
        with patch("subprocess.run", return_value=result):
            code = cli.main(["--json", "show", "--config", str(config)])
        assert code == cli.EXIT_OK
        assert json.loads(capsys.readouterr().out)["schema"] == cli.SCHEMA


class TestForceStillReachesTheSubcommands:
    """The main parser owning a `--force` (for `--update`) must not swallow the subcommands'
    own. Without `default=SUPPRESS` on theirs, `omodel --force set …` would parse, ignore the
    flag, and then refuse the write for the very reason the caller was overriding.

    `subprocess.run` is stubbed as everywhere else — a real `opencode` is on PATH and costs
    ~3s / ~320 MB a call (CONTRACTS §hard rules)."""

    CONFIG = '{"agents": {}, "categories": {}}\n'

    def _set(self, tmp_path, capsys, argv):
        from unittest.mock import MagicMock, patch

        config = tmp_path / "c.jsonc"
        config.write_text(self.CONFIG)
        result = MagicMock(returncode=0, stdout="opencode/glm-5\nzhipuai/glm-5\n", stderr="")
        with patch("subprocess.run", return_value=result):
            code = cli.main([*argv, "--config", str(config), "--json"])
        return code, json.loads(capsys.readouterr().out)

    def test_unforced_still_refuses(self, tmp_path, capsys):
        """The baseline the two below are measured against: nothing serves `nope/nope`."""
        code, payload = self._set(tmp_path, capsys, ["set", "agent:sisyphus", "nope/nope"])
        assert code == cli.EXIT_REJECTED and payload["error"] == "unavailable"

    @pytest.mark.parametrize("argv", [
        ["set", "agent:sisyphus", "nope/nope", "--force"],
        ["--force", "set", "agent:sisyphus", "nope/nope"],
    ])
    def test_force_survives_either_order(self, tmp_path, capsys, argv):
        code, payload = self._set(tmp_path, capsys, argv)
        assert code == cli.EXIT_OK
        assert payload["ok"] is True and payload["to"] == "nope/nope"
        assert payload["warn"] == ["unavailable"]
