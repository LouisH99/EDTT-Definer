from __future__ import annotations

import json
from decimal import Decimal

from PySide6.QtCore import QObject, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__
from .radius import normalize_name, positive_decimal


EDSM_BODIES_URL = "https://www.edsm.net/api-system-v1/bodies"


def parse_edsm_body_radius(payload: bytes | str, body_name: str) -> Decimal:
    try:
        data = json.loads(payload, parse_float=Decimal)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError("EDSM returned invalid JSON.") from exc
    bodies = data.get("bodies") if isinstance(data, dict) else None
    if not isinstance(bodies, list):
        raise ValueError("EDSM did not return a body list for this system.")
    wanted = normalize_name(body_name)
    for body in bodies:
        if not isinstance(body, dict) or normalize_name(str(body.get("name", ""))) != wanted:
            continue
        radius = positive_decimal(body.get("radius"))
        if radius is None:
            raise ValueError(f"EDSM has no usable radius for {body_name}.")
        # The EDSM bodies endpoint reports planetary radius in kilometres.
        return radius
    raise ValueError(f"EDSM could not find {body_name} in the selected system.")


class EdsmRadiusClient(QObject):
    succeeded = Signal(str, str, str)
    failed = Signal(str, str, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self._pending: set[tuple[str, str]] = set()

    def lookup(self, system: str, body: str) -> bool:
        key = (normalize_name(system), normalize_name(body))
        if not all(key) or key in self._pending:
            return False
        self._pending.add(key)
        url = QUrl(EDSM_BODIES_URL)
        query = QUrlQuery()
        query.addQueryItem("systemName", system)
        url.setQuery(query)
        request = QNetworkRequest(url)
        request.setHeader(
            QNetworkRequest.KnownHeaders.UserAgentHeader,
            f"EDTT-Definer/{__version__}",
        )
        request.setTransferTimeout(15000)
        reply = self.manager.get(request)
        reply.finished.connect(
            lambda reply=reply, system=system, body=body, key=key: self._finished(
                reply, system, body, key
            )
        )
        return True

    def _finished(
        self,
        reply: QNetworkReply,
        system: str,
        body: str,
        key: tuple[str, str],
    ) -> None:
        self._pending.discard(key)
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.failed.emit(system, body, f"EDSM lookup failed: {reply.errorString()}")
                return
            try:
                radius = parse_edsm_body_radius(bytes(reply.readAll()), body)
            except ValueError as exc:
                self.failed.emit(system, body, str(exc))
                return
            self.succeeded.emit(system, body, format(radius, "f"))
        finally:
            reply.deleteLater()
