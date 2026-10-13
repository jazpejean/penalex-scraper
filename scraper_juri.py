#!/usr/bin/env python3
"""scraper_juri.py — Marca es_jurisprudencia y REEMPLAZA doctrinas con los
extractos OFICIALES de la página de jurisprudencias de la Sala Constitucional.

Mecánica (validada con HTML real del portal):
  https://www.tsj.gob.ve/es/web/tsj/juriprudencias#1
  - `#select_years`: años que tienen jurisprudencias (los que aparecen).
  - `a.numero-dia` con onclick fire3('DD/MM/AAAA','005'): días con criterios.
  - Al hacer clic se carga el día con divs `noti_N`, cada uno = UN criterio:
      * URL a historico.tsj.gob.ve/decisiones/scon/... -> id de la sentencia
      * "N° de Expediente: XX-XXXX N° de Sentencia: NN"
      * Tema, Materia, Asunto
      * <div class="hide"> = EXTRACTO OFICIAL del criterio (la Sala redactó)

Para cada id de sentencia único del día:
  - Si existe en Supabase: PATCH es_jurisprudencia=true; BORRA sus doctrinas
    (las de IA) y guarda CADA extracto oficial como doctrina (reemplazo).
  - Si NO existe: se registra en el log (lista de pendientes).

Uso:
  python scraper_juri.py [--aplicar] [--max-dias N] [--anio 2000]
  (sin --aplicar = modo dry-run: solo muestra qué se marcaría/reemplazaría)
"""
import argparse, asyncio, re, sys, html as H

sys.path.insert(0, ".")
from penalex.config import Config
from penalex.supabase import Supabase

PORTAL_JURI = "https://www.tsj.gob.ve/es/web/tsj/juriprudencias#1"
SALA = "scon"

RE_URL = re.compile(r'https://historico\.tsj\.gob\.ve/decisiones/[^"&<>\s]+\.HTM', re.I)
RE_REF = re.compile(r'N°\s*de\s*Expediente:\s*([\d-]+)\s*N°\s*de\s*Sentencia:\s*(\d+)')
RE_TEMA = re.compile(r'<b>Tema:</b>\s*(.*?)<br')
RE_MATERIA = re.compile(r'<b>Materia:</b>\s*(.*?)<br')
RE_ASUNTO = re.compile(r'<b>Asunto:</b>\s*(.*?)<br')
RE_EXTRACTO = re.compile(r'<div class="hide">(.*?)</div>', re.S)


def id_de_url(u: str) -> str:
    """id de la sentencia = nombre del archivo sin extensión ni espacios finales
    (idéntico a `idDe` del Worker, que es como quedó en la BD)."""
    nombre = u.split('/')[-1]
    nombre = re.sub(r'\.(HTM|HTML|htm|html)$', '', nombre)
    return re.sub(r'[\s.]+$', '', nombre)


def limpio(t: str) -> str:
    t = re.sub(r'<br\s*/?>', '\n', t or '')
    t = re.sub(r'<[^>]+>', ' ', t)
    t = H.unescape(t)
    return re.sub(r'\s+', ' ', t).strip()


def extraer_criterios(html_dia: str) -> dict:
    """Devuelve {id_sentencia: [criterio, ...]}, un criterio por div noti_N.

    Cada criterio: {id, num_sentencia, expediente, tema, materia, asunto,
    extracto, url}. Varios criterios pueden apuntar a la MISMA sentencia
    (una sentencia puede tener varios criterios jurisprudenciales)."""
    bloques = re.findall(r'<div id="noti_\d+" class="comment cf">(.*?)</div>\s*</div>\s*</div>', html_dia, re.S)
    por_id: dict = {}
    for b in bloques:
        m_url = RE_URL.search(b)
        if not m_url:
            continue
        url = m_url.group(0)
        sid = id_de_url(url)
        ref = RE_REF.search(b)
        temas = RE_TEMA.search(b)
        mats = RE_MATERIA.search(b)
        asun = RE_ASUNTO.search(b)
        extractos = RE_EXTRACTO.findall(b)
        extracto = limpio(extractos[0]) if extractos else ''
        criterio = {
            'id': sid,
            'url': url,
            'expediente': ref.group(1) if ref else None,
            'num_sentencia': int(ref.group(2)) if ref else None,
            'tema': limpio(temas.group(1)) if temas else '',
            'materia': limpio(mats.group(1)) if mats else '',
            'asunto': limpio(extractos[1]) if len(extractos) > 1 else '',
            'extracto': extracto,
        }
        if not extracto:
            continue  # sin texto oficial no hay criterio que guardar
        por_id.setdefault(sid, []).append(criterio)
    return por_id


