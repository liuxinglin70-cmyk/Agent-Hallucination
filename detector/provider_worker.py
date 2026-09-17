"""Single-request worker used to enforce provider-call deadlines on Windows.

An error status is a valid provider answer, not a transport crash: it is
returned in the same envelope so the parent can report ``http <status>`` and
decide whether to retry. Only genuine connection faults exit non-zero.
"""

from __future__ import annotations

import base64
import json
import sys
from urllib import error as urlerror
from urllib import request as urlrequest

from detector.provider import _DIRECT_OPENER


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    request = urlrequest.Request(
        str(payload["url"]),
        data=base64.b64decode(str(payload["body_b64"]), validate=True),
        headers=dict(payload["headers"]),
        method="POST",
    )
    try:
        with _DIRECT_OPENER.open(
            request, timeout=float(payload["timeout"])
        ) as response:
            status = int(getattr(response, "status", 0) or 0)
            body = response.read()
    except urlerror.HTTPError as exc:
        # The body carries the provider's own error message; keep it.
        status = int(exc.code)
        body = exc.read()
    result = {
        "status": status,
        "body_b64": base64.b64encode(body).decode("ascii"),
    }
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
