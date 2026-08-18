"""Single-request worker used to enforce provider-call deadlines on Windows."""

from __future__ import annotations

import base64
import json
import sys
from urllib import request as urlrequest

from detector_v2.provider import _DIRECT_OPENER


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    request = urlrequest.Request(
        str(payload["url"]),
        data=base64.b64decode(str(payload["body_b64"]), validate=True),
        headers=dict(payload["headers"]),
        method="POST",
    )
    with _DIRECT_OPENER.open(request, timeout=float(payload["timeout"])) as response:
        result = {
            "status": int(getattr(response, "status", 0) or 0),
            "body_b64": base64.b64encode(response.read()).decode("ascii"),
        }
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
