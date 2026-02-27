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
st.title("🔗 Website Link Checker")
st.caption("Vul een website URL in en klik op Start om alle broken links te vinden.")

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

def crawl(start_url, max_links, delay, status_placeholder, metrics_placeholder, stop_placeholder):
    domain  = urlparse(start_url).netloc
    visited = set()
    queue   = [(start_url, start_url)]
    results = []

    while queue:
        # Check stop button via session state
        if st.session_state.get("stop_crawl", False):
            break

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

        # Update UI elke 5 links
        if len(visited) % 5 == 0 or not queue:
            broken = sum(1 for r in results if r["status"] in BROKEN_CODES)
            status_placeholder.markdown(f"⏳ **{len(visited)}** gecheckt — ❌ **{broken}** broken — 📋 **{len(queue)}** in wachtrij")
            with metrics_placeholder.container():
                c1, c2, c3 = st.columns(3)
                c1.metric("Gecheckt", len(visited))
                c2.metric("Broken",   broken)
                c3.metric("Wachtrij", len(queue))

        time.sleep(delay)

    return results

# ── Sidebar ──
with st.sidebar:
    st.header("⚙️ Instellingen")
    max_links = st.slider("Max links", 100, 10000, 5000, 100)
    delay     = st.slider("Delay (s)", 0.0, 1.0, 0.1, 0.05)
    st.divider()
    st.caption("🔴 404 / 410 = pagina bestaat niet")
    st.caption("🔴 500 / 502 / 503 = serverfout")

# ── Main UI ──
col1, col2 = st.columns([4, 1])
with col1:
    url_input = st.text_input("Website URL", placeholder="https://www.jouwwebsite.nl/", label_visibility="collapsed")
with col2:
    start = st.button("▶ Start", type="primary", use_container_width=True)

if st.button("⏹ Stop"):
    st.session_state.stop_crawl = True

status_placeholder  = st.empty()
metrics_placeholder = st.empty()
stop_placeholder    = st.empty()

if start and url_input:
    st.session_state.stop_crawl = False
    results = crawl(url_input, max_links, delay, status_placeholder, metrics_placeholder, stop_placeholder)

    broken = [r for r in results if r["status"] in BROKEN_CODES]
    checked = len(results)

    status_placeholder.markdown(f"✅ **Klaar!** {checked} gecheckt — ❌ {len(broken)} broken links gevonden")

    if broken:
        st.divider()
        st.subheader(f"❌ {len(broken)} Broken links")
        df = pd.DataFrame(broken)[["source_page", "url", "status", "error"]]
        df.columns = ["Gevonden op", "Broken URL", "Status", "Error"]
        st.dataframe(df, use_container_width=True, height=400)

        buf = io.StringIO()
        df.to_csv(buf, index=False)
        st.download_button(
            label=f"⬇ Download {len(broken)} broken links (CSV)",
            data=buf.getvalue().encode(),
            file_name=f"broken_links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            type="primary",
        )
    else:
        st.success("🎉 Geen broken links gevonden!")
