#!/usr/bin/env python3
"""scraper_juri.py — Marca es_jurisprudencia y REEMPLAZA doctrinas con los
extractos OFICIALES de la página de jurisprudencias del TSJ.

Salas soportadas:
  - scon (Sala Constitucional): https://www.tsj.gob.ve/es/web/tsj/juriprudencias#1
  - scp  (Sala de Casación Penal): https://www.tsj.gob.ve/es/web/tsj/juriprudencias#5

Mecánica (validada con HTML real del portal):
  - `#select_years`: años que tienen jurisprudencias (los que aparecen).
  - `a.numero-dia` con onclick fire3('DD/MM/AAAA','005'): días con criterios.
  - Al hacer clic se carga el día con divs `noti_N`, cada uno = UN criterio:
      * URL a historico.tsj.gob.ve/decisiones/<sala>/... -> id de la sentencia
      * "N° de Expediente: XX-XXXX N° de Sentencia: NN"
      * Tema, Materia, Asunto
      * <div class="hide"> = EXTRACTO OFICIAL del criterio (la Sala redactó)

Para cada id de sentencia único del día:
  - Si existe en Supabase: PATCH es_jurisprudencia=true; BORRA sus doctrinas
    (las de IA) y guarda CADA extracto oficial como doctrina (reemplazo).
  - Si NO existe: la INTEGRA automáticamente (descarga del histórico con
    Playwright + worker /ingest, que la sube a R2 e inserta en Supabase)
    y luego la marca con sus criterios. Nada queda por fuera.
  - Idempotente: si la sentencia ya tiene EXACTAMENTE los mismos extractos
    oficiales (tipo='jurisprudencia'), no se reescriben (ideal para cron).

Uso:
  python scraper_juri.py --sala scp [--aplicar] [--max-dias N] [--anio 2000]
  python scraper_juri.py --sala scon [--aplicar] [--max-dias N] [--anio 2000]
  (sin --aplicar = modo dry-run: solo muestra qué se marcaría/reemplazaría)
"""
import argparse, asyncio, base64, os, re, sys, html as H
from datetime import datetime, timezone
import requests as plain_requests

sys.path.insert(0, ".")
from penalex.config import Config
from penalex.supabase import Supabase

SALAS = {
    "scon": "https://www.tsj.gob.ve/es/web/tsj/juriprudencias#1",
    "scp": "https://www.tsj.gob.ve/es/web/tsj/juriprudencias#5",
}

RE_URL = re.compile(r'https://historico\.tsj\.gob\.ve/decisiones/[^"&<>\s]+\.HTML?', re.I)
RE_REF = re.compile(r'N°\s*de\s*Expediente:\s*([A-Za-z]{0,4}\d{1,4}[\d-]*)\s*N°\s*de\s*Sentencia:\s*(\d+)')
RE_TEMA = re.compile(r'<b>Tema:</b>\s*(.*?)<br')
RE_MATERIA = re.compile(r'<b>Materia:</b>\s*(.*?)<br')
RE_ASUNTO = re.compile(r'<b>Asunto:</b>\s*(.*?)<br')
RE_EXTRACTO = re.compile(r'<div class="hide">(.*?)</div>', re.S)
RE_NOTI = re.compile(r'<div id="noti_\d+" class="comment cf">', re.I)


def id_de_url(u: str) -> str:
    """id de la sentencia = nombre del archivo sin extensión ni espacios finales
    (idéntico a `idDe` del Worker, que es como quedó en la BD)."""
    nombre = u.split('/')[-1]
    nombre = re.sub(r'\.(HTM|HTML|htm|html)$', '', nombre, flags=re.I)
    return re.sub(r'[\s.]+$', '', nombre)


def limpio(t: str) -> str:
    t = re.sub(r'<br\s*/?>', '\n', t or '')
    t = re.sub(r'<[^>]+>', ' ', t)
    t = H.unescape(t)
    return re.sub(r'\s+', ' ', t).strip()


def _normalizar(t: str) -> str:
    """Compara extractos ignorando mayúsculas, espacios y puntuación."""
    return re.sub(r'[\s.,;:¡!¿?«»"\'()\-]+', ' ', (t or '').lower()).strip()


