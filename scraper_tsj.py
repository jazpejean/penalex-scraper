#!/usr/bin/env python3
"""Scraper TSJ: descarga HTML y envía al Worker (compatible con GitHub Actions)."""
import os, asyncio, base64, argparse, time, sys
from playwright.async_api import async_playwright
from curl_cffi import requests as cffi
import requests as plain_requests
import urllib3
urllib3.disable_warnings()

WORKER   = os.environ['WORKER_URL']
TOKEN    = os.environ['INGEST_TOKEN']
MAX_DOCS = int(os.environ.get('MAX_DOCS', '999999'))
DELAY    = float(os.environ.get('DELAY', '1.5'))

SALAS = {
    "scp":  {"selector": "#5", "dir": "scp"},
    "scon": {"selector": "#1", "dir": "scon"},
}

PORTAL = "https://www.tsj.gob.ve/es/decisiones"

S = cffi.Session(impersonate="chrome120")
S.verify = False


def descargar(url, tries=4):
    for i in range(tries):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 200 and len(r.content) > 500:
                return r
            if r.status_code == 404:
                return None
        except Exception:
            time.sleep(3 * (i + 1))
    return None


def normalizar_utf8(raw_bytes):
    try:
        return raw_bytes.decode('windows-1252').encode('utf-8')
    except Exception:
        pass
    try:
        return raw_bytes.decode('utf-8').encode('utf-8')
    except UnicodeDecodeError:
        return raw_bytes.decode('windows-1252', errors='replace').encode('utf-8')


def enviar(url, html_bytes, sala):
    try:
        utf8 = normalizar_utf8(html_bytes)
        b64 = base64.b64encode(utf8).decode('ascii')
        payload = {'url': url, 'html_b64': b64, 'sala': sala, 'tipo': 'sentencia'}
        r = plain_requests.post(f'{WORKER}/ingest',
                                headers={'x-token': TOKEN, 'Content-Type': 'application/json'},
                                json=payload, timeout=120)
        if not r.ok:
            print(f'      ingest HTTP {r.status_code}: {r.text[:200]}', flush=True)
        return r.ok
    except Exception as e:
        print(f'      ingest fail -> {e}', flush=True)
        return False


async def scrape(sala_code, anio, max_docs):
    info = SALAS[sala_code]
    ok = err = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        print(f"🌐 Cargando portal...", flush=True)
        await page.goto(PORTAL, wait_until="domcontentloaded", timeout=120000)

        # Esperar a que el div esté ATTACHED (no visible, porque puede estar hidden)
        try:
            await page.wait_for_selector("#contentDisplayLista", state="attached", timeout=90000)
        except Exception as e:
            print(f"   portal no cargó: {e}", flush=True)
            await browser.close()
            return

        await page.wait_for_timeout(5000)

        print(f"🎯 Sala {sala_code} ({info['selector']})...", flush=True)
        try:
            await page.click(f"a[href='{info['selector']}']", timeout=30000)
            await page.wait_for_timeout(5000)
        except Exception as e:
            print(f"   click sala fallo: {e}", flush=True)
            await browser.close()
            return

        print(f"📅 Año {anio}...", flush=True)
        try:
            await page.select_option("#select_years", str(anio), timeout=30000)
            await page.wait_for_timeout(5000)
        except Exception as e:
            print(f"   seleccion año fallo: {e}", flush=True)
            await browser.close()
            return

        dias_links = await page.query_selector_all("a.numero-dia")
        print(f"   {len(dias_links)} días con decisiones", flush=True)

        for i, link in enumerate(dias_links):
            if ok + err >= max_docs:
                print(f"   límite {max_docs} alcanzado", flush=True)
                break

            try:
                await link.click()
                await page.wait_for_timeout(3000)
            except Exception as e:
                print(f"   click día fallo: {e}", flush=True)
                continue

            enlaces = await page.query_selector_all("a[href*='historico.tsj.gob.ve']")
            urls_dia = []
            for e in enlaces:
                href = await e.get_attribute("href")
                if href and href.endswith((".HTM", ".htm", ".HTML", ".html")):
                    if href not in urls_dia:
                        urls_dia.append(href)

            print(f"   día {i+1}/{len(dias_links)}: {len(urls_dia)} sentencias", flush=True)

            for url_sent in urls_dia:
                if ok + err >= max_docs:
                    break
                r = descargar(url_sent)
                if not r:
                    err += 1
                    print(f"      ✗ {url_sent.split('/')[-1]}", flush=True)
                    continue
                if enviar(url_sent, r.content, sala_code):
                    ok += 1
                    print(f"      ✓ OK {ok} {url_sent.split('/')[-1]}", flush=True)
                else:
                    err += 1
                time.sleep(DELAY)

        await browser.close()

    print(f"\n✅ {sala_code}/{anio}: ok={ok} err={err}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sala", default="scp")
    ap.add_argument("--anio", required=True)
    a = ap.parse_args()
    asyncio.run(scrape(a.sala, int(a.anio), MAX_DOCS))
