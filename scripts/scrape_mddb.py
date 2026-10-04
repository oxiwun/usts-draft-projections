#!/usr/bin/env python3
import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

MDDB_URL = "https://www.nflmockdraftdatabase.com/big-boards/{year}/consensus-big-board-{year}"
SCOUTING_GRADE_URL = "https://scoutinggrade.com/{year}-nfl-draft-big-board"

POSITIONS = {"QB","RB","WR","TE","OT","IOL","EDGE","DT","LB","CB","S"}

def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())

def normalize_pos(pos: str) -> str:
    p = (pos or "").strip().upper().replace("ED", "EDGE")
    aliases = {
        "FB":"RB",
        "T":"OT","LT":"OT","RT":"OT",
        "OG":"IOL","G":"IOL","C":"IOL","OC":"IOL","LG":"IOL","RG":"IOL","OL":"IOL",
        "DE":"EDGE",
        "DL":"DT","NT":"DT",
        "ILB":"LB","MLB":"LB","OLB":"LB",
        "DB":"CB",
        "FS":"S","SS":"S",
    }
    return aliases.get(p, p)

def current_future_years(count: int = 4) -> List[int]:
    now = datetime.now(timezone.utc)
    first = now.year if now.month <= 4 else now.year + 1
    return list(range(first, first + count))

def load_existing(path: Path) -> Dict[int, List[dict]]:
    by_year: Dict[int, List[dict]] = {}
    if not path.exists() or path.stat().st_size == 0:
        return by_year
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    y = int(row.get("Draft_Year", ""))
                    p = int(row.get("Projected_Pick", ""))
                except Exception:
                    continue
                if 1 <= p <= 256:
                    by_year.setdefault(y, []).append(row)
    except Exception:
        pass
    for y in by_year:
        by_year[y].sort(key=lambda r: int(r["Projected_Pick"]))
    return by_year

def scrape_mddb_top100(page, year: int) -> List[dict]:
    url = MDDB_URL.format(year=year)
    print(f"Fetching MDDB {year}: {url}")
    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if resp is not None and resp.status == 404:
        return []
    page.wait_for_timeout(2500)

    stable = 0
    last = -1
    for _ in range(25):
        count = page.locator('a[href*="/players/"]').count()
        if count >= 100:
            break
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(600)
        new_count = page.locator('a[href*="/players/"]').count()
        stable = stable + 1 if new_count == last else 0
        last = new_count
        if stable >= 3:
            break

    raw = page.evaluate(
        """() => {
          const clean = s => (s || '').replace(/\s+/g,' ').trim();
          const links = Array.from(document.querySelectorAll('a[href*="/players/"]'));
          const out = [];
          const seen = new Set();
          for (const a of links) {
            const name = clean(a.textContent);
            const href = a.getAttribute('href') || '';
            if (!name || name.length < 3 || !href.includes('/players/')) continue;
            const k = name.toLowerCase().replace(/[^a-z0-9]/g,'');
            if (!k || seen.has(k)) continue;
            seen.add(k);
            out.push({name, href});
            if (out.length >= 100) break;
          }
          return out;
        }"""
    )

    rows = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i, item in enumerate(raw[:100], start=1):
        name = re.sub(r"\s+", " ", item.get("name","")).strip()
        if not name:
            continue
        rows.append({
            "Draft_Year": year,
            "Projected_Pick": i,
            "Player": name,
            "Position": "",
            "College": "",
            "Name_Key": norm_name(name),
            "Match_Key": "",
            "Source_URL": url,
            "Last_Updated": now,
            "Status": "MDDB consensus top 100",
        })
    print(f"  MDDB {year}: {len(rows)} rows")
    return rows

