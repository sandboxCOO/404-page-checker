import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time, io, csv, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pandas as pd

# ─── CONFIG ───────────────────────────────────────────────────────────────────
THREADS         = 10
DELAY           = 0.3
TIMEOUT         = 10
BROKEN_CODES    = [404, 410, 500, 502, 503]
MAX_LINKS       = 10000
SKIP_EXTENSIONS = [".jpg",".jpeg",".png",".gif",".svg",".webp",
                   ".pdf",".zip",".mp4",".mp3",".woff",".woff2",".css",".js"]
SKIP_PATTERNS   = ["?page=", "&page=", "/cdn-cgi/"]
# ──────────────────────────────────────────────────────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LinkChecker/1.0)"}

st.set_page_config(page_title="Link Checker", page_icon="🔗", layout="wide")
st.title("🔗 Website Link Checker")
st.caption("Vul een website URL in en klik op Start om alle broken links te vinden.")

# ── Session state ──
if "results"  not in st.session_state: st.session_state.results  = []
if "running"  not in st.session_state: st.session_state.running  = False
if "done"     not in st.session_state: st.session_state.done     = False
if "stop"     not in st.session_state: st.session_state.stop     = False
if "visited"  not in st.session_state: st.session_state.visited  = set()
if "queue"    not in st.session_state: st.session_state.queue    = []
if "lock"     not in st.session_state: st.session_state.lock     = threading.Lock()

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

def get_links(url, html, domain):
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
        r      = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)
        status = r.status_code
        if status in (301, 302, 303, 307, 308):
            location  = r.headers.get("Location", "")
            final_url = urljoin(url, location)
            if "/404" in final_url or "not-found" in final_url:
                return 404, final_url, None, None
            r2 = requests.get(final_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            return r2.status_code, r2.url, r2.text if is_internal(url, domain) and r2.status_code == 200 else None, None
        return status, url, r.text if is_internal(url, domain) and status == 200 else None, None
    except requests.exceptions.ConnectionError: return None, url, None, "Connection error"
    except requests.exceptions.Timeout:         return None, url, None, "Timeout"
    except Exception as e:                       return None, url, None, str(e)

def process(url, source, domain, state):
    if state["stop"] or should_skip(url): return
    status, final_url, html, error = fetch(url, domain)
    new_urls = []
    if html and is_internal(url, domain):
        for link in get_links(url, html, domain):
            with state["lock"]:
                if link not in state["visited"]:
                    new_urls.append(link)
    with state["lock"]:
        matched = next((r for r in state["results"] if r["url"] == url), None)
        if matched:
            matched.update({"status": status, "final_url": final_url if final_url != url else "", "error": error or ""})
        else:
            state["results"].append({
                "source_page": source, "url": url, "status": status,
                "final_url": final_url if final_url != url else "",
                "error": error or "", "type": "internal" if is_internal(url, domain) else "external",
            })
        for link in new_urls:
            if link not in state["visited"]:
                state["queue"].append((link, url))
                state["results"].append({
                    "source_page": url, "url": link, "status": None,
                    "final_url": "", "error": "",
                    "type": "internal" if is_internal(link, domain) else "external",
                })

def crawl(start_url, state):
    domain = urlparse(start_url).netloc
    state["running"] = True
    state["done"]    = False
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        while not state["stop"]:
            with state["lock"]:
                batch = []
                while state["queue"] and len(batch) < THREADS * 2:
                    item = state["queue"].pop(0)
                    url, src = item if isinstance(item, tuple) else (item, item)
                    if url not in state["visited"] and len(state["visited"]) < MAX_LINKS:
                        state["visited"].add(url)
                        batch.append((url, src))
            if not batch:
                time.sleep(0.3)
                with state["lock"]:
                    if not state["queue"]: break
                continue
            futures = {executor.submit(process, url, src, domain, state): url for url, src in batch}
            for f in as_completed(futures):
                try: f.result()
                except: pass
            time.sleep(DELAY)
    state["running"] = False
    state["done"]    = True

# ── Sidebar ──
with st.sidebar:
    st.header("⚙️ Instellingen")
    max_links = st.slider("Max links", 100, 10000, 5000, 100)
    threads   = st.slider("Threads", 1, 20, 10)
    delay     = st.slider("Delay (s)", 0.0, 2.0, 0.3, 0.1)
    st.divider()
    st.caption("🔴 404 / 410 = pagina bestaat niet")
    st.caption("🔴 500 / 502 / 503 = serverfout")

# ── Main UI ──
col1, col2 = st.columns([4, 1])
with col1:
    url_input = st.text_input("Website URL", placeholder="https://www.jouwwebsite.nl/", label_visibility="collapsed")
with col2:
    start = st.button("▶ Start", type="primary", use_container_width=True)

stop_btn = st.button("⏹ Stop", use_container_width=False)

if stop_btn:
    st.session_state.stop = True

if start and url_input:
    # Reset
    st.session_state.results = []
    st.session_state.visited = set()
    st.session_state.queue   = [(url_input, url_input)]
    st.session_state.stop    = False
    st.session_state.done    = False
    MAX_LINKS = max_links
    THREADS   = threads
    DELAY     = delay

    state = {
        "results": st.session_state.results,
        "visited": st.session_state.visited,
        "queue":   st.session_state.queue,
        "stop":    st.session_state.stop,
        "done":    False,
        "running": False,
        "lock":    st.session_state.lock,
    }

    t = threading.Thread(target=crawl, args=(url_input, state), daemon=True)
    t.start()
    st.session_state.running = True

    # Live update loop
    progress_bar = st.progress(0)
    status_text  = st.empty()
    stats_cols   = st.columns(3)

    while state["running"] or not state["done"]:
        with state["lock"]:
            checked = len(state["visited"])
            broken  = sum(1 for r in state["results"] if r.get("status") in BROKEN_CODES)
            queued  = len(state["queue"])
            total   = checked + queued

        pct = int((checked / max(total, 1)) * 100)
        progress_bar.progress(pct)
        status_text.markdown(f"⏳ **{checked}** gecheckt — ❌ **{broken}** broken — 📋 **{queued}** in wachtrij")

        with stats_cols[0]: st.metric("Gecheckt", checked)
        with stats_cols[1]: st.metric("Broken",   broken)
        with stats_cols[2]: st.metric("Wachtrij", queued)

        if state["done"] or st.session_state.stop:
            break
        time.sleep(2)
        st.rerun()

    status_text.markdown(f"✅ **Klaar!** {checked} gecheckt — ❌ {broken} broken links gevonden")
    st.session_state.running = False
    st.session_state.done    = True
    st.session_state.results = state["results"]

# ── Resultaten ──
if st.session_state.results:
    broken = [r for r in st.session_state.results if r.get("status") in BROKEN_CODES]

    if broken:
        st.divider()
        st.subheader(f"❌ {len(broken)} Broken links")

        df = pd.DataFrame(broken)[["source_page", "url", "status", "error"]]
        df.columns = ["Gevonden op", "Broken URL", "Status", "Error"]
        st.dataframe(df, use_container_width=True, height=400)

        # Download
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