async def recorrer(sb: Supabase, aplicar: bool, max_dias: int, anio_filtro: int | None):
    from playwright.async_api import async_playwright
    marcadas = reemplazadas = no_encontradas = 0
    no_encontradas_ids: list[str] = []
    usadas: set = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--headless=new"])
        ctx = await browser.new_context(
            ignore_https_errors=True,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "Chrome/120.0.0.0 Safari/537.36"),
        )
        page = await ctx.new_page()
        print("🌐 Cargando portal de jurisprudencias...", flush=True)
        await page.goto(PORTAL_JURI, wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_selector("#select_years", state="attached", timeout=90000)
        await page.wait_for_timeout(4000)

        # Años disponibles = los que tienen jurisprudencias (según el portal).
        opciones = await page.eval_on_selector_all(
            "#select_years option",
            "opts => opts.map(o => o.value)",
        )
        anios = [int(a) for a in opciones if a.strip().isdigit()]
        if anio_filtro:
            anios = [a for a in anios if a == anio_filtro]
        print(f"📅 Años con jurisprudencias: {anios}", flush=True)

        for anio in anios:
            try:
                await page.select_option("#select_years", str(anio), timeout=30000)
                await page.wait_for_timeout(4000)
            except Exception as e:
                print(f"   ⚠️ año {anio}: {e}", flush=True)
                continue

            dias = await page.query_selector_all("a.numero-dia")
            print(f"   {anio}: {len(dias)} días con criterios", flush=True)
            for i, link in enumerate(dias[:max_dias]):
                try:
                    await link.click()
                    await page.wait_for_timeout(3500)
                except Exception as e:
                    print(f"      día {i+1} fallo click: {e}", flush=True)
                    continue

                html_dia = await page.inner_html("body")
                criterios = extraer_criterios(html_dia)
                print(f"      día {i+1}: {len(criterios)} sentencias con criterios "
                      f"({sum(len(v) for v in criterios.values())} criterios)", flush=True)

                for sid, lista in criterios.items():
                    if sid in usadas:
                        continue
                    usadas.add(sid)
                    # ¿Existe en la BD?
                    filas = sb._r("sentencias", "GET", params={
                        "select": "id,es_jurisprudencia", "id": f"eq.{sid}", "limit": "1",
                    })
                    existe = filas.ok and bool(filas.json())
                    if not existe:
                        no_encontradas += 1
                        no_encontradas_ids.append(sid)
                        print(f"      ❌ {sid}: NO está en la BD", flush=True)
                        continue

                    print(f"      ✅ {sid}: {len(lista)} criterios oficiales "
                          f"{'(APLICARÍA)' if not aplicar else '(reemplazando doctrinas IA)'}", flush=True)
                    if not aplicar:
                        marcadas += 1
                        reemplazadas += len(lista)
                        continue

                    # 1) marcar es_jurisprudencia
                    ok1 = sb._r(f"sentencias?id=eq.{sid}", "PATCH",
                                headers={"Prefer": "return=minimal"},
                                json={"es_jurisprudencia": True})
                    if not ok1.ok:
                        print(f"      ⚠️ no se pudo marcar {sid}: {ok1.status_code}", flush=True)
                    # 2) borrar doctrinas IA previas (reemplazo)
                    sb.borrar_doctrinas(sid)
                    # 3) guardar cada extracto oficial como doctrina
                    for c in lista:
                        sb.guardar_doctrina(sid, {
                            "texto_ratio": c["extracto"][:3000],
                            "parrafo_evidencia": c["extracto"],
                            "tipo": "jurisprudencia",
                        })
                    marcadas += 1
                    reemplazadas += len(lista)

        await browser.close()

    print("\n" + "=" * 56)
    print(f"RESUMEN ({'dry-run — nada modificado' if not aplicar else 'APLICADO'}):")
    print(f"  sentencias marcadas es_jurisprudencia: {marcadas}")
    print(f"  doctrinas oficiales guardadas:         {reemplazadas}")
    print(f"  jurisprudencias NO en la BD:           {no_encontradas}")
    if no_encontradas_ids:
        print("  ids no encontrados (para ingestar después):")
        for x in no_encontradas_ids[:50]:
            print(f"    - {x}")
    return marcadas, reemplazadas, no_encontradas


def main():
    ap = argparse.ArgumentParser(description="Marca jurisprudencias oficiales de la SCON")
    ap.add_argument("--aplicar", action="store_true", help="aplicar cambios (default: dry-run)")
    ap.add_argument("--max-dias", type=int, default=999999)
    ap.add_argument("--anio", type=int, default=None)
    args = ap.parse_args()

    cfg = Config.desde_env()
    cfg.requerir("supabase")
    sb = Supabase(cfg)
    marcadas, reemplazadas, no_enc = asyncio.run(
        recorrer(sb, args.aplicar, args.max_dias, args.anio)
    )
    if marcadas == 0 and no_enc == 0:
        print("Nada nuevo (¿portal sin cambios o bloqueado?).")


if __name__ == "__main__":
    main()
