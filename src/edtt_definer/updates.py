from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__


LATEST_RELEASE_API = (
    "https://api.github.com/repos/LouisH99/EDTT-Definer/releases/latest"
)
REPOSITORY_RELEASE_PATH = "/LouisH99/EDTT-Definer/releases/"
_VERSION_PATTERN = re.compile(r"^[vV]?(\d+)\.(\d+)(?:\.(\d+))?$")
_INSTALLED_VERSION_PATTERN = re.compile(
    r"^[vV]?(\d+)\.(\d+)(?:\.(\d+))?(?:\.dev(\d+))?$"
)


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: tuple[int, int, int]
    html_url: str
    title: str

    @property
    def normalized_version(self) -> str:
        return ".".join(str(part) for part in self.version)


def parse_version_tag(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def is_trusted_release_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return False
    return (
        url.scheme == "https"
        and (url.hostname or "").casefold() == "github.com"
        and port is None
        and url.username is None
        and url.password is None
        and url.path.casefold().startswith(REPOSITORY_RELEASE_PATH.casefold())
        and not url.query
        and not url.fragment
    )


def parse_release_payload(payload: bytes | str) -> ReleaseInfo:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError("GitHub returned invalid release data.") from exc
    if not isinstance(data, dict):
        raise ValueError("GitHub returned an unexpected release response.")
    tag_name = data.get("tag_name")
    html_url = data.get("html_url")
    if not isinstance(tag_name, str) or not isinstance(html_url, str):
        raise ValueError("GitHub release data is missing a tag or release page.")
    version = parse_version_tag(tag_name)
    if version is None:
        raise ValueError(f"The latest release has an unsupported version tag: {tag_name}")
    if not is_trusted_release_url(html_url):
        raise ValueError("GitHub returned an untrusted release-page address.")
    title = data.get("name")
    return ReleaseInfo(
        tag_name=tag_name.strip(),
        version=version,
        html_url=html_url,
        title=title.strip() if isinstance(title, str) and title.strip() else tag_name.strip(),
    )


def is_newer_release(release: ReleaseInfo, current_version: str = __version__) -> bool:
    match = _INSTALLED_VERSION_PATTERN.fullmatch(current_version.strip())
    if match is None:
        return False
    major, minor, patch, development = match.groups()
    installed = (
        int(major),
        int(minor),
        int(patch or 0),
        -1 if development is not None else 0,
    )
    return (*release.version, 0) > installed


class GitHubUpdateClient(QObject):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None

    @property
    def checking(self) -> bool:
        return self._reply is not None

    def check(self) -> bool:
        if self._reply is not None:
            return False
        request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
        request.setHeader(
            QNetworkRequest.KnownHeaders.UserAgentHeader,
            f"EDTT-Definer/{__version__}",
        )
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"X-GitHub-Api-Version", b"2022-11-28")
        request.setTransferTimeout(15000)
        reply = self.manager.get(request)
        self._reply = reply
        reply.finished.connect(lambda reply=reply: self._finished(reply))
        return True

    def _finished(self, reply: QNetworkReply) -> None:
        self._reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                remaining = bytes(reply.rawHeader("X-RateLimit-Remaining"))
                if status == 403 and remaining == b"0":
                    self.failed.emit("GitHub's update-check rate limit has been reached. Try again later.")
                else:
                    self.failed.emit(f"Update check failed: {reply.errorString()}")
                return
            try:
                release = parse_release_payload(bytes(reply.readAll()))
            except ValueError as exc:
                self.failed.emit(str(exc))
                return
            self.succeeded.emit(release)
        finally:
            reply.deleteLater()
