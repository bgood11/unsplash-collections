"""Match each Unsplash photo to its local source file to recover film stock.

1. pHash every local JPEG on the Photos SSD (same method as ukit.phash.file_phash).
2. Read embedded keywords / UserComment / Model with one batched exiftool run.
3. pHash every Unsplash thumbnail and find its nearest local file.

Writes data/local_index.json and data/matches.json.
"""
import io
import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import imagehash
from PIL import Image

ROOT = "/Volumes/Photos SSD"
SKIP = ("/Instagram", "_Instagram Exports", "Photos Library.photoslibrary", "Wondershare",
        "/Lightroom/", "_TIFF Originals", "05 Not Photos", "/.")
DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def phash_path(path):
    try:
        im = Image.open(path)
        try:
            im.draft("L", (256, 256))
        except Exception:
            pass
        return path, str(imagehash.phash(im.convert("L")))
    except Exception:
        return path, None


def phash_url(item):
    pid, url = item
    try:
        data = urllib.request.urlopen(url, timeout=30).read()
        return pid, str(imagehash.phash(Image.open(io.BytesIO(data)).convert("L")))
    except Exception:
        return pid, None


def main():
    if "--match-only" in sys.argv:
        index = json.load(open(os.path.join(DATA, "local_index.json")))
        return match(index)
    files = []
    for dp, dn, fn in os.walk(ROOT):
        if any(s in dp + "/" for s in SKIP):
            dn[:] = []
            continue
        files += [os.path.join(dp, f) for f in fn if f.lower().endswith((".jpg", ".jpeg"))]
    print(f"local jpgs: {len(files)}", flush=True)

    hashes = {}
    with ProcessPoolExecutor(max_workers=8) as ex:
        for i, (p, h) in enumerate(ex.map(phash_path, files, chunksize=32)):
            if h:
                hashes[p] = h
            if i % 1000 == 0:
                print(f"  hashed {i}", flush=True)

    argfile = os.path.join(DATA, "exif_args.txt")
    with open(argfile, "w") as f:
        f.write("\n".join(hashes))
    out = subprocess.run(["/opt/homebrew/bin/exiftool", "-j", "-q", "-q", "-IPTC:Keywords", "-XMP-dc:Subject",
                          "-EXIF:UserComment", "-EXIF:Model", "-@", argfile],
                         capture_output=True, text=True).stdout
    meta = {m["SourceFile"]: m for m in json.loads(out or "[]")}
    index = []
    for p, h in hashes.items():
        m = meta.get(p, {})
        kw = m.get("Keywords") or m.get("Subject") or []
        if isinstance(kw, str):
            kw = [kw]
        index.append({"path": p, "phash": h, "keywords": kw,
                      "comment": m.get("UserComment") or "", "model": m.get("Model") or ""})
    json.dump(index, open(os.path.join(DATA, "local_index.json"), "w"))
    print(f"indexed {len(index)} local files", flush=True)
    return match(index)


def match(index):
    photos = json.load(open(os.path.join(DATA, "photos.json")))
    with ThreadPoolExecutor(max_workers=16) as ex:
        uhash = dict(ex.map(phash_url, [(p["id"], p["thumb"]) for p in photos]))
    local = [(imagehash.hex_to_hash(e["phash"]), e) for e in index]
    matches = {}
    for pid, h in uhash.items():
        if not h:
            continue
        hh = imagehash.hex_to_hash(h)
        d, e = min(((hh - lh, e) for lh, e in local), key=lambda t: t[0])
        matches[pid] = {"distance": int(d), "path": e["path"], "keywords": e["keywords"],
                        "comment": e["comment"], "model": e["model"]}
    json.dump(matches, open(os.path.join(DATA, "matches.json"), "w"), indent=1)
    good = sum(1 for m in matches.values() if m["distance"] <= 8)
    print(f"unsplash photos hashed {sum(1 for h in uhash.values() if h)}; confident matches (d<=8): {good}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
