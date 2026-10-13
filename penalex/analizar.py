"""Análisis con IA remota + vectorización remota (bge-m3 -> Qdrant).

Flujo por sentencia pendiente (analyzed_at null):
  R2 TXT -> IA (Workers AI JSON mode) -> Supabase (extracto/ratio/doctrinas)
  -> embeddings bge-m3 -> Qdrant -> qdrant_id en doctrinas.

Correcciones frente al notebook:
  - No se borran doctrinas si el guardado de IA falló.
  - Re-análisis: se limpian los puntos Qdrant de la sentencia antes de re-subir
    (evita huérfanos de doctrinas descartadas).
  - ratio_decidendi se guarda completo (hasta 3000 chars), no truncado a 400.
"""
from __future__ import annotations

import time
from typing import Optional

from .config import Config
from .embeddings import EmbeddingsRemotos, Qdrant, compute_qdrant_id
from .ia import AnalizadorIA
from .supabase import Supabase


class R2:
    """Descarga TXT limpio generado por el Worker desde el bucket público."""

    def __init__(self, public_url: str, timeout: int = 60):
        self.public_url = public_url.rstrip("/")
        self.timeout = timeout

    def descargar_txt(self, r2_key: str) -> Optional[str]:
        import requests
        try:
            r = requests.get(f"{self.public_url}/{r2_key.lstrip('/')}",
                             timeout=self.timeout)
            if r.ok and len(r.content) > 200:
                return r.content.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        return None


def procesar_pendiente(sb: Supabase, ia: AnalizadorIA, r2: R2, qdrant: Optional[Qdrant],
                       emb: Optional[EmbeddingsRemotos], fila: dict) -> str:
    """Procesa una sentencia pendiente.
    Devuelve 'ok' | 'ia_falló' | 'sin_txt' | 'tramite'."""
    sid = fila["id"]
    r2_key = fila.get("r2_txt_key")
    texto = r2.descargar_txt(r2_key) if r2_key else None
    if not texto:
        return "sin_txt"

    # Filtro de MERO TRÁMITE por extensión: las decisiones de mera sustanciación
    # (autos: notificar, colocar a la orden, solicitar información...) son
    # sistemáticamente cortas y casi nunca sientan doctrina (<3% en <3k chars).
    # Se marcan sin gastar IA (los tokens/RPD del lote se van a lo que importa).
    if len(texto) < ia.cfg.min_chars_analisis:
        sb.marcar_mero_tramite(sid)
        print(f"      [tramite] {sid}: {len(texto)} chars — sin IA", flush=True)
        return "tramite"

    t0 = time.time()
    ia_data = ia.analizar(texto)
    if not ia_data:
        return "ia_falló"
    print(f"      IA {sid}: {(time.time()-t0):.1f}s — ratio {'✓' if ia_data.get('ratio_decidendi') else '·'} "
          f"— {len(ia_data.get('criterios', []))} criterios", flush=True)

    if not sb.guardar_ia(sid, ia_data):
        return "ia_falló"  # no se borran doctrinas ni se marca analizado

    # Solo tras guardar OK: reemplazar doctrinas
    sb.borrar_doctrinas(sid)
    doctrinas_con_uuid = []
    for d in ia_data.get("doctrinas", []):
        uuid = sb.guardar_doctrina(sid, d)
        if uuid:
            doctrinas_con_uuid.append((uuid, d))

    if qdrant is not None and emb is not None:
        _subir_qdrant(qdrant, emb, sb, sid, fila, ia_data, doctrinas_con_uuid)
    return "ok"


