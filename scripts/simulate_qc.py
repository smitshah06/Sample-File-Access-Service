"""Development helper: play the QC pipeline and report a result for one file.

Usage:  python scripts/simulate_qc.py <file_id> passed|failed

Reads QC_CALLBACK_TOKEN from the environment and calls the internal callback, exactly as the
real pipeline would. Uses only the standard library.
"""

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[2] not in {"passed", "failed"}:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    file_id, status = sys.argv[1], sys.argv[2]
    token = os.environ.get("QC_CALLBACK_TOKEN")
    if not token:
        print("QC_CALLBACK_TOKEN is not set", file=sys.stderr)
        return 2

    base = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
    request = urllib.request.Request(
        f"{base}/api/internal/files/{file_id}/qc-status",
        data=json.dumps({"status": status}).encode(),
        method="PUT",
        headers={"Content-Type": "application/json", "X-Pipeline-Token": token},
    )
    try:
        with urllib.request.urlopen(request) as response:
            print(response.status, response.read().decode())
            return 0
    except urllib.error.HTTPError as err:
        print(err.code, err.read().decode(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
