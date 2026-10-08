"""Sweep the profile for photos uploaded since the last sweep and add them to plan.json.

Runs before runner.py in every batch of the workflow. Cost when nothing is new: one
Unsplash API call. For each new photo it makes one more call (photo detail, for EXIF),
then classifies the batch with Claude Haiku and appends placements to plan.json.
runner.py then does the actual adding, within whatever API budget is left.

Photos that cannot be processed this time (rate limit, classifier unreachable) are left
out of known_ids.json, so the next sweep retries them. Nothing is ever lost.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("SWEEP_ROOT", HERE)
API = "https://api.unsplash.com"
TOKEN = os.environ.get("UNSPLASH_TOKEN", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("SWEEP_MODEL", "claude-haiku-4-5-20251001")
BUDGET = int(os.environ.get("BUDGET", "45"))
FAKE = os.environ.get("SWEEP_FAKE") == "1"  # test mode: keyword classifier, no Anthropic call
CLUSTER_MIN = 8

PLAN = os.path.join(ROOT, "plan.json")
THEMES = os.path.join(ROOT, "data", "themes.json")
KNOWN = os.path.join(ROOT, "known_ids.json")
UNSORTED = os.path.join(ROOT, "unsorted.json")
LOG = os.path.join(ROOT, "SWEEP_LOG.md")
CALLS_FILE = os.path.join(ROOT, "sweep_calls.txt")

BW_STOCK_THEME = "black-and-white"
STOCK_KEYS = {"Kodak Vision3 250D": "kodak-vision3-250d", "Kodak Ultramax 400": "kodak-ultramax-400",
              "Kodak Gold 200": "kodak-gold-200", "Kodak Vision3 500T": "kodak-vision3-500t",
              "Fujicolor 200": "fujicolor-200"}


class RateLimited(Exception):
    pass


calls = 0


def unsplash(path):
    global calls
    if calls >= BUDGET:
        raise RateLimited()
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {TOKEN}", "Accept-Version": "v1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            calls += 1
            if int(r.headers.get("X-Ratelimit-Remaining", "99")) < 2:
                raise RateLimited()
            return json.load(r)
    except urllib.error.HTTPError as e:
        calls += 1
        if e.code == 403:
            raise RateLimited()
        raise


def load(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)


def stock_from_exif(exif, bw):
    """ISO + camera heuristic. Returns (stock or None, ambiguous flag)."""
    iso = exif.get("iso") or 0
    model = (exif.get("model") or "").lower()
    if bw:
        return None, False
    if iso in (250, 320):
        return "Kodak Vision3 250D", False
    if iso in (500, 640):
        return "Kodak Vision3 500T", False
    if iso == 400:
        return "Kodak Ultramax 400", False
    if iso == 200:
        # Fujicolor 200 was shot on the Olympus mju II; Gold 200 on the Canon and Pentax bodies
        return ("Fujicolor 200", True) if "mju" in model else ("Kodak Gold 200", True)
    return None, False


_CLIENT = None


def ask_claude(prompt, max_tokens=4000):
    """Official SDK: uses ANTHROPIC_API_KEY if set, else workload identity federation
    (ANTHROPIC_FEDERATION_RULE_ID / ORGANIZATION_ID / SERVICE_ACCOUNT_ID / IDENTITY_TOKEN_FILE)."""
    import anthropic as sdk
    global _CLIENT  # one client per process: each new client repeats the token exchange, which a used identity token cannot do
    if _CLIENT is None:
        _CLIENT = sdk.Anthropic()
    msg = _CLIENT.messages.create(model=MODEL, max_tokens=max_tokens,
                                  messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    return json.loads(m.group(0)) if m else text


def fake_classify(photos, themes):
    out = {}
    for p in photos:
        t = ((p["description"] or "") + " " + (p["alt"] or "")).lower()
        key = next((k for k, v in themes.items() if k != BW_STOCK_THEME and any(w in t for w in re.findall(r"[a-z]{5,}", v["title"].lower()))), None)
        out[p["id"]] = {"primary": key, "secondary": None, "bw": "black and white" in t, "label": None}
    return out


def classify(photos, themes):
    if FAKE:
        return fake_classify(photos, themes)
    options = "\n".join(f'- {k}: {v["title"]}. {v["rule"]}' for k, v in themes.items() if k != BW_STOCK_THEME)
    items = "\n".join(f'{p["id"]} | {(p["description"] or p["alt"] or "")[:420]}' for p in photos)
    prompt = f"""Sort photos from one photographer's Unsplash portfolio into collections.