def _subir_qdrant(qdrant: Qdrant, emb: EmbeddingsRemotos, sb: Supabase, sid: str,
                  fila: dict, ia_data: dict, doctrinas_con_uuid: list) -> None:
    """Sube extracto, ratio y doctrinas (criterio + cita) a Qdrant (1024 dims)."""
    qdrant.crear_si_no_existe("sentencias_chunks")
    qdrant.crear_si_no_existe("doctrinas_vectores")
    # Índice en documento_id: sin él, Qdrant rechaza el filtro de borrado (400)
    qdrant.asegurar_index("sentencias_chunks", "documento_id")
    qdrant.asegurar_index("doctrinas_vectores", "documento_id")
    # Limpieza de puntos previos de la sentencia (re-análisis idempotente)
    qdrant.borrar_por_sentencia("sentencias_chunks", sid)
    qdrant.borrar_por_sentencia("doctrinas_vectores", sid)

    sala = fila.get("sala") or ""
    anio = str(fila.get("anio") or "")
    puntos_chunks, puntos_doctrinas = [], []
    textos_aux = []
    base_chunk = {"documento_id": sid, "sala": sala, "anio": anio}

    extracto = (ia_data.get("extracto") or "").strip()
    ratio = (ia_data.get("ratio_decidendi") or "").strip()
    if len(extracto) > 50:
        puntos_chunks.append(compute_qdrant_id(sid, "_extracto"))
        textos_aux.append(("chunk", sid, extracto,
                           {**base_chunk, "fuente": "extracto"}))
    if len(ratio) > 50:
        puntos_chunks.append(compute_qdrant_id(sid, "_ratio"))
        textos_aux.append(("chunk", sid, ratio,
                           {**base_chunk, "fuente": "ratio_decidendi"}))

    pares = []  # (uuid, qdrant_id) para persistir
    for k, (uuid, d) in enumerate(doctrinas_con_uuid):
        td = (d.get("texto_ratio") or "").strip()
        ev = (d.get("parrafo_evidencia") or "").strip()
        base_payload = {"doctrina_id": uuid, "documento_id": sid, "sala": sala,
                        "anio": anio, "tipo": d.get("tipo"),
                        "articulo": d.get("articulo_numero"),
                        "instrumento": d.get("instrumento")}
        if len(td) >= 20:
            cid = compute_qdrant_id(sid, f"_d{k}_criterio")
            pares.append((uuid, cid))
            textos_aux.append(("doctrina", sid, td, {**base_payload, "fuente": "criterio"}))
            puntos_doctrinas.append(cid)
        if len(ev) >= 40:
            vid = compute_qdrant_id(sid, f"_d{k}_cita")
            textos_aux.append(("doctrina", sid, ev, {**base_payload, "fuente": "cita_textual"}))
            puntos_doctrinas.append(vid)

    # vectorizar en lotes y subir agrupado por colección (una llamada cada una)
    if textos_aux:
        vectores = emb.vectorizar([t[2] for t in textos_aux])
        por_col: dict[str, list] = {"sentencias_chunks": [], "doctrinas_vectores": []}
        for (tipo, _, texto, payload), vec in zip(textos_aux, vectores):
            col = "sentencias_chunks" if tipo == "chunk" else "doctrinas_vectores"
            pid = puntos_chunks.pop(0) if tipo == "chunk" else puntos_doctrinas.pop(0)
            payload = {**payload, "texto_chunk": texto}
            por_col[col].append({"id": pid, "vector": vec, "payload": payload})
        for col, puntos in por_col.items():
            if puntos:
                qdrant.upsert(col, puntos)

    for uuid, qid in pares:
        sb.guardar_qdrant_id(uuid, qid)


def analizar_pendientes(sala: Optional[str] = None, anio: Optional[int] = None,
                        limite: int = 500, vectorizar: bool = True,
                        excluir: Optional[set] = None) -> dict:
    cfg = Config.desde_env()
    cfg.requerir("ia", "supabase")
    sb = Supabase(cfg)
    ia = AnalizadorIA(cfg)
    r2 = R2(cfg.r2_public_url)
    qd = Qdrant(cfg) if (vectorizar and cfg.qdrant_url) else None
    emb = EmbeddingsRemotos(cfg) if qd else None

    pendientes = sb.pendientes_ia(anio=anio, sala=sala, limite=limite,
                                  excluir=excluir)
    print(f"⏳ {len(pendientes)} sentencias pendientes de análisis", flush=True)
    res = {"ok": 0, "ia_falló": 0, "sin_txt": 0, "tramite": 0,
           "ids_falladas": [],
           "sin_pendientes": len(pendientes) == 0}
    for fila in pendientes:
        estado = procesar_pendiente(sb, ia, r2, qd, emb, fila)
        res[estado] = res.get(estado, 0) + 1
        if estado != "ok":
            res["ids_falladas"].append(fila["id"])
        print(f"   [{estado}] {fila['id']}", flush=True)
    print(f"✅ análisis: {res}", flush=True)
    return res