def _extractos_oficiales_existentes(sb: Supabase, sid: str) -> set:
    """Extractos oficiales (tipo='jurisprudencia') que ya tiene la sentencia."""
    r = sb._r("doctrinas", "GET", params={
        "select": "parrafo_evidencia", "sentencia_id": f"eq.{sid}", "tipo": "eq.jurisprudencia",
        "limit": "50",
    })
    if not r.ok:
        return set()
    return {_normalizar(d.get("parrafo_evidencia") or "") for d in r.json()}


MESES = {
    "01": "enero", "02": "febrero", "03": "marzo", "04": "abril",
    "05": "mayo", "06": "junio", "07": "julio", "08": "agosto",
    "09": "septiembre", "10": "octubre", "11": "noviembre", "12": "diciembre",
}
RE_FIRE3 = re.compile(r"fire3\('(\d{2})/(\d{2})/(\d{4})'")


async def _fecha_de_link(link) -> str | None:
    """Fecha del día desde el onclick fire3('DD/MM/AAAA','003') del link."""
    try:
        onclick = await link.get_attribute("onclick") or ""
        m = RE_FIRE3.search(onclick)
        if m:
            mes = MESES.get(m.group(2), m.group(2))
            return f"{int(m.group(1))} de {mes} de {m.group(3)}"
    except Exception:
        pass
    return None


