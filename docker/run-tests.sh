#!/bin/sh
set -eu

ARTIFACT_DIR="${ARTIFACT_DIR:-/artifacts}"
TEST_SCOPE="${TEST_SCOPE:-smoke}"

mkdir -p "$ARTIFACT_DIR"

flask --app api.mock_server:app run --host=0.0.0.0 --port=5000 >/tmp/mock_server.log 2>&1 &
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

python - <<'PY'
import sys
import time
import urllib.request

url = "http://127.0.0.1:5000/health"

for _ in range(40):
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            if resp.status == 200:
                sys.exit(0)
    except Exception:
        time.sleep(1)

print("mock server not ready", file=sys.stderr)
sys.exit(1)
PY

TEST_FILES="tests/test_smoke_flow.py"
if [ "$TEST_SCOPE" = "full" ]; then
  TEST_FILES="tests/test_smoke_flow.py tests/test_stress_flow.py tests/test_chaos_flow.py"
fi

set +e
sh -c "pytest $TEST_FILES -v --html=$ARTIFACT_DIR/report.html --self-contained-html"
TEST_EXIT=$?
set -e

cp /tmp/mock_server.log "$ARTIFACT_DIR/mock_server.log" 2>/dev/null || true
[ -d reports ] && cp -r reports "$ARTIFACT_DIR/reports" || true

exit "$TEST_EXIT"