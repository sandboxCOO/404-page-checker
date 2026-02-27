import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time, io
import pandas as pd
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BROKEN_CODES    = [404, 410, 500, 502, 503]
SKIP_EXTENSIONS = [".jpg",".jpeg",".png",".gif",".svg",".webp",
                   ".pdf",".zip",".mp4",".mp3",".woff",".woff2",".css",".js"]
SKIP_PATTERNS   = ["?page=", "&page=", "/cdn-cgi/"]
# ──────────────────────────────────────────────────────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LinkChecker/1.0)"}

st.set_page_config(page_title="Link Checker", page_icon="🔗", layout="wide")

# ── Header ──
st.markdown("""
    <h1 style='margin-bottom:0'>🔗 Website Link Checker</h1>
    <p style='color:#888;font-size:16px;margin-top:8px'>
        Enter your website URL below and hit <b>Start Scan</b>. This tool automatically crawls 
        every page on your website and identifies broken links — pages that return a 
        <b>404 Page Not Found</b> or other errors. When the scan is complete, you'll see a full 
        list of broken pages and which page they were found on, ready to download as a CSV.
    </p>
    <hr style='margin:20px 0;border-color:#333'>
""", unsafe_allow_html=True)

def normalize(url):
    p = urlparse(url)
    return p._replace(query="", fragment="").geturl()

def is_internal(url, domain):
    return urlparse(url).netloc == domain

def should_skip(url):
    p = urlparse(url)
    if any(p.path.lower().endswith(e) for e in SKIP_EXTENSIONS): return True
    if any(pat in url for pat in SKIP_PATTERNS): return True
    return False

def get_links(url, html):
    soup  = BeautifulSoup(html, "html.parser")
    links = set()
    for tag in soup.find_all("a", href=True):
        full = normalize(urljoin(url, tag["href"]))
        p    = urlparse(full)
        if p.scheme in ("http", "https") and not any(p.path.lower().endswith(e) for e in SKIP_EXTENSIONS):
            links.add(full)
    return links

def fetch(url, domain):
    try:
        r      = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=False)
        status = r.status_code
        if status in (301, 302, 303, 307, 308):
            location  = r.headers.get("Location", "")
            final_url = urljoin(url, location)
            if "/404" in final_url or "not-found" in final_url:
                return 404, final_url, None, None
            r2 = requests.get(final_url, headers=HEADERS, timeout=10, allow_redirects=True)
            return r2.status_code, r2.url, r2.text if is_internal(url, domain) and r2.status_code == 200 else None, None
        return status, url, r.text if is_internal(url, domain) and status == 200 else None, None
    except requests.exceptions.ConnectionError: return None, url, None, "Connection error"
    except requests.exceptions.Timeout:         return None, url, None, "Timeout"
    except Exception as e:                       return None, url, None, str(e)

def crawl(start_url, max_links, delay, status_ph, metrics_ph):
    domain  = urlparse(start_url).netloc
    visited = set()
    queue   = [(start_url, start_url)]
    results = []

    while queue and not st.session_state.get("stop_crawl", False):
        url, source = queue.pop(0)
        url = normalize(url)

        if url in visited or len(visited) >= max_links or should_skip(url):
            continue
        visited.add(url)

        status, final_url, html, error = fetch(url, domain)
        results.append({
            "source_page": source, "url": url, "status": status,
            "final_url": final_url if final_url != url else "",
            "error": error or "",
        })

        if html and is_internal(url, domain):
            for link in get_links(url, html):
                if link not in visited:
                    queue.append((link, url))

        if len(visited) % 5 == 0 or not queue:
            checked = len(visited)
            broken  = sum(1 for r in results if r["status"] in BROKEN_CODES)
            queued  = len(queue)
            pct     = int(checked / max(checked + queued, 1) * 100)
            status_ph.progress(pct, text=f"⏳ {checked} pages scanned — ❌ {broken} broken — {queued} remaining")
            with metrics_ph.container():
                c1, c2, c3 = st.columns(3)
                c1.metric("Pages Scanned", checked)
                c2.metric("Broken Links",  broken)
                c3.metric("Queue",         queued)

        time.sleep(delay)

    return results

# ── Sidebar ──
with st.sidebar:
    st.header("⚙️ Settings")
    max_links = st.slider("Max pages to scan", 100, 10000, 5000, 100)
    delay     = st.slider("Delay per request (s)", 0.0, 1.0, 0.1, 0.05)
    st.divider()
    st.markdown("**Status codes flagged as broken:**")
    st.caption("🔴 404 — Page not found")
    st.caption("🔴 410 — Page permanently gone")
    st.caption("🔴 500 / 502 / 503 — Server error")

# ── Input ──
col1, col2, col3 = st.columns([5, 1, 1])
with col1:
    url_input = st.text_input("URL", placeholder="https://www.yourwebsite.com/", label_visibility="collapsed")
with col2:
    start = st.button("▶ Start Scan", type="primary", use_container_width=True)
with col3:
    if st.button("⏹ Stop", use_container_width=True):
        st.session_state.stop_crawl = True

status_ph  = st.empty()
metrics_ph = st.empty()

# ── Run ──
if start and url_input:
    st.session_state.stop_crawl = False
    results = crawl(url_input, max_links, delay, status_ph, metrics_ph)

    broken  = [r for r in results if r["status"] in BROKEN_CODES]
    checked = len(results)

    if broken:
        status_ph.progress(100, text=f"✅ Scan complete — {checked} pages scanned — ❌ {len(broken)} broken links found")
        st.divider()

        st.markdown(f"### ❌ {len(broken)} Broken Links Found")
        st.caption("The table below shows every broken link and the page it was found on. Download the CSV to share or action the results.")

        df = pd.DataFrame(broken)[["source_page", "url", "status", "error"]]
        df.columns = ["Found on page", "Broken URL", "Status code", "Error"]
        st.dataframe(df, use_container_width=True, height=400)

        buf = io.StringIO()
        df.to_csv(buf, index=False)
        st.download_button(
            label=f"⬇️ Download {len(broken)} broken links as CSV",
            data=buf.getvalue().encode(),
            file_name=f"broken_links_{urlparse(url_input).netloc}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            type="primary",
        )
    else:
        status_ph.progress(100, text=f"✅ Scan complete — {checked} pages scanned")
        st.success("🎉 No broken links found — your website is looking good!")