Collections (key: title. rule):
{options}

For EACH photo choose:
- "primary": the best collection key, or null if none genuinely fits.
- "secondary": a second key only when it strongly fits too, else null. Place collections (tuscany, algarve, alpine) should usually be primary when the text clearly shows that place.
- "bw": true only if the text says black and white / monochrome.
- "label": if primary is null, a short 2-4 word subject label for the photo (e.g. "bicycles", "market stalls"), else null.
Do not invent places or details that the text does not state.

Photos (id | description):
{items}

Reply with only a JSON object mapping each id to {{"primary":..., "secondary":..., "bw":..., "label":...}}."""
    return ask_claude(prompt)


def propose_clusters(unsorted, themes):
    """Ask the model whether 8+ unsorted photos share a clear theme worth a new collection."""
    if FAKE or len(unsorted) < CLUSTER_MIN:
        return []
    existing = ", ".join(v["title"] for v in themes.values())
    items = "\n".join(f'{u["id"]} | {u["label"] or ""} | {u["text"][:200]}' for u in unsorted)
    prompt = f"""A photographer has these Unsplash photos that fit none of their existing collections ({existing}).
Find any groups of {CLUSTER_MIN} or more photos that share one clear, searchable theme and would make a good new public collection.
Only propose a group if the theme is coherent; leave the rest alone. A photo may be in at most one group.

Photos (id | label | description):
{items}