def scrape_scouting_grade(page, year: int) -> List[dict]:
    url = SCOUTING_GRADE_URL.format(year=year)
    print(f"Fetching ScoutingGrade {year}: {url}")
    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if resp is not None and resp.status == 404:
        return []
    page.wait_for_timeout(1500)

    raw = page.evaluate(
        """(year) => {
          const clean = s => (s || '').replace(/\s+/g,' ').trim();
          const hrefRx = new RegExp('/' + year + '/players/');
          const posRx = /\b(QB|RB|WR|TE|OT|IOL|EDGE|DT|LB|CB|S)\b/i;
          const links = Array.from(document.querySelectorAll('a[href]')).filter(a => hrefRx.test(a.getAttribute('href') || ''));

          // The page shows the first prospects twice: once in the table and again
          // in the complete directory. Prefer the occurrence with the shortest
          // anchor text, which is the directory's name-only link, while keeping
          // the first occurrence's board order.
          const map = new Map();
          let order = 0;
          for (const a of links) {
            const href = a.getAttribute('href') || '';
            if (!href) continue;
            const anchorText = clean(a.textContent);
            if (!anchorText) continue;

            let holder = a.closest('li') || a.closest('tr') || a.parentElement;
            const holderText = clean(holder && holder.textContent);
            const schoolLink = holder && holder.querySelector && holder.querySelector('a[href*="/colleges/"]');
            const school = clean(schoolLink && schoolLink.textContent);

            if (!map.has(href)) {
              map.set(href, {order: order++, name: anchorText, holderText, school});
            } else {
              const cur = map.get(href);
              if (anchorText.length < cur.name.length) {
                cur.name = anchorText;
                cur.holderText = holderText;
                cur.school = school;
              }
            }
          }

          const items = Array.from(map.values()).sort((a,b) => a.order - b.order);
          const out = [];
          for (const item of items) {
            let name = clean(item.name);
            const txt = clean(item.holderText);
            let position = '';
            let college = clean(item.school);

            // On the complete directory, the anchor itself currently renders as
            // "Player Name POS · School". Parse that directly when available.
            const direct = name.match(/^(.*?)\s+(QB|RB|WR|TE|OT|IOL|EDGE|DT|LB|CB|S)\s*[·•|]\s*(.+)$/i);
            if (direct) {
              name = clean(direct[1]);
              position = direct[2].toUpperCase();
              if (!college) college = clean(direct[3]);
            }

            // Fallback to the surrounding row text.
            if (!position) {
              const pm = txt.match(posRx);
              if (pm) {
                position = pm[1].toUpperCase();
                const after = txt.match(new RegExp('\\b' + position + '\\b\\s*[·•|]\\s*([^·•|]+)', 'i'));
                if (after && !college) college = clean(after[1]);
              }
            }

            // Top-table anchors can include school + position + round. Strip
            // those suffixes only when the anchor still looks like card text.
            if (name.includes('·') || name.includes('Round ')) {
              const card = name.match(/^(.*?)\s+(.+?)\s*[·•|]\s*(QB|RB|WR|TE|OT|IOL|EDGE|DT|LB|CB|S)\s*[·•|]\s*Round\s+\d+/i);
              if (card) {
                name = clean(card[1]);
                if (!position) position = card[3].toUpperCase();
                if (!college) college = clean(card[2]);
              }
            }

            if (!name || name.length < 3) continue;
            out.push({name, position, college});
          }
          return out;
        }""",
        year
    )

    rows = []
    seen = set()
    for item in raw:
        name = re.sub(r"\s+", " ", item.get("name","")).strip()
        key = norm_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        pos = normalize_pos(item.get("position",""))
        college = re.sub(r"\s+", " ", item.get("college","")).strip()
        rows.append({
            "Rank": len(rows) + 1,
            "Player": name,
            "Position": pos,
            "College": college,
            "Name_Key": key,
            "Source_URL": url,
        })
    print(f"  ScoutingGrade {year}: {len(rows)} unique rows")
    return rows

