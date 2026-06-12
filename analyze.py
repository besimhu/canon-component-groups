"""
analyze.py — Fetch all sitemaps for one or more sites, group URLs by path
structure, and produce a content migration effort assessment as an HTML report.

Sites are defined in a CSV file (default: sites.csv) with columns:
    name        Short label used in filenames and the report header
    sitemap_url Full URL to the sitemap or sitemap index

The HTML output can be opened in a browser and copied directly into Excel.

Usage:
    # Run all sites in sites.csv
    python3 analyze.py

    # Run a single site by name
    python3 analyze.py --site CUSA

    # Use a different input file
    python3 analyze.py --sites-file my-sites.csv

    # All options
    python3 analyze.py --sites-file sites.csv --site CUSA --out report.html
"""

import argparse
import asyncio
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth
    async def _stealth(page):
        await Stealth().apply_stealth_async(page)
except ImportError:
    async def _stealth(page):
        pass

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Effort thresholds (page count)
T_LOW       =  15
T_MED       =  75
T_HIGH      = 500
# > T_HIGH  → VERY HIGH


# ---------------------------------------------------------------------------
# Sitemap fetching
# ---------------------------------------------------------------------------

async def _fetch_xml(context, url: str) -> str:
    page = await context.new_page()
    await _stealth(page)
    try:
        resp = await page.goto(url, wait_until="load", timeout=30_000)
        raw  = await resp.body()
        return raw.decode("utf-8", errors="replace")
    finally:
        await page.close()


def _locs(xml: str) -> list[str]:
    """Extract all <loc> values from sitemap XML."""
    soup = BeautifulSoup(xml, "lxml-xml")
    locs = [t.get_text(strip=True) for t in soup.find_all("loc")]
    if not locs:
        soup = BeautifulSoup(xml, "html.parser")
        locs = [t.get_text(strip=True) for t in soup.find_all("loc")]
    return [u for u in locs if u.startswith("http")]


async def collect_urls(sitemap_url: str) -> tuple[list[str], list[dict]]:
    """
    Resolve a sitemap or sitemap index to a flat deduplicated URL list.
    Returns (page_urls, sitemap_manifest) where manifest has name + count per child.
    """
    all_urls: list[str] = []
    manifest: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=BROWSER_UA,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )

        print(f"Fetching {sitemap_url} …")
        xml = await _fetch_xml(context, sitemap_url)

        if "<sitemapindex" in xml:
            children = _locs(xml)
            print(f"  Sitemap index — {len(children)} child sitemaps\n")
            for child in children:
                name = child.split("/")[-1]
                print(f"  {name} … ", end="", flush=True)
                try:
                    child_xml = await _fetch_xml(context, child)
                    urls      = _locs(child_xml)
                    print(f"{len(urls)} URLs")
                    all_urls.extend(urls)
                    manifest.append({"name": name, "url": child, "count": len(urls)})
                except Exception as e:
                    print(f"FAILED ({e})")
                    manifest.append({"name": name, "url": child, "count": 0, "error": str(e)})
        else:
            urls = _locs(xml)
            all_urls = urls
            manifest.append({"name": sitemap_url.split("/")[-1], "url": sitemap_url, "count": len(urls)})
            print(f"  Regular sitemap — {len(urls)} URLs")

        await browser.close()

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    print(f"\n  Total unique URLs: {len(unique)}\n")
    return unique, manifest


# ---------------------------------------------------------------------------
# Grouping & analysis
# ---------------------------------------------------------------------------

def _parts(url: str, root_path: str = "") -> list[str]:
    path = urlparse(url).path
    if root_path and path.startswith(root_path):
        path = path[len(root_path):]
    return [p for p in path.split("/") if p]


def group_urls(urls: list[str], root_path: str = "") -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for url in urls:
        parts = _parts(url, root_path)
        key   = f"/{parts[0]}/" if parts else "/"
        groups[key].append(url)
    return dict(groups)


def effort_label(count: int) -> str:
    if count <= T_LOW:
        return "LOW"
    if count <= T_MED:
        return "MED"
    if count <= T_HIGH:
        return "HIGH"
    return "VERY HIGH"


def analyze_group(group: str, urls: list[str], root_path: str = "") -> dict:
    depths = [len(_parts(u, root_path)) for u in urls]
    max_depth = max(depths) if depths else 0

    sub2: dict[str, int] = defaultdict(int)
    sub3: dict[str, int] = defaultdict(int)
    for url in urls:
        parts = _parts(url, root_path)
        if len(parts) >= 2:
            sub2[f"/{parts[0]}/{parts[1]}/"] += 1
        if len(parts) >= 3:
            sub3[f"/{parts[0]}/{parts[1]}/{parts[2]}/"] += 1

    count = len(urls)
    return {
        "group":      group,
        "pages":      count,
        "max_depth":  max_depth,
        "sub2_count": len(sub2),
        "sub3_count": len(sub3),
        "effort":     effort_label(count),
        "top_sub2":   sorted(sub2.items(), key=lambda x: -x[1])[:6],
        "top_sub3":   sorted(sub3.items(), key=lambda x: -x[1])[:3],
        "urls":       urls[:500],
    }


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

