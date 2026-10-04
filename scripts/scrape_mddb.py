#!/usr/bin/env python3
import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://www.nflmockdraftdatabase.com/big-boards/{year}/consensus-big-board-{year}"


def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def normalize_pos(pos: str) -> str:
    p = (pos or "").strip().upper()
    aliases = {
        "QB": "QB",
        "RB": "RB", "FB": "RB",
        "WR": "WR",
        "TE": "TE",
        "OT": "OT", "T": "OT", "LT": "OT", "RT": "OT",
        "IOL": "IOL", "OG": "IOL", "G": "IOL", "C": "IOL", "OC": "IOL", "LG": "IOL", "RG": "IOL",
        "EDGE": "EDGE", "DE": "EDGE",
        "DT": "DT", "NT": "DT", "DL": "DT",
        "LB": "LB", "ILB": "LB", "MLB": "LB", "OLB": "LB",
        "CB": "CB", "DB": "CB",
        "S": "S", "FS": "S", "SS": "S",
    }
    return aliases.get(p, p)


def current_future_years(count: int = 4) -> List[int]:
    now = datetime.now(timezone.utc)
    # Before/during the NFL Draft (Jan-Apr), the current calendar year's class is still upcoming.
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
                except Exception:
                    continue
                by_year.setdefault(y, []).append(row)
    except Exception:
        pass
    return by_year


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def scrape_year(page, year: int, limit: int) -> List[dict]:
    url = BASE.format(year=year)
    print(f"Fetching {year}: {url}")
    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if resp is not None and resp.status == 404:
        print(f"  {year}: board not available (404)")
        return []
    page.wait_for_timeout(3500)

    # Expand the board. MDDB currently renders about 100 prospects initially and
    # exposes the rest through a load-more control rather than pure infinite scroll.
    stable = 0
    last_count = page.locator('a[href*="/players/"]').count()
    for _ in range(40):
        if last_count >= limit:
            break

        clicked = False
        for pattern in [r"load\s*more", r"show\s*more", r"view\s*more", r"more\s*prospects", r"next"]:
            btn = page.get_by_role("button", name=re.compile(pattern, re.I))
            if btn.count() and btn.first.is_visible():
                try:
                    btn.first.click(timeout=3000)
                    clicked = True
                    break
                except Exception:
                    pass

        if not clicked:
            for pattern in [r"load\s*more", r"show\s*more", r"view\s*more", r"more\s*prospects"]:
                link = page.get_by_role("link", name=re.compile(pattern, re.I))
                if link.count() and link.first.is_visible():
                    try:
                        link.first.click(timeout=3000)
                        clicked = True
                        break
                    except Exception:
                        pass

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(900)
        new_count = page.locator('a[href*="/players/"]').count()

        if new_count > last_count:
            stable = 0
        else:
            stable += 1
        last_count = new_count
        if stable >= 5:
            break

    print(f"  {year}: visible player links after expansion = {last_count}")
    try:
        button_texts = page.locator("button").all_inner_texts()
        pager_links = page.locator('a').evaluate_all("""
          els => els.map(a => ({text:(a.textContent||'').replace(/\\s+/g,' ').trim(), href:a.getAttribute('href')||''}))
                    .filter(x => /more|next|page|101|102|200|256/i.test(x.text + ' ' + x.href))
        """)
        print("MDDB BUTTONS:", button_texts)
        print("MDDB PAGER LINKS:", pager_links)
    except Exception:
        pass

    # Print a compact DOM sample to GitHub Actions logs for layout diagnostics.
    try:
        first_link = page.locator('a[href*="/players/"]').first
        if first_link.count():
            sample = first_link.evaluate("el => { let n=el; for(let i=0;i<4 && n.parentElement;i++) n=n.parentElement; return n.outerHTML; }")
            print("MDDB DOM SAMPLE:", sample[:5000])
    except Exception:
        pass

    # Preferred extraction: board cards. Current MDDB layout uses Tailwind classes.
    rows = page.evaluate(
        """
        ({limit}) => {
          const clean = s => (s || '').replace(/\s+/g, ' ').trim();
          const cards = Array.from(document.querySelectorAll('.mock-list-item'));
          const out = [];
          for (let idx = 0; idx < cards.length; idx++) {
            const card = cards[idx];
            const rankNode = card.querySelector('.pick-number');
            const nameNode = card.querySelector('.player-name') || card.querySelector('a[href*="/players/"]');
            const detailNode = card.querySelector('.player-details.college-details') || card.querySelector('.college-details');
            const posNode = card.querySelector('span.text-xs.font-bold');
            const schoolNode = card.querySelector('a[href*="/colleges/"]');
            const rankMatch = clean(rankNode && rankNode.textContent).match(/\d+/);
            const rank = rankMatch ? Number(rankMatch[0]) : (idx + 1);
            const name = clean(nameNode && nameNode.textContent);
            const details = clean(detailNode && detailNode.textContent);
            const pieces = details.split('|').map(clean);
            const position = clean(posNode && posNode.textContent) || pieces[0] || '';
            const college = clean(schoolNode && schoolNode.textContent) || pieces[1] || '';
            if (!rank || !name || rank > limit) continue;
            out.push({rank, name, position, college});
          }
          return out;
        }
        """,
        {"limit": limit},
    )

        # Fallback known to work for this site: player links are emitted in board order.
    if len(rows) < 25:
        rows = page.evaluate(
            """
            ({limit}) => {
              const clean = s => (s || '').replace(/\s+/g, ' ').trim();
              const links = Array.from(document.querySelectorAll('a[href*="/players/"]'));
              const seen = new Set();
              const out = [];
              for (const link of links) {
                const name = clean(link.textContent);
                if (!name || name.length < 3) continue;
                let node = link.parentElement;
                let position = '';
                let college = '';
                for (let depth = 0; node && depth < 6; depth++, node = node.parentElement) {
                  const detail = node.querySelector && (node.querySelector('.player-details.college-details') || node.querySelector('.college-details'));
                  if (detail) {
                    const parts = clean(detail.textContent).split('|').map(clean);
                    position = parts[0] || '';
                    college = parts[1] || '';
                    break;
                  }
                }
                if (!position) {
                  const parent = link.parentElement;
                  if (parent) {
                    const kids = Array.from(parent.children);
                    for (const kid of kids) {
                      if (kid === link) continue;

                      // Current MDDB card layout: the player name and a flex row are siblings.
                      // The flex row's first child is position; second child is the school link.
                      const style = (kid.getAttribute && kid.getAttribute('style')) || '';
                      if (style.includes('display: flex') || style.includes('display:flex')) {
                        const children = Array.from(kid.children || []);
                        if (children.length > 0) position = clean(children[0].textContent);
                        if (children.length > 1) {
                          const schoolLink = children[1].tagName === 'A'
                            ? children[1]
                            : (children[1].querySelector && children[1].querySelector('a'));
                          if (schoolLink) college = clean(schoolLink.getAttribute('aria-label') || schoolLink.textContent);
                        }
                        if (position) break;
                      }

                      const txt = clean(kid.textContent);
                      if (/^(QB|RB|FB|WR|TE|OT|T|IOL|OG|G|C|EDGE|DE|DT|NT|DL|LB|ILB|MLB|OLB|CB|DB|S|FS|SS)\b/i.test(txt)) {
                        const parts = txt.split('|').map(clean);
                        position = parts[0] || '';
                        college = parts[1] || '';
                        break;
                      }
                    }
                  }
                }
                const key = [name.toLowerCase(), position.toUpperCase(), college.toLowerCase()].join('|');
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({rank: out.length + 1, name, position, college});
                if (out.length >= limit) break;
              }
              return out;
            }
            """,
            {"limit": limit},
        )

    cleaned = []
    seen = set()
    for item in sorted(rows, key=lambda x: int(x.get("rank") or 999999)):
        try:
            rank = int(item.get("rank"))
        except Exception:
            continue
        if rank < 1 or rank > limit:
            continue
        name = _clean_text(item.get("name", ""))
        if not name:
            continue
        pos = normalize_pos(_clean_text(item.get("position", "")))
        college = _clean_text(item.get("college", ""))
        key = f"{norm_name(name)}|{pos}"
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "Draft_Year": year,
            "Projected_Pick": rank,
            "Player": name,
            "Position": pos,
            "College": college,
            "Name_Key": norm_name(name),
            "Match_Key": key,
            "Source_URL": url,
            "Last_Updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "Status": f"MDDB consensus; top {limit}",
        })
        if len(cleaned) >= limit:
            break

    print(f"  {year}: {len(cleaned)} rows")
    return cleaned


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/draft_projection.csv")
    ap.add_argument("--limit", type=int, default=256)
    ap.add_argument("--years", nargs="*", type=int)
    args = ap.parse_args()

    if args.limit < 1 or args.limit > 256:
        raise SystemExit("--limit must be between 1 and 256")

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
                    rows = scrape_year(page, year, args.limit)
                    if rows:
                        all_rows.extend(rows)
                    elif existing.get(year):
                        print(f"  {year}: preserving previous snapshot")
                        all_rows.extend(existing[year][: args.limit])
                except (PlaywrightTimeoutError, Exception) as exc:
                    failures.append(f"{year}: {exc}")
                    if existing.get(year):
                        print(f"  {year}: scrape failed, preserving prior snapshot: {exc}")
                        all_rows.extend(existing[year][: args.limit])
                    else:
                        print(f"  {year}: scrape failed with no prior snapshot: {exc}", file=sys.stderr)
        finally:
            browser.close()

    all_rows.sort(key=lambda r: (int(r["Draft_Year"]), int(r["Projected_Pick"])))
    fields = [
        "Draft_Year", "Projected_Pick", "Player", "Position", "College",
        "Name_Key", "Match_Key", "Source_URL", "Last_Updated", "Status"
    ]
    with output.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)

    counts = {}
    for row in all_rows:
        counts[row["Draft_Year"]] = counts.get(row["Draft_Year"], 0) + 1
    print("Wrote", output, counts)

    # Do not fail the workflow when a far-future board is absent if at least one class worked.
    if not all_rows:
        print("No draft-projection rows available.", file=sys.stderr)
        return 1
    if failures:
        print("Warnings:", *failures, sep="\n- ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())