def build_hybrid_year(mddb: List[dict], deep: List[dict], existing: List[dict], year: int) -> List[dict]:
    # Never replace a previously good board with a partial MDDB load.
    if len(mddb) < 100:
        if existing:
            print(f"  {year}: MDDB returned only {len(mddb)}; preserving previous {len(existing)} rows")
            return existing[:256]
        raise RuntimeError(f"MDDB returned only {len(mddb)} top-board rows")

    deep_by_name = {r["Name_Key"]: r for r in deep}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    out = []
    seen = set()

    # 1-100: user's preferred MDDB ordering.
    for r in mddb[:100]:
        key = r["Name_Key"]
        meta = deep_by_name.get(key)
        pos = normalize_pos(meta["Position"]) if meta else ""
        college = meta["College"] if meta else ""
        rr = dict(r)
        rr["Position"] = pos
        rr["College"] = college
        rr["Match_Key"] = f"{key}|{pos}" if pos else key
        rr["Last_Updated"] = now
        rr["Status"] = "MDDB consensus top 100"
        out.append(rr)
        seen.add(key)

    # 101-256: deep-board fallback, preserving its relative ordering while
    # removing anyone already represented in MDDB's top 100.
    supplemental = []
    for r in deep:
        key = r["Name_Key"]
        if not key or key in seen:
            continue
        supplemental.append(r)
        seen.add(key)
        if len(supplemental) >= 156:
            break

    # If the deep source is temporarily short, retain prior supplemental rows
    # not already represented rather than fabricating players/ranks.
    if len(supplemental) < 156 and existing:
        old = [r for r in existing if int(r.get("Projected_Pick","0") or 0) > 100]
        for r in old:
            key = norm_name(r.get("Player",""))
            if not key or key in seen:
                continue
            supplemental.append({
                "Player": r.get("Player",""),
                "Position": normalize_pos(r.get("Position","")),
                "College": r.get("College",""),
                "Name_Key": key,
                "Source_URL": r.get("Source_URL",""),
            })
            seen.add(key)
            if len(supplemental) >= 156:
                break

    for idx, r in enumerate(supplemental[:156], start=101):
        pos = normalize_pos(r.get("Position",""))
        key = r["Name_Key"]
        out.append({
            "Draft_Year": year,
            "Projected_Pick": idx,
            "Player": r["Player"],
            "Position": pos,
            "College": r.get("College",""),
            "Name_Key": key,
            "Match_Key": f"{key}|{pos}" if pos else key,
            "Source_URL": r.get("Source_URL", SCOUTING_GRADE_URL.format(year=year)),
            "Last_Updated": now,
            "Status": "ScoutingGrade supplemental after MDDB top 100",
        })

    print(f"  {year}: hybrid board = {len(out)} rows")
    return out[:256]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/draft_projection.csv")
    ap.add_argument("--limit", type=int, default=256)
    ap.add_argument("--years", nargs="*", type=int)
    args = ap.parse_args()

    if args.limit != 256:
        print("This workflow is designed to maintain exactly the top 256 available projections per class.", file=sys.stderr)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    years = args.years or current_future_years(4)
    existing = load_existing(output)
    all_rows: List[dict] = []
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1100},
            locale="en-US",
        )
        page = context.new_page()
        try:
            for year in years:
                try:
                    mddb = scrape_mddb_top100(page, year)
                    deep = scrape_scouting_grade(page, year)
                    if not mddb and not deep and not existing.get(year):
                        continue
                    rows = build_hybrid_year(mddb, deep, existing.get(year, []), year)
                    all_rows.extend(rows)
                except (PlaywrightTimeoutError, Exception) as exc:
                    failures.append(f"{year}: {exc}")
                    if existing.get(year):
                        print(f"  {year}: scrape failed; preserving prior snapshot: {exc}")
                        all_rows.extend(existing[year][:256])
                    else:
                        print(f"  {year}: scrape failed with no prior snapshot: {exc}", file=sys.stderr)
        finally:
            browser.close()

    all_rows.sort(key=lambda r: (int(r["Draft_Year"]), int(r["Projected_Pick"])))
    fields = [
        "Draft_Year","Projected_Pick","Player","Position","College",
        "Name_Key","Match_Key","Source_URL","Last_Updated","Status"
    ]
    with output.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)

    counts = {}
    for row in all_rows:
        counts[row["Draft_Year"]] = counts.get(row["Draft_Year"], 0) + 1
    print("Wrote", output, counts)

    if not all_rows:
        print("No draft-projection rows available.", file=sys.stderr)
        return 1
    if failures:
        print("Warnings:", *failures, sep="\n- ")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
