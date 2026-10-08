"""Usage:
  python soko.py agents                      # public, no key needed
  python soko.py schema <agent_id>
  python soko.py hire <agent_id> key=value [key=value ...] [--max-credits N]
  python soko.py job <job_id>
  python soko.py wait <job_id>
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("SOKOSUMI_BASE_URL", "https://api.sokosumi.com/v1")
TERMINAL = {"completed", "failed", "input_required", "payment_failed", "refund_resolved", "dispute_resolved"}


def load_dotenv():
    here = Path(__file__).resolve().parent
    for env in (here / ".env", here.parent / ".env"):
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def request(method, path, body=None, auth=True):
    headers = {"Accept": "application/json"}
    if auth:
        key = os.environ.get("SOKOSUMI_API_KEY")
        if not key:
            sys.exit("SOKOSUMI_API_KEY is not set (put it in .env)")
        headers["Authorization"] = f"Bearer {key}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:1000]}")


def cmd_agents():
    cursor = None
    while True:
        qs = f"?cursor={cursor}" if cursor else ""
        res = request("GET", f"/agents{qs}", auth=bool(os.environ.get("SOKOSUMI_API_KEY")))
        for a in res["data"]:
            print(f"{a['id']}  {a['credits']:>5} cr  {a['name']}  -  {(a.get('summary') or '')[:70]}")
        cursor = res["meta"]["pagination"].get("nextCursor")
        if not cursor:
            break


def get_schema(agent_id):
    return request("GET", f"/agents/{agent_id}/input-schema")["data"]


def cmd_schema(agent_id):
    print(json.dumps(get_schema(agent_id), indent=2))


def cmd_hire(agent_id, args):
    max_credits = None
    inputs = {}
    it = iter(args)
    for a in it:
        if a == "--max-credits":
            max_credits = float(next(it))
        else:
            k, v = a.split("=", 1)
            inputs[k] = v
    body = {"inputSchema": get_schema(agent_id), "inputData": inputs}
    if max_credits:
        body["maxCredits"] = max_credits
    job = request("POST", f"/agents/{agent_id}/jobs", body)["data"]
    print(f"job {job['id']} status={job['status']} credits={job['credits']}")
    return job["id"]


def cmd_job(job_id):
    job = request("GET", f"/jobs/{job_id}")["data"]
    print(json.dumps({k: job.get(k) for k in ("id", "status", "credits", "result", "completedAt")}, indent=2))
    return job


def cmd_wait(job_id, interval=10):
    start = time.time()
    while True:
        job = request("GET", f"/jobs/{job_id}")["data"]
        print(f"[{int(time.time() - start):>4}s] {job['status']}")
        if job["status"] in TERMINAL:
            print(job.get("result") or "")
            return job
        time.sleep(interval)


def main():
    load_dotenv()
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "agents":
        cmd_agents()
    elif cmd == "schema":
        cmd_schema(rest[0])
    elif cmd == "hire":
        job_id = cmd_hire(rest[0], rest[1:])
        cmd_wait(job_id)
    elif cmd == "job":
        cmd_job(rest[0])
    elif cmd == "wait":
        cmd_wait(rest[0])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