async def ingestar_por_worker(ctx, url: str, sala: str, fecha: str | None,
                              worker_url: str, token: str) -> bool:
    """Descarga el HTML de la sentencia (Playwright, única vía que el TSJ
    no bloquea) y lo envía al worker /ingest, que sube a R2 e inserta en
    Supabase con la misma regla idDe. Devuelve True si el worker aceptó.
    """
    pagina = await ctx.new_page()
    try:
        for intento in range(3):
            try:
                await pagina.goto(url, wait_until="domcontentloaded", timeout=120000)
                break
            except Exception:
                await pagina.wait_for_timeout(5000)
        html = await pagina.content()
    except Exception as e:
        print(f"      ⚠️ descarga {url.split('/')[-1]}: {e}", flush=True)
        return False
    finally:
        await pagina.close()
    if not html or len(html) < 500:
        print(f"      ⚠️ HTML muy corto para {url.split('/')[-1]} ({len(html) or 0} bytes)", flush=True)
        return False

    try:
        utf8 = html.encode("utf-8")
        b64 = base64.b64encode(utf8).decode("ascii")
        payload = {
            "url": url,
            "html_b64": b64,
            "sala": sala,
            "tipo": "sentencia",
            "fecha": fecha,
        }
        r = plain_requests.post(
            f"{worker_url}/ingest",
            headers={"x-token": token, "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if not r.ok:
            print(f"      ⚠️ ingest {r.status_code}: {r.text[:200]}", flush=True)
            return False
        j = r.json()
        if j.get("ok") is False:
            print(f"      ⚠️ ingest fail worker: {j.get('error', '?')[:200]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"      ⚠️ ingest error: {e}", flush=True)
        return False


def extraer_criterios(html_dia: str) -> dict:
    """Devuelve {id_sentencia: [criterio, ...]}, un criterio por div noti_N.

    Cada criterio: {id, num_sentencia, expediente, tema, materia, asunto,
    extracto, url}. Varios criterios pueden apuntar a la MISMA sentencia
    (una sentencia puede tener varios criterios jurisprudenciales).

    El parseo divide el HTML por el marcador <div id="noti_N" class="comment
    cf"> (no depende del cierre exacto de los divs, que varía entre salas)."""
    marcadores = list(RE_NOTI.finditer(html_dia))
    por_id: dict = {}
    for k, m in enumerate(marcadores):
        inicio = m.end()
        fin = marcadores[k + 1].start() if k + 1 < len(marcadores) else len(html_dia)
        b = html_dia[inicio:fin]
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


async def recorrer(sb: Supabase, sala: str, aplicar: bool, max_dias: int, anio_filtro: int | None):
    from playwright.async_api import async_playwright
    marcadas = reemplazadas = no_encontradas = 0
    no_encontradas_ids: list[str] = []
    usadas: set = set()

    # Códigos de sala del portlet (li.nombre-sala onclick fire('XXX')).
    CODIGOS_SALA = {"scon": "005", "scp": "003"}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--headless=new"])
        ctx = await browser.new_context(
            ignore_https_errors=True,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "Chrome/120.0.0.0 Safari/537.36"),
        )
        page = await ctx.new_page()
        print(f"🌐 Cargando portal de jurisprudencias {sala.upper()}...", flush=True)
        # El fragmento #N no basta: hay que hacer clic en la sala del portlet
        # (dispara fire('XXX') -> carga el select de años en #years).
        url = SALAS[sala].split("#")[0]
        for intento in range(3):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=120000)
                break
            except Exception:
                await page.wait_for_timeout(5000)
        await page.wait_for_selector("#years", state="attached", timeout=120000)
        await page.wait_for_timeout(3000)

        # 1) clic en la sala (por código o por posición). El portal es lento:
        #    esperar a que la lista de salas esté visible antes de hacer clic.
        selector_sala = f'li.nombre-sala[onclick*="{CODIGOS_SALA[sala]}"]'
        try:
            await page.wait_for_selector(selector_sala, state="visible", timeout=60000)
            await page.click(selector_sala)
            print(f"✅ clic en sala {sala} ({CODIGOS_SALA[sala]})", flush=True)
        except Exception as e:
            print(f"⚠️ no encontré la sala {sala}: {e}", flush=True)

        # 2) esperar el select de años (se crea dinámico dentro de #years).
        #    Reintentar la carga si el portal no respondió.
        select_ok = False
        for intento in range(3):
            try:
                await page.wait_for_selector("#years select", state="attached", timeout=45000)
                await page.wait_for_timeout(2000)
                select_ok = True
                break
            except Exception:
                print(f"   ⚠️ intento {intento+1}: select de años no apareció; recargando...", flush=True)
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=120000)
                    await page.wait_for_timeout(3000)
                    await page.wait_for_selector(selector_sala, state="visible", timeout=60000)
                    await page.click(selector_sala)
                    await page.wait_for_timeout(4000)
                except Exception:
                    pass
        if not select_ok:
            print("❌ No se pudo cargar el select de años (portal inestable).", flush=True)
            await browser.close()
            return marcadas, reemplazadas, no_encontradas

        # Años disponibles = los que tienen jurisprudencias (según el portal).
        opciones = await page.eval_on_selector_all(
            "#years select option",
            "opts => opts.map(o => o.value)",
        )
        anios = [int(a) for a in opciones if a.strip().isdigit()]
        if anio_filtro:
            anios = [a for a in anios if a == anio_filtro]
        print(f"📅 Años con jurisprudencias: {anios}", flush=True)

        for anio in anios:
            try:
                await page.select_option("#years select", str(anio), timeout=30000)
                await page.wait_for_timeout(5000)
            except Exception as e:
                print(f"   ⚠️ año {anio}: {e}", flush=True)
                continue

            dias = await page.query_selector_all("a.numero-dia")
            print(f"   {anio}: {len(dias)} días con criterios", flush=True)
            for i, link in enumerate(dias[:max_dias]):
                try:
                    await link.click()
                    await page.wait_for_timeout(4000)
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
                        # INTEGRAR: la jurisprudencia existe en el portal pero no
                        # está en la BD -> la ingesta ahora (descarga + worker).
                        # Nada puede quedar por fuera.
                        ingesta_ok = False
                        if aplicar:
                            cfg = Config.desde_env()
                            url_orig = lista[0].get("url", "")
                            fecha_dia = None
                            if hasattr(link, "get_attribute"):
                                try:
                                    fecha_dia = await _fecha_de_link(link)
                                except Exception:
                                    fecha_dia = None
                            ingesta_ok = await ingestar_por_worker(
                                ctx, url_orig, sala, fecha_dia,
                                cfg.worker_url or "", cfg.ingest_token or "",
                            )
                            if ingesta_ok:
                                await page.wait_for_timeout(3000)
                                filas2 = sb._r("sentencias", "GET", params={
                                    "select": "id,es_jurisprudencia", "id": f"eq.{sid}", "limit": "1",
                                })
                                existe = filas2.ok and bool(filas2.json())
                        if not existe:
                            no_encontradas += 1
                            no_encontradas_ids.append(sid)
                            estado = "INGESTADA" if ingesta_ok else "NO está en la BD"
                            print(f"      ❌ {sid}: {estado}", flush=True)
                            continue
                        print(f"      🆕 {sid}: integrada (no estaba en la BD)", flush=True)

                    # Idempotencia: comparar con los extractos oficiales que ya
                    # tiene la sentencia. Si son los mismos, no reescribir.
                    nuevos = [_normalizar(c["extracto"]) for c in lista]
                    existentes = _extractos_oficiales_existentes(sb, sid)
                    pendientes = [c for c, n in zip(lista, nuevos) if n not in existentes]

                    if not pendientes:
                        print(f"      ✅ {sid}: ya tenía los {len(lista)} criterios oficiales (sin cambios)", flush=True)
                        continue

                    print(f"      ✅ {sid}: {len(lista)} criterios oficiales "
                          f"({len(pendientes)} nuevos) "
                          f"{'(APLICARÍA)' if not aplicar else '(reemplazando doctrinas IA)'}", flush=True)
                    if not aplicar:
                        marcadas += 1
                        reemplazadas += len(pendientes)
                        continue

                    # 1) marcar es_jurisprudencia (su análisis ya existe: el
                    #    criterio oficial; analyzed_at evita que parezca pendiente)
                    ok1 = sb._r(f"sentencias?id=eq.{sid}", "PATCH",
                                headers={"Prefer": "return=minimal"},
                                json={"es_jurisprudencia": True,
                                      "analyzed_at": datetime.now(timezone.utc).isoformat()})
                    if not ok1.ok:
                        print(f"      ⚠️ no se pudo marcar {sid}: {ok1.status_code}", flush=True)
                    # 2) borrar doctrinas previas (reemplazo: oficiales iguales
                    #    se reinsertan iguales; IA y obsoletos desaparecen)
                    sb.borrar_doctrinas(sid)
                    # 3) guardar CADA extracto oficial como doctrina
                    for c in lista:
                        sb.guardar_doctrina(sid, {
                            "texto_ratio": c["extracto"],
                            "parrafo_evidencia": c["extracto"],
                            "tipo": "jurisprudencia",
                        })
                    # 4) poblar extracto Y ratio_decidendi de la sentencia con
                    #    el criterio oficial (el trigger recalcula search_vector:
                    #    así la búsqueda fulltext encuentra la sentencia por su
                    #    texto y la ficha técnica muestra el ratio real)
                    extracto_completo = "\n\n".join(
                        c["extracto"].strip() for c in lista if c.get("extracto")
                    )
                    if extracto_completo:
                        ec = extracto_completo.strip()
                        if ec and ec[-1] not in ".;…":
                            ec = ec + "."
                        sb._r(f"sentencias?id=eq.{sid}", "PATCH",
                              headers={"Prefer": "return=minimal"},
                              json={"extracto": ec,
                                    "ratio_decidendi": ec})
                    marcadas += 1
                    reemplazadas += len(pendientes)

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
    ap = argparse.ArgumentParser(description="Marca jurisprudencias oficiales del TSJ (SCON o SCP)")
    ap.add_argument("--sala", choices=["scon", "scp"], default="scon",
                    help="sala a procesar (default: scon)")
    ap.add_argument("--aplicar", action="store_true", help="aplicar cambios (default: dry-run)")
    ap.add_argument("--max-dias", type=int, default=999999)
    ap.add_argument("--anio", type=int, default=None)
    args = ap.parse_args()

    cfg = Config.desde_env()
    cfg.requerir("supabase")
    sb = Supabase(cfg)
    marcadas, reemplazadas, no_enc = asyncio.run(
        recorrer(sb, args.sala, args.aplicar, args.max_dias, args.anio)
    )
    if marcadas == 0 and no_enc == 0:
        print("Nada nuevo (¿portal sin cambios o bloqueado?).")


if __name__ == "__main__":
    main()
