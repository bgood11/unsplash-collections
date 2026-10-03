"""Hourly runner: create Unsplash collections and add photos from plan.json.

Spends at most BUDGET API calls per run, stops early when the rate limit is
nearly exhausted, and records every completed step in state.json so the next
run carries on where this one stopped. Safe to re-run at any time.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.unsplash.com"
DRY_RUN = os.environ.get("DRY_RUN") == "1"
TOKEN = "dry" if DRY_RUN else os.environ["UNSPLASH_TOKEN"]
BUDGET = int(os.environ.get("BUDGET", "45"))
HERE = os.path.dirname(os.path.abspath(__file__))
PLAN = os.path.join(HERE, "plan.json")
STATE = os.environ.get("STATE_FILE", os.path.join(HERE, "state.json"))


class RateLimited(Exception):
    pass


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"collections": {}, "added": {}, "skipped": {}}


def save_state(state):
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def post(path, fields):
    if DRY_RUN:
        return 201, {"id": "dry-" + fields.get("title", "x")[:12]}, 99
    req = urllib.request.Request(
        API + path, data=urllib.parse.urlencode(fields).encode(), method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Accept-Version": "v1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            remaining = int(r.headers.get("X-Ratelimit-Remaining", "99"))
            return r.status, json.load(r), remaining
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code == 403 and "Rate Limit" in body:
            raise RateLimited()
        remaining = int(e.headers.get("X-Ratelimit-Remaining", "99") or 99)
        return e.code, body, remaining


def main():
    with open(PLAN) as f:
        plan = json.load(f)
    state = load_state()
    calls = 0

    try:
        for col in plan["collections"]:
            key = col["key"]
            if calls >= BUDGET:
                break
            if key not in state["collections"]:
                status, body, remaining = post("/collections", {
                    "title": col["title"], "description": col.get("description", ""),
                    "private": "true" if col.get("private") else "false"})
                calls += 1
                if status not in (200, 201):
                    print(f"create {key} failed: HTTP {status} {str(body)[:200]}")
                    break
                state["collections"][key] = body["id"]
                save_state(state)
                print(f"created collection {col['title']!r} -> {body['id']}")
                if remaining < 2:
                    break

            coll_id = state["collections"][key]
            done = set(state["added"].get(key, [])) | set(state["skipped"].get(key, []))
            for pid in col["photos"]:
                if pid in done:
                    continue
                if calls >= BUDGET:
                    break
                status, body, remaining = post(f"/collections/{coll_id}/add", {"photo_id": pid})
                calls += 1
                if status in (200, 201):
                    state["added"].setdefault(key, []).append(pid)
                elif status in (404, 422):
                    # photo gone, hidden, or already in the collection: never retry
                    state["skipped"].setdefault(key, []).append(pid)
                    print(f"skip {pid} in {key}: HTTP {status}")
                else:
                    print(f"add {pid} to {key} failed: HTTP {status}; will retry next run")
                save_state(state)
                if remaining < 2:
                    raise RateLimited()
    except RateLimited:
        print("rate limit reached, stopping this run")

    total = sum(len(c["photos"]) for c in plan["collections"])
    finished = sum(len(v) for v in state["added"].values()) + sum(len(v) for v in state["skipped"].values())
    print(f"calls this run: {calls} | progress: {finished}/{total} placements "
          f"| collections: {len(state['collections'])}/{len(plan['collections'])}")
    if finished >= total and len(state["collections"]) == len(plan["collections"]):
        print("PLAN COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