Reply with only a JSON array (possibly empty) of objects:
{{"key": "kebab-case-key", "title": "Collection Title (2-4 words)", "description": "One sentence.", "rule": "One sentence telling a classifier which photos belong.", "ids": ["..."]}}"""
    return ask_claude(prompt)


def main():
    if not TOKEN:
        print("UNSPLASH_TOKEN not set")
        return 1
    plan = load(PLAN, {"collections": []})
    themes = load(THEMES, {})
    known = set(load(KNOWN, []))
    unsorted = load(UNSORTED, [])
    cols = {c["key"]: c for c in plan["collections"]}
    log = []
    errors = 0

    if "--selftest" in sys.argv:
        print("anthropic auth self-test:", ask_claude("Reply with the single word OK.", 20))
        return 0
    if not ANTHROPIC_KEY and not os.environ.get("ANTHROPIC_FEDERATION_RULE_ID") and not FAKE:
        print("no Anthropic credentials configured: sweep skipped")
        open(CALLS_FILE, "w").write("0")
        return 0

    # 1. find photos we have not seen, newest first, stopping once a page has only known ones
    new, page = [], 1
    try:
        while True:
            batch = unsplash(f"/users/bgoodpic/photos?per_page=30&page={page}&order_by=latest")
            fresh = [p for p in batch if p["id"] not in known]
            new += fresh
            if len(fresh) < len(batch) or len(batch) < 30:
                break
            page += 1
    except RateLimited:
        print("rate limited while listing; will retry next batch")

    # 2. one detail call per new photo, for EXIF
    ready = []
    try:
        for p in new:
            d = unsplash(f"/photos/{p['id']}")
            ready.append({"id": p["id"], "description": p.get("description"), "alt": p.get("alt_description"),
                          "exif": d.get("exif") or {}})
    except RateLimited:
        print(f"rate limited after {len(ready)} of {len(new)} detail calls; rest next batch")

    # 3. classify in batches of 20, then append placements to the plan
    added = 0
    placed_ids = []
    if ready:
        result = {}
        for i in range(0, len(ready), 20):
            chunk = ready[i:i + 20]
            try:
                result.update(classify(chunk, themes))
            except Exception as e:  # classifier unreachable: leave these unknown, retry later
                errors += 1
                print(f"classifier failed for {len(chunk)} photos: {e}")
        for p in ready:
            r = result.get(p["id"])
            if not isinstance(r, dict):
                continue
            keys = [k for k in (r.get("primary"), r.get("secondary")) if k in themes and k in cols]
            bw = bool(r.get("bw"))
            if bw and BW_STOCK_THEME in cols:
                keys.append(BW_STOCK_THEME)
            stock, ambiguous = stock_from_exif(p["exif"], bw)
            if stock in STOCK_KEYS and STOCK_KEYS[stock] in cols:
                keys.append(STOCK_KEYS[stock])
            if not r.get("primary") or r.get("primary") not in themes:
                unsorted.append({"id": p["id"], "label": r.get("label"),
                                 "text": p["description"] or p["alt"] or ""})
            for k in dict.fromkeys(keys):
                if p["id"] not in cols[k]["photos"]:
                    cols[k]["photos"].append(p["id"])
                    added += 1
            placed_ids.append(p["id"])
            note = ", ".join(cols[k]["title"] for k in dict.fromkeys(keys)) or "UNSORTED"
            flag = " (ISO 200: Gold vs Fujicolor is a guess)" if ambiguous else ""
            log.append(f"- `{p['id']}` {(p['description'] or p['alt'] or '')[:70]}: {note}{flag}")
            known.add(p["id"])

    # 4. auto-create a collection when 8+ unsorted photos share a clear theme
    if len(unsorted) >= CLUSTER_MIN:
        try:
            for g in propose_clusters(unsorted, themes):
                ids = [i for i in g.get("ids", []) if any(u["id"] == i for u in unsorted)]
                key = re.sub(r"[^a-z0-9-]", "", g.get("key", "").lower())
                if len(ids) < CLUSTER_MIN or not key or key in cols:
                    continue
                themes[key] = {"title": g["title"], "description": g["description"], "rule": g["rule"]}
                plan["collections"].append({"key": key, "title": g["title"], "description": g["description"],
                                            "private": False, "photos": ids})
                cols[key] = plan["collections"][-1]
                unsorted = [u for u in unsorted if u["id"] not in ids]
                added += len(ids)
                log.append(f"- NEW COLLECTION **{g['title']}** created with {len(ids)} photos")
        except Exception as e:
            errors += 1
            print(f"cluster proposal failed: {e}")

    # 5. persist everything, reopen the plan if there is new work
    if added or placed_ids:
        save(PLAN, plan)
        save(THEMES, themes)
    save(KNOWN, sorted(known))
    save(UNSORTED, unsorted)
    if added and os.path.exists(os.path.join(ROOT, "DONE")):
        os.remove(os.path.join(ROOT, "DONE"))
    if log:
        with open(LOG, "a") as f:
            f.write(f"\n## {__import__('datetime').datetime.utcnow():%Y-%m-%d %H:%M} UTC: {len(placed_ids)} new photos, {added} placements\n")
            f.write("\n".join(log) + "\n")
    open(CALLS_FILE, "w").write(str(calls))
    comment = os.path.join(ROOT, "sweep_comment.md")
    if log:
        open(comment, "w").write(f"@bgood11 Filed {len(placed_ids)} new photo(s), {added} placement(s):\n\n" + "\n".join(log) + "\n")
    elif os.path.exists(comment):
        os.remove(comment)
    print(f"sweep: {len(new)} new photos seen, {len(placed_ids)} classified, {added} placements added, "
          f"{len(unsorted)} unsorted, {calls} API calls")
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