EFFORT_STYLES = {
    "LOW":       ("background:#dcfce7;color:#166534;", "LOW"),
    "MED":       ("background:#fef9c3;color:#854d0e;", "MED"),
    "HIGH":      ("background:#fee2e2;color:#991b1b;", "HIGH"),
    "VERY HIGH": ("background:#fce7f3;color:#9d174d;", "VERY HIGH"),
}

HTML_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
    color: #1e293b;
    background: #f8fafc;
    padding: 24px 32px;
}
h1 { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
.meta { color: #64748b; font-size: 12px; margin-bottom: 20px; }
.summary-chips {
    display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px;
}
.chip {
    border-radius: 6px; padding: 8px 16px; font-weight: 600; font-size: 13px;
    display: flex; flex-direction: column; align-items: center; gap: 2px;
}
.chip .chip-num { font-size: 22px; font-weight: 800; }
.chip .chip-lbl { font-size: 11px; font-weight: 500; }

/* Key */
.key { display:flex; gap:16px; margin-bottom:16px; font-size:12px; }
.key-item { display:flex; align-items:center; gap:6px; }
.key-dot { width:12px; height:12px; border-radius:3px; }

/* Controls */
.controls { display:flex; gap:10px; margin-bottom:12px; flex-wrap:wrap; }
.controls input, .controls select {
    padding: 5px 10px; border: 1px solid #e2e8f0;
    border-radius: 6px; font-size: 12px;
}
.controls input { flex: 1; min-width: 180px; }

/* Table */
.table-wrap { overflow-x: auto; }
table {
    width: 100%;
    border-collapse: collapse;
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    overflow: hidden;
}
thead th {
    background: #f1f5f9;
    text-align: left;
    padding: 9px 12px;
    font-weight: 600;
    font-size: 12px;
    border-bottom: 2px solid #e2e8f0;
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
}
thead th:hover { background: #e2e8f0; }
thead th.sorted-asc::after  { content: " ▲"; font-size:10px; }
thead th.sorted-desc::after { content: " ▼"; font-size:10px; }
tbody tr { border-bottom: 1px solid #f1f5f9; }
tbody tr:hover { background: #f8fafc; }
tbody tr.subrow { background: #fafafa; }
tbody tr.subrow:hover { background: #f1f5f9; }
tbody tr.hidden { display: none; }
td { padding: 8px 12px; vertical-align: top; }
td.group-cell { font-family: monospace; font-size: 12px; }
td.group-cell.indent { padding-left: 28px; color: #64748b; font-size: 11px; }
.badge {
    display: inline-block;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 700;
    white-space: nowrap;
}
.num { text-align: right; font-variant-numeric: tabular-nums; }
.total-row td { font-weight: 700; background: #f1f5f9; border-top: 2px solid #e2e8f0; }
/* Sitemaps section */
.sitemaps { margin-top: 28px; }
.sitemaps h2 { font-size: 14px; font-weight: 600; margin-bottom: 10px; }
.sm-table { border-collapse: collapse; width: auto; }
.sm-table td, .sm-table th {
    border: 1px solid #e2e8f0; padding: 6px 12px; font-size: 12px;
}
.sm-table th { background: #f1f5f9; font-weight: 600; }
"""

HTML_JS = """
// ── Filtering ───────────────────────────────────────────────────────────────
const rows      = Array.from(document.querySelectorAll('tbody tr[data-group]'));
const subRows   = Array.from(document.querySelectorAll('tbody tr[data-parent]'));
const searchEl  = document.getElementById('search');
const filterEl  = document.getElementById('effort-filter');
const countEl   = document.getElementById('visible-count');

function applyFilters() {
    const q       = searchEl.value.toLowerCase();
    const effort  = filterEl.value;
    let visible   = 0;
    rows.forEach(row => {
        const group    = (row.dataset.group || '').toLowerCase();
        const rowEffort = row.dataset.effort || '';
        const show     = (!q || group.includes(q)) && (!effort || rowEffort === effort);
        row.classList.toggle('hidden', !show);
        if (show) visible++;
    });
    subRows.forEach(row => {
        const parent = document.querySelector(`tr[data-group="${row.dataset.parent}"]`);
        row.classList.toggle('hidden', !parent || parent.classList.contains('hidden'));
    });
    countEl.textContent = visible + ' groups';
}

searchEl.addEventListener('input', applyFilters);
filterEl.addEventListener('change', applyFilters);

// ── Sorting ──────────────────────────────────────────────────────────────────
let sortCol = 'pages', sortDir = -1;

document.querySelectorAll('thead th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
        const col = th.dataset.col;
        if (sortCol === col) {
            sortDir *= -1;
        } else {
            sortCol = col;
            sortDir = col === 'group' ? 1 : -1;
        }
        document.querySelectorAll('thead th').forEach(t => {
            t.classList.remove('sorted-asc', 'sorted-desc');
        });
        th.classList.add(sortDir === 1 ? 'sorted-asc' : 'sorted-desc');
        sortTable();
    });
});

function sortTable() {
    const tbody = document.querySelector('tbody');
    const effortOrder = { 'VERY HIGH': 0, 'HIGH': 1, 'MED': 2, 'LOW': 3 };
    const mainRows = rows.slice().sort((a, b) => {
        let av, bv;
        if (sortCol === 'group') {
            av = a.dataset.group; bv = b.dataset.group;
            return sortDir * av.localeCompare(bv);
        }
        if (sortCol === 'effort') {
            av = effortOrder[a.dataset.effort] ?? 9;
            bv = effortOrder[b.dataset.effort] ?? 9;
        } else {
            av = parseFloat(a.dataset[sortCol]) || 0;
            bv = parseFloat(b.dataset[sortCol]) || 0;
        }
        return sortDir * (av - bv);
    });
    // Re-insert each main row followed by its sub-rows
    const totalRow = document.querySelector('tr.total-row');
    mainRows.forEach(row => {
        tbody.appendChild(row);
        const subs = subRows.filter(sr => sr.dataset.parent === row.dataset.group);
        subs.forEach(sr => tbody.appendChild(sr));
    });
    if (totalRow) tbody.appendChild(totalRow);
    applyFilters();
}
"""


def _badge(effort: str) -> str:
    style, label = EFFORT_STYLES.get(effort, ("", effort))
    return f'<span class="badge" style="{style}">{label}</span>'


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_html_unused(groups_data: list[dict], total: int, manifest: list[dict],
               region: str, sitemap_url: str) -> str:
    # Retained for reference — use build_combined_html instead

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Summary chips — page totals per effort tier
    effort_page_totals: dict[str, int] = defaultdict(int)
    effort_group_counts: dict[str, int] = defaultdict(int)
    for g in groups_data:
        effort_page_totals[g["effort"]]  += g["pages"]
        effort_group_counts[g["effort"]] += 1

    chip_styles = {
        "VERY HIGH": "background:#fce7f3;color:#9d174d;",
        "HIGH":      "background:#fee2e2;color:#991b1b;",
        "MED":       "background:#fef9c3;color:#854d0e;",
        "LOW":       "background:#dcfce7;color:#166534;",
    }
    chips_html = f"""
        <div class="chip" style="background:#eff6ff;color:#1e40af;">
            <span class="chip-num">{total:,}</span>
            <span class="chip-lbl">Total Pages</span>
        </div>
        <div class="chip" style="background:#f1f5f9;color:#334155;">
            <span class="chip-num">{len(groups_data)}</span>
            <span class="chip-lbl">URL Groups</span>
        </div>"""
    for effort in ("VERY HIGH", "HIGH", "MED", "LOW"):
        pages  = effort_page_totals.get(effort, 0)
        groups = effort_group_counts.get(effort, 0)
        chips_html += f"""
        <div class="chip" style="{chip_styles[effort]}">
            <span class="chip-num">{pages:,}</span>
            <span class="chip-lbl">{effort} · {groups} group{"s" if groups != 1 else ""}</span>
        </div>"""

    # Table rows
    rows_html = ""
    for g in groups_data:
        group     = _esc(g["group"])
        rows_html += (
            f'<tr data-group="{group}" data-pages="{g["pages"]}" '
            f'data-depth="{g["max_depth"]}" data-sub2="{g["sub2_count"]}" '
            f'data-effort="{g["effort"]}">'
            f'<td class="group-cell">{group}</td>'
            f'<td class="num">{g["pages"]:,}</td>'
            f'<td class="num">{g["max_depth"]}</td>'
            f'<td class="num">{g["sub2_count"]}</td>'
            f'<td class="num">{g["sub3_count"]}</td>'
            f'<td>{_badge(g["effort"])}</td>'
            f'</tr>\n'
        )
        # Sub-group rows (indented)
        for sub, cnt in g["top_sub2"]:
            sub_esc = _esc(sub)
            rows_html += (
                f'<tr class="subrow" data-parent="{group}">'
                f'<td class="group-cell indent">↳ {sub_esc}</td>'
                f'<td class="num" style="color:#64748b">{cnt:,}</td>'
                f'<td colspan="4"></td>'
                f'</tr>\n'
            )

    rows_html += (
        f'<tr class="total-row">'
        f'<td>TOTAL</td>'
        f'<td class="num">{total:,}</td>'
        f'<td colspan="4"></td>'
        f'</tr>\n'
    )

    # Sitemap manifest table
    sm_rows = ""
    for sm in manifest:
        err = sm.get("error", "")
        err_cell = f'<span style="color:#ef4444">{_esc(err)}</span>' if err else "✓"
        sm_rows += (
            f'<tr><td>{_esc(sm["name"])}</td>'
            f'<td class="num">{sm["count"]:,}</td>'
            f'<td>{err_cell}</td></tr>\n'
        )

    key_html = "".join(
        f'<div class="key-item">'
        f'<div class="key-dot" style="{EFFORT_STYLES[e][0]}"></div>'
        f'<span><b>{e}</b> — {desc}</span></div>'
        for e, desc in [
            ("LOW",       f"≤ {T_LOW} pages"),
            ("MED",       f"{T_LOW+1}–{T_MED} pages"),
            ("HIGH",      f"{T_MED+1}–{T_HIGH} pages"),
            ("VERY HIGH", f"> {T_HIGH} pages"),
        ]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(region)} — URL Group Analysis</title>
<style>{HTML_CSS}</style>
</head>
<body>

<h1>{_esc(region)} — URL Group Analysis</h1>
<p class="meta">Generated {now} &nbsp;·&nbsp; Sitemap: <code>{_esc(sitemap_url)}</code></p>

<div class="summary-chips">{chips_html}</div>

<div class="key">{key_html}</div>

<div class="controls">
  <input id="search" type="text" placeholder="Search groups…">
  <select id="effort-filter">
    <option value="">All effort levels</option>
    <option value="VERY HIGH">VERY HIGH</option>
    <option value="HIGH">HIGH</option>
    <option value="MED">MED</option>
    <option value="LOW">LOW</option>
  </select>
  <span id="visible-count" style="color:#64748b;font-size:12px;align-self:center">{len(groups_data)} groups</span>
</div>

<div class="table-wrap">
<table id="main-table">
  <thead>
    <tr>
      <th data-col="group">URL Group</th>
      <th data-col="pages" class="sorted-desc" style="text-align:right">Pages</th>
      <th data-col="depth" style="text-align:right">Max Depth</th>
      <th data-col="sub2" style="text-align:right">L2 Sub-paths</th>
      <th data-col="sub3" style="text-align:right">L3 Sub-paths</th>
      <th data-col="effort">Effort</th>
    </tr>
  </thead>
  <tbody>
{rows_html}
  </tbody>
</table>
</div>

<div class="sitemaps">
  <h2>Sitemaps crawled</h2>
  <table class="sm-table">
    <tr><th>Sitemap</th><th>URLs</th><th>Status</th></tr>
    {sm_rows}
  </table>
</div>

<script>{HTML_JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Sites input file
# ---------------------------------------------------------------------------

def load_sites(path: Path) -> list[dict]:
    """Read sites.csv → list of {name, sitemap_url, root_path, locale}.

    root_path  — strip this path prefix before grouping (e.g. CVI's deep AEM path)
    locale     — keep only URLs whose first path segment matches this value,
                 then strip that segment before grouping (e.g. en_ca for CCI)
    """
    if not path.exists():
        raise FileNotFoundError(f"Sites file not found: {path}")
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        sites = []
        for r in reader:
            name = r.get("name", "").strip()
            url  = r.get("sitemap_url", "").strip()
            if name and url:
                sites.append({
                    "name":        name,
                    "sitemap_url": url,
                    "root_path":   r.get("root_path", "").strip().rstrip("/"),
                    "locale":      r.get("locale", "").strip().strip("/"),
                })
    if not sites:
        raise ValueError(f"No valid rows found in {path}")
    return sites


# ---------------------------------------------------------------------------
# Per-site data collection
# ---------------------------------------------------------------------------

MIN_PAGES = 5   # groups below this are collapsed into /other/


def collect_site_data(name: str, sitemap_url: str, sort: str,
                      root_path: str = "", locale: str = "") -> dict | None:
    """Fetch and analyse one site. Returns structured data for the combined report."""
    print(f"\n{'='*60}")
    print(f"  {name}  —  {sitemap_url}")
    if locale:
        print(f"  Locale filter: /{locale}/ (others excluded)")
    if root_path:
        print(f"  Root path stripped: {root_path}")
    print(f"{'='*60}")

    urls, manifest = asyncio.run(collect_urls(sitemap_url))

    if not urls:
        print(f"  No URLs found for {name} — skipping.")
        return None

    # Apply locale filter: keep only URLs whose first path segment matches,
    # then treat the locale as an implicit root_path for grouping
    if locale:
        locale_prefix = f"/{locale}/"
        before = len(urls)
        urls = [u for u in urls if urlparse(u).path.startswith(locale_prefix)]
        print(f"  Locale filter: {len(urls)} kept of {before} total URLs")
        # Locale segment becomes the effective root to strip
        effective_root = f"/{locale}"
    else:
        effective_root = root_path

    groups_dict = group_urls(urls, effective_root)
    analyzed    = [analyze_group(g, u, effective_root) for g, u in groups_dict.items()]

    main_groups = [g for g in analyzed if g["pages"] >= MIN_PAGES]
    other_urls  = [u for g in analyzed if g["pages"] < MIN_PAGES
                     for u in groups_dict[g["group"]]]
    if other_urls:
        other_entry = analyze_group("/other/", other_urls, effective_root)
        other_entry["group"] = "/other/"
        main_groups.append(other_entry)

    if sort == "pages":
        main_groups.sort(key=lambda x: (x["group"] == "/other/", -x["pages"]))
    elif sort == "group":
        main_groups.sort(key=lambda x: (x["group"] == "/other/", x["group"]))
    elif sort == "effort":
        order = {"VERY HIGH": 0, "HIGH": 1, "MED": 2, "LOW": 3}
        main_groups.sort(key=lambda x: (x["group"] == "/other/", order[x["effort"]], -x["pages"]))

    # Terminal summary
    effort_page_totals:  dict[str, int] = defaultdict(int)
    effort_group_counts: dict[str, int] = defaultdict(int)
    for g in main_groups:
        effort_page_totals[g["effort"]]  += g["pages"]
        effort_group_counts[g["effort"]] += 1

    print(f"\n  {'GROUP':<33} {'PAGES':>7}  EFFORT")
    print(f"  {'─'*53}")
    for g in main_groups:
        print(f"  {g['group']:<33} {g['pages']:>7,}  {g['effort']}")
    print(f"  {'─'*53}")
    print(f"  {'TOTAL':<33} {len(urls):>7,}")
    print()
    for effort in ("VERY HIGH", "HIGH", "MED", "LOW"):
        pg = effort_page_totals.get(effort, 0)
        gn = effort_group_counts.get(effort, 0)
        if pg:
            print(f"    {effort:<12} {pg:>7,} pages  {gn} group{'s' if gn != 1 else ''}")

    # Base URL for constructing clickable links in the report
    parsed   = urlparse(sitemap_url)
    url_base = f"{parsed.scheme}://{parsed.netloc}{effective_root}"

    return {
        "name":        name,
        "sitemap_url": sitemap_url,
        "groups":      main_groups,
        "total":       len(urls),
        "manifest":    manifest,
        "url_base":    url_base,
    }


# ---------------------------------------------------------------------------
# Combined HTML report (tabbed)
# ---------------------------------------------------------------------------

COMBINED_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
    font-size: 13px; color: #1e293b; background: #f8fafc;
}
/* Tab bar */
.tab-bar {
    background: #0f172a; display: flex; align-items: flex-end;
    padding: 0 24px; gap: 4px; position: sticky; top: 0; z-index: 10;
}
.tab-bar h1 {
    color: #f1f5f9; font-size: 15px; font-weight: 700;
    padding: 14px 16px 14px 0; margin-right: 12px; white-space: nowrap;
}
.tab {
    padding: 10px 20px; cursor: pointer; border-radius: 6px 6px 0 0;
    font-size: 13px; font-weight: 600; color: #94a3b8;
    border: 1px solid transparent; border-bottom: none;
    transition: background 0.15s;
}
.tab:hover { background: #1e293b; color: #e2e8f0; }
.tab.active { background: #f8fafc; color: #0f172a; border-color: #334155; }

/* Panels */
.panel { display: none; padding: 24px 32px; }
.panel.active { display: block; }

/* Site header */
.site-header { margin-bottom: 16px; }
.site-header h2 { font-size: 18px; font-weight: 700; }
.site-header .meta { color: #64748b; font-size: 12px; margin-top: 3px; }

/* Summary chips */
.summary-chips { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
.chip {
    border-radius: 6px; padding: 8px 16px; font-weight: 600; font-size: 13px;
    display: flex; flex-direction: column; align-items: center; gap: 2px;
}
.chip .chip-num { font-size: 22px; font-weight: 800; }
.chip .chip-lbl { font-size: 11px; font-weight: 500; }

/* Key */
.key { display:flex; gap:16px; margin-bottom:14px; font-size:12px; flex-wrap:wrap; }
.key-item { display:flex; align-items:center; gap:6px; }
.key-dot { width:12px; height:12px; border-radius:3px; flex-shrink:0; }

/* Controls */
.controls { display:flex; gap:10px; margin-bottom:12px; flex-wrap:wrap; align-items:center; }
.controls input, .controls select {
    padding: 5px 10px; border: 1px solid #e2e8f0;
    border-radius: 6px; font-size: 12px;
}
.controls input { flex: 1; min-width: 180px; }
.visible-count { color:#64748b; font-size:12px; }

/* Table */
.table-wrap { overflow-x: auto; margin-bottom: 28px; }
table {
    width: 100%; border-collapse: collapse;
    background: #fff; border: 1px solid #e2e8f0;
    border-radius: 8px; overflow: hidden;
}
thead th {
    background: #f1f5f9; text-align: left; padding: 9px 12px;
    font-weight: 600; font-size: 12px; border-bottom: 2px solid #e2e8f0;
    white-space: nowrap; cursor: pointer; user-select: none;
}
thead th:hover { background: #e2e8f0; }
thead th.sorted-asc::after  { content: " ▲"; font-size:10px; }
thead th.sorted-desc::after { content: " ▼"; font-size:10px; }
tbody tr { border-bottom: 1px solid #f1f5f9; }
tbody tr:hover { background: #f8fafc; }
tbody tr.subrow td { background: #fafafa; }
tbody tr.subrow:hover td { background: #f1f5f9; }
tbody tr.hidden { display: none; }
td { padding: 8px 12px; vertical-align: top; }
td.group-cell { font-family: monospace; font-size: 12px; }
td.group-cell.indent { padding-left: 28px; color: #64748b; font-size: 11px; }
.badge {
    display: inline-block; border-radius: 4px; padding: 2px 8px;
    font-size: 11px; font-weight: 700; white-space: nowrap;
}
.num { text-align: right; font-variant-numeric: tabular-nums; }
.total-row td { font-weight: 700; background: #f1f5f9; border-top: 2px solid #e2e8f0; }

/* Sitemaps section */
.sitemaps h3 { font-size: 13px; font-weight: 600; margin-bottom: 8px; color:#475569; }
.sm-table { border-collapse: collapse; }
.sm-table td, .sm-table th {
    border: 1px solid #e2e8f0; padding: 5px 10px; font-size: 12px;
}
.sm-table th { background: #f1f5f9; font-weight: 600; }

/* Pages detail row */
tr.pages-row > td {
    background: #f8fafc; padding: 6px 16px 10px 28px;
    border-bottom: 1px solid #e2e8f0;
}
tr.pages-row:hover > td { background: #f1f5f9; }
.pages-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 3px 16px;
}
.page-link {
    color: #3b82f6; text-decoration: none; font-size: 11px;
    font-family: monospace;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.page-link:hover { text-decoration: underline; }
.pages-extra { margin-top: 6px; }
details.pages-more summary {
    cursor: pointer; color: #64748b; font-size: 11px;
    margin-top: 6px; user-select: none;
}
details.pages-more summary:hover { color: #334155; }
details.pages-more[open] summary { margin-bottom: 4px; }
"""

COMBINED_JS = """
// ── Tab switching ────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(tab.dataset.panel).classList.add('active');
    });
});

// ── Per-panel filtering & sorting ───────────────────────────────────────────
document.querySelectorAll('.panel').forEach(panel => {
    const id       = panel.id;
    const rows     = Array.from(panel.querySelectorAll('tbody tr[data-group]'));
    const subRows  = Array.from(panel.querySelectorAll('tbody tr[data-parent]'));
    const searchEl = panel.querySelector('.search-input');
    const filterEl = panel.querySelector('.effort-filter');
    const countEl  = panel.querySelector('.visible-count');

    function applyFilters() {
        const q      = searchEl.value.toLowerCase();
        const effort = filterEl.value;
        let visible  = 0;
        rows.forEach(row => {
            const show = (!q || row.dataset.group.toLowerCase().includes(q))
                      && (!effort || row.dataset.effort === effort);
            row.classList.toggle('hidden', !show);
            if (show) visible++;
        });
        subRows.forEach(row => {
            const parent = panel.querySelector(`tr[data-group="${row.dataset.parent}"]`);
            row.classList.toggle('hidden', !parent || parent.classList.contains('hidden'));
        });
        countEl.textContent = visible + ' groups';
    }
    searchEl.addEventListener('input', applyFilters);
    filterEl.addEventListener('change', applyFilters);

    // Sorting
    let sortCol = 'pages', sortDir = -1;
    panel.querySelectorAll('thead th[data-col]').forEach(th => {
        th.addEventListener('click', () => {
            if (sortCol === th.dataset.col) { sortDir *= -1; }
            else { sortCol = th.dataset.col; sortDir = sortCol === 'group' ? 1 : -1; }
            panel.querySelectorAll('thead th').forEach(t =>
                t.classList.remove('sorted-asc', 'sorted-desc'));
            th.classList.add(sortDir === 1 ? 'sorted-asc' : 'sorted-desc');
            sortRows();
        });
    });

    function sortRows() {
        const tbody       = panel.querySelector('tbody');
        const effortOrder = {'VERY HIGH':0,'HIGH':1,'MED':2,'LOW':3};
        const totalRow    = panel.querySelector('tr.total-row');
        rows.slice().sort((a, b) => {
            if (sortCol === 'group')  return sortDir * a.dataset.group.localeCompare(b.dataset.group);
            if (sortCol === 'effort') return sortDir * ((effortOrder[a.dataset.effort]??9) - (effortOrder[b.dataset.effort]??9));
            return sortDir * ((parseFloat(a.dataset[sortCol])||0) - (parseFloat(b.dataset[sortCol])||0));
        }).forEach(row => {
            tbody.appendChild(row);
            subRows.filter(sr => sr.dataset.parent === row.dataset.group)
                   .forEach(sr => tbody.appendChild(sr));
        });
        if (totalRow) tbody.appendChild(totalRow);
        applyFilters();
    }
});
"""


def _build_panel(site_data: dict) -> str:
    """Build the inner HTML for one site panel (no <html>/<body> wrapper)."""
    name        = site_data["name"]
    sitemap_url = site_data["sitemap_url"]
    groups_data = site_data["groups"]
    total       = site_data["total"]
    manifest    = site_data["manifest"]
    url_base    = site_data.get("url_base", "").rstrip("/")
    panel_id    = f"panel-{name.lower().replace(' ', '-')}"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    effort_page_totals:  dict[str, int] = defaultdict(int)
    effort_group_counts: dict[str, int] = defaultdict(int)
    for g in groups_data:
        effort_page_totals[g["effort"]]  += g["pages"]
        effort_group_counts[g["effort"]] += 1

    chip_styles = {
        "VERY HIGH": "background:#fce7f3;color:#9d174d;",
        "HIGH":      "background:#fee2e2;color:#991b1b;",
        "MED":       "background:#fef9c3;color:#854d0e;",
        "LOW":       "background:#dcfce7;color:#166534;",
    }
    chips_html = f"""
        <div class="chip" style="background:#eff6ff;color:#1e40af;">
            <span class="chip-num">{total:,}</span>
            <span class="chip-lbl">Total Pages</span>
        </div>
        <div class="chip" style="background:#f1f5f9;color:#334155;">
            <span class="chip-num">{len(groups_data)}</span>
            <span class="chip-lbl">URL Groups</span>
        </div>"""
    for effort in ("VERY HIGH", "HIGH", "MED", "LOW"):
        pages  = effort_page_totals.get(effort, 0)
        groups = effort_group_counts.get(effort, 0)
        chips_html += f"""
        <div class="chip" style="{chip_styles[effort]}">
            <span class="chip-num">{pages:,}</span>
            <span class="chip-lbl">{effort} · {groups} group{"s" if groups != 1 else ""}</span>
        </div>"""

    key_html = "".join(
        f'<div class="key-item">'
        f'<div class="key-dot" style="{EFFORT_STYLES[e][0]}"></div>'
        f'<span><b>{e}</b> — {desc}</span></div>'
        for e, desc in [
            ("LOW",       f"≤ {T_LOW} pages"),
            ("MED",       f"{T_LOW+1}–{T_MED} pages"),
            ("HIGH",      f"{T_MED+1}–{T_HIGH} pages"),
            ("VERY HIGH", f"> {T_HIGH} pages"),
        ]
    )

    def _page_row(group: str, group_urls: list[str]) -> str:
        if not group_urls:
            return ""
        cap_note = " · capped at 500" if len(group_urls) >= 500 else ""
        def link(u: str) -> str:
            path = urlparse(u).path or u
            return (f'<a href="{u}" target="_blank" rel="noopener" '
                    f'class="page-link" title="{_esc(u)}">{_esc(path)}</a>')
        links_html = "\n".join(link(u) for u in group_urls)
        return (
            f'<tr class="pages-row subrow" data-parent="{group}">'
            f'<td colspan="6" class="pages-row-cell">'
            f'<details class="pages-more">'
            f'<summary>Show pages ({len(group_urls):,}{cap_note})</summary>'
            f'<div class="pages-grid">{links_html}</div>'
            f'</details>'
            f'</td></tr>\n'
        )

    rows_html = ""
    for g in groups_data:
        group     = _esc(g["group"])
        group_url = f"{url_base}{g['group'].rstrip('/')}" if url_base and g["group"] != "/other/" else ""
        group_cell = (
            f'<a href="{group_url}" target="_blank" rel="noopener" '
            f'style="color:inherit;text-decoration:none;" '
            f'onmouseover="this.style.textDecoration=\'underline\'" '
            f'onmouseout="this.style.textDecoration=\'none\'">{group}</a>'
            if group_url else group
        )
        rows_html += (
            f'<tr data-group="{group}" data-pages="{g["pages"]}" '
            f'data-depth="{g["max_depth"]}" data-sub2="{g["sub2_count"]}" '
            f'data-effort="{g["effort"]}">'
            f'<td class="group-cell">{group_cell}</td>'
            f'<td class="num">{g["pages"]:,}</td>'
            f'<td class="num">{g["max_depth"]}</td>'
            f'<td class="num">{g["sub2_count"]}</td>'
            f'<td class="num">{g["sub3_count"]}</td>'
            f'<td>{_badge(g["effort"])}</td>'
            f'</tr>\n'
        )
        rows_html += _page_row(group, g.get("urls", []))
        for sub, cnt in g["top_sub2"]:
            sub_esc  = _esc(sub)
            sub_url  = f"{url_base}{sub.rstrip('/')}" if url_base else ""
            sub_cell = (
                f'↳ <a href="{sub_url}" target="_blank" rel="noopener" '
                f'style="color:#3b82f6;text-decoration:none;" '
                f'onmouseover="this.style.textDecoration=\'underline\'" '
                f'onmouseout="this.style.textDecoration=\'none\'">{sub_esc}</a>'
                if sub_url else f'↳ {sub_esc}'
            )
            rows_html += (
                f'<tr class="subrow" data-parent="{group}">'
                f'<td class="group-cell indent">{sub_cell}</td>'
                f'<td class="num" style="color:#64748b">{cnt:,}</td>'
                f'<td colspan="4"></td>'
                f'</tr>\n'
            )
    rows_html += (
        f'<tr class="total-row">'
        f'<td>TOTAL</td><td class="num">{total:,}</td><td colspan="4"></td>'
        f'</tr>\n'
    )

    sm_rows = ""
    for sm in manifest:
        err      = sm.get("error", "")
        err_cell = f'<span style="color:#ef4444">{_esc(err)}</span>' if err else "✓"
        sm_rows += (
            f'<tr><td>{_esc(sm["name"])}</td>'
            f'<td class="num">{sm["count"]:,}</td>'
            f'<td>{err_cell}</td></tr>\n'
        )

    return f"""
<div class="site-header">
  <h2>{_esc(name)}</h2>
  <p class="meta">Generated {now} &nbsp;·&nbsp; <code>{_esc(sitemap_url)}</code></p>
</div>
<div class="summary-chips">{chips_html}</div>
<div class="key">{key_html}</div>
<div class="controls">
  <input class="search-input" type="text" placeholder="Search groups…">
  <select class="effort-filter">
    <option value="">All effort levels</option>
    <option value="VERY HIGH">VERY HIGH</option>
    <option value="HIGH">HIGH</option>
    <option value="MED">MED</option>
    <option value="LOW">LOW</option>
  </select>
  <span class="visible-count">{len(groups_data)} groups</span>
</div>
<div class="table-wrap">
<table>
  <thead>
    <tr>
      <th data-col="group">URL Group</th>
      <th data-col="pages" class="sorted-desc" style="text-align:right">Pages</th>
      <th data-col="depth" style="text-align:right">Max Depth</th>
      <th data-col="sub2" style="text-align:right">L2 Sub-paths</th>
      <th data-col="sub3" style="text-align:right">L3 Sub-paths</th>
      <th data-col="effort">Effort</th>
    </tr>
  </thead>
  <tbody>
{rows_html}
  </tbody>
</table>
</div>
<div class="sitemaps">
  <h3>Sitemaps crawled</h3>
  <table class="sm-table">
    <tr><th>Sitemap</th><th>URLs</th><th>Status</th></tr>
    {sm_rows}
  </table>
</div>
"""


def build_combined_html(all_sites: list[dict]) -> str:
    tabs_html   = ""
    panels_html = ""

    for i, site in enumerate(all_sites):
        name     = site["name"]
        panel_id = f"panel-{name.lower().replace(' ', '-')}"
        active   = "active" if i == 0 else ""
        tabs_html   += f'<div class="tab {active}" data-panel="{panel_id}">{_esc(name)}</div>\n'
        panels_html += f'<div class="panel {active}" id="{panel_id}">{_build_panel(site)}</div>\n'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canon — URL Group Analysis</title>
<style>{COMBINED_CSS}</style>
</head>
<body>

<div class="tab-bar">
  <h1>URL Group Analysis</h1>
  {tabs_html}
</div>

{panels_html}

<script>{COMBINED_JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyse site URL groupings for content migration effort.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 analyze.py                          # all sites in sites.csv
  python3 analyze.py --site CUSA              # one site only
  python3 analyze.py --sites-file my.csv      # custom input file
        """,
    )
    parser.add_argument(
        "--sites-file", default="sites.csv",
        help="CSV file with columns: name, sitemap_url  (default: sites.csv)",
    )
    parser.add_argument(
        "--site", default="",
        help="Process only this site by name (e.g. CUSA). Omit to run all.",
    )
    parser.add_argument(
        "--out", default="groups.html",
        help="Output HTML file (default: groups.html)",
    )
    parser.add_argument(
        "--sort", choices=["pages", "group", "effort"], default="pages",
        help="Initial sort order in the report (default: pages)",
    )
    args = parser.parse_args()

    sites = load_sites(Path(args.sites_file))

    if args.site:
        match = [s for s in sites if s["name"].upper() == args.site.upper()]
        if not match:
            names = ", ".join(s["name"] for s in sites)
            print(f"ERROR: '{args.site}' not found in {args.sites_file}. Available: {names}")
            raise SystemExit(1)
        sites = match

    all_site_data = []
    for site in sites:
        data = collect_site_data(
            site["name"], site["sitemap_url"], args.sort,
            root_path=site.get("root_path", ""),
            locale=site.get("locale", ""),
        )
        if data:
            all_site_data.append(data)

    if not all_site_data:
        print("No data collected — nothing to write.")
        raise SystemExit(1)

    out_path = Path(args.out)
    out_path.write_text(build_combined_html(all_site_data))
    print(f"\nReport → {out_path}  (open with: open {out_path})")


if __name__ == "__main__":
    main()
