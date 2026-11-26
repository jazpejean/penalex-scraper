"""Almacenamiento en Supabase (REST): sentencias, doctrinas y metadatos.

Columnas usadas (según el Worker y el notebook):
  sentencias: id, sala, tipo, anio, fecha, num_sentencia, expediente,
              url_tsj, r2_html_key, r2_txt_key, partes, procedimiento,
              ponente, decision, extracto, ratio_decidendi, es_jurisprudencia,
              analyzed_at
  doctrinas:  sentencia_id, texto_ratio, tipo, parrafo_evidencia,
              instrumento, articulo_numero, qdrant_id
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

import requests

from .config import Config


class Supabase:
    def __init__(self, cfg: Config):
        cfg.requerir("supabase")
        self.url = cfg.supabase_url.rstrip("/")
        self._s = requests.Session()
        self._s.headers.update({
            "apikey": cfg.supabase_key,
            "Authorization": f"Bearer {cfg.supabase_key}",
            "Content-Type": "application/json",
        })

    # ------------------------------------------------------------------
    def _r(self, path: str, metodo: str, **kw):
        return self._s.request(metodo, f"{self.url}/rest/v1/{path}", timeout=60, **kw)

    def consultar_ids(self, anio: int, sala: Optional[str] = None) -> set[str]:
        """IDs de sentencias ya ingresadas (filtro por anio y opcional sala)."""
        ids: set[str] = set()
        offset = 0
        while True:
            params = {"select": "id", "anio": f"eq.{anio}", "limit": 1000, "offset": offset}
            if sala:
                params["sala"] = f"eq.{sala}"
            r = self._r("sentencias", "GET", params=params)
            if not r.ok:
                break
            lote = r.json()
            if not lote:
                break
            ids.update(row["id"] for row in lote)
            offset += 1000
            if len(lote) < 1000:
                break
        return ids

    def consultar_metadatos(self, anio: int, sala: Optional[str] = None) -> dict:
        """id → {num_sentencia, expediente, fecha} de un (sala, año)."""
        out: dict = {}
        offset = 0
        while True:
            params = {"select": "id,num_sentencia,expediente,fecha",
                      "anio": f"eq.{anio}", "limit": 1000, "offset": offset}
            if sala:
                params["sala"] = f"eq.{sala}"
            r = self._r("sentencias", "GET", params=params)
            if not r.ok:
                break
            lote = r.json()
            if not lote:
                break
            for row in lote:
                out[row["id"]] = row
            offset += 1000
            if len(lote) < 1000:
                break
        return out

    def guardar_metadatos(self, sentencia_id: str, meta: dict) -> bool:
        """PATCH de metadatos precisos provenientes del portal (no nulls)."""
        payload = {}
        for campo in ("fecha", "num_sentencia", "expediente", "partes",
                      "procedimiento", "ponente", "decision", "url_tsj"):
            v = meta.get(campo)
            if v is not None and str(v).strip():
                payload[campo] = str(v).strip()
        if not payload:
            return True
        r = self._r(
            f"sentencias?id=eq.{sentencia_id}", "PATCH",
            headers={"Prefer": "return=minimal"}, json=payload,
        )
        return r.ok

    def guardar_ia(self, sentencia_id: str, ia_data: dict) -> bool:
        ratio = ia_data.get("ratio_decidendi")
        if ratio and str(ratio).strip().lower() in ("null", "none", ""):
            ratio = None
        payload = {
            "extracto": (ia_data.get("extracto") or "")[:500],
            "ratio_decidendi": str(ratio).strip()[:3000] if ratio else None,
            # es_jurisprudencia NO se toca aquí: todas son sentencias; el flag
            # lo pondrá el scraper de jurisprudencia del usuario cuando corra
            # sobre las mismas sentencias (según su definición del proyecto).
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
        r = self._r(
            f"sentencias?id=eq.{sentencia_id}", "PATCH",
            headers={"Prefer": "return=minimal"}, json=payload,
        )
        return r.ok

    def marcar_mero_tramite(self, sentencia_id: str) -> bool:
        """Marca una decisión como mero trámite (sin análisis IA) y la da por
        procesada: no volverá como pendiente ni consumirá requests del lote."""
        payload = {
            "es_mero_tramite": True,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
        r = self._r(
            f"sentencias?id=eq.{sentencia_id}", "PATCH",
            headers={"Prefer": "return=minimal"}, json=payload,
        )
        if r.ok:
            return True
        # Columna es_mero_tramite aún no existe en la BD (ALTER pendiente):
        # degrada a marcar solo analyzed_at (la fila deja de ser pendiente).
        r2 = self._r(
            f"sentencias?id=eq.{sentencia_id}", "PATCH",
            headers={"Prefer": "return=minimal"},
            json={"analyzed_at": datetime.now(timezone.utc).isoformat()},
        )
        return r2.ok

    def articulo_upsert(self, slug: str, ley_nombre: str,
                        articulo: object) -> bool:
        """Inserta/actualiza un artículo en la tabla `articulos` (merge por id)."""
        art_id = f"{slug}-art-{articulo.numero}"
        r = self._r(
            "articulos", "POST",
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"id": art_id, "instrumento": ley_nombre,
                  "numero": str(articulo.numero),
                  "titulo": str(articulo.encabezado or "")},
        )
        return r.ok

    def borrar_doctrinas(self, sentencia_id: str) -> bool:
        r = self._r(
            f"doctrinas?sentencia_id=eq.{sentencia_id}", "DELETE",
            headers={"Prefer": "return=minimal"},
        )
        return r.ok

    def borrar_citas_doctrinales(self, sentencia_id: str) -> bool:
        """Borra las citas a doctrina académica de una sentencia (idempotencia)."""
        r = self._r(
            f"doctrinas_academicas?sentencia_id=eq.{sentencia_id}", "DELETE",
            headers={"Prefer": "return=minimal"},
        )
        return r.ok

    def guardar_cita_doctrinal(self, sentencia_id: str, cita: dict) -> bool:
        """Guarda una cita a doctrina académica (autor/obra/párrafo/página)."""
        import uuid as _uuid
        payload = {
            "id": str(_uuid.uuid4()),
            "sentencia_id": sentencia_id,
            "autor": str(cita.get("autor") or "").strip() or None,
            "obra": str(cita.get("obra") or "").strip() or None,
            "parrafo_evidencia": str(
                cita.get("parrafo_evidencia") or cita.get("cita") or "").strip() or None,
            "pagina": str(cita.get("pagina") or "").strip() or None,
        }
        if not payload["parrafo_evidencia"] and not payload["autor"]:
            return False
        r = self._r("doctrinas_academicas", "POST",
                    headers={"Prefer": "return=minimal"}, json=payload)
        return r.ok

    def guardar_doctrina(self, sentencia_id: str, doctrina: dict) -> Optional[str]:
        import uuid as _uuid
        payload = {
            # id uuid NOT NULL SIN default en la tabla: hay que generarlo aquí
            # (sin él el INSERT falla con 23502 y no se guarda ninguna doctrina).
            "id": str(_uuid.uuid4()),
            "sentencia_id": sentencia_id,
            "texto_ratio": (doctrina.get("texto_ratio") or "")[:3000],
            "tipo": doctrina.get("tipo", "interpretacion"),
            "parrafo_evidencia": doctrina.get("parrafo_evidencia", ""),
            "instrumento": doctrina.get("instrumento"),
            "articulo_numero": doctrina.get("articulo_numero"),
        }
        ref = doctrina.get("referencia_citada")
        if ref:
            payload["referencia_citada"] = ref
        r = self._r(
            "doctrinas", "POST",
            headers={"Prefer": "return=representation"},
            json=payload,
        )
        if not r.ok and ref and r.status_code == 400:
            # Migración de referencia_citada pendiente: degradar sin ella.
            del payload["referencia_citada"]
            r = self._r(
                "doctrinas", "POST",
                headers={"Prefer": "return=representation"},
                json=payload,
            )
        if r.ok and r.json():
            return r.json()[0]["id"]
        return None

    def guardar_qdrant_id(self, doctrina_id: str, qdrant_id: int) -> bool:
        r = self._r(
            f"doctrinas?id=eq.{doctrina_id}", "PATCH",
            headers={"Prefer": "return=minimal"}, json={"qdrant_id": qdrant_id},
        )
        return r.ok

    def checkpoint_leer(self, sala: str, anio: int) -> Optional[str]:
        """Última fecha completada (scraper_checkpoints) o None."""
        r = self._r("scraper_checkpoints", "GET", params={
            "select": "last_fecha", "sala": f"eq.{sala}", "anio": f"eq.{anio}",
            "limit": "1",
        })
        if not r.ok or not r.json():
            return None
        return r.json()[0].get("last_fecha")

    def checkpoint_actualizar(self, sala: str, anio: int,
                              last_completed_day: int, last_fecha: str) -> bool:
        payload = {"sala": sala, "anio": str(anio),
                   "last_completed_day": last_completed_day,
                   "last_fecha": last_fecha,
                   "updated_at": datetime.now(timezone.utc).isoformat()}
        r = self._r(
            "scraper_checkpoints", "PATCH",
            params={"sala": f"eq.{sala}", "anio": f"eq.{anio}"},
            headers={"Prefer": "return=minimal"}, json=payload,
        )
        if r.ok:
            return True
        # upsert si no existe la fila
        r = self._r(
            "scraper_checkpoints", "POST",
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload,
        )
        return r.ok

    def pendientes_ia(self, anio: Optional[int] = None, sala: Optional[str] = None,
                      limite: int = 500, excluir: Optional[set] = None,
                      mes_ini: Optional[int] = None,
                      mes_fin: Optional[int] = None) -> list[dict]:
        """Sentencias ingresadas que aún no tienen análisis (analyzed_at null).

        Si `excluir` trae ids ya intentados en esta ejecución, se avanzan con
        paginación estable (order=id + offset) hasta juntar `limite` filas
        nuevas; así cada pasada procesa docs distintos y converge aunque
        muchos pendientes fallen (flakiness del modelo).

        mes_ini/mes_fin (1-12): restringe a sentencias cuyo campo `fecha`
        (texto "N de MES de AÑO") cae en ese rango de meses — permite partir
        un año en franjas disjuntas entre canales.
        """
        excluir = set(excluir or [])
        params = {
            "select": "id,sala,anio,fecha,url_tsj,r2_txt_key",
            "analyzed_at": "is.null",
            "es_jurisprudencia": "not.is.true",
            "order": "id",
        }
        if anio:
            params["anio"] = f"eq.{anio}"
        if sala:
            params["sala"] = f"eq.{sala}"
        if mes_ini or mes_fin:
            meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                     "julio", "agosto", "septiembre", "octubre", "noviembre",
                     "diciembre"]
            lo = (mes_ini or 1) - 1
            hi = (mes_fin or 12)
            elegidos = [meses[i] for i in range(lo, hi) if 0 <= i < 12]
            if elegidos:
                # OR de ilike sobre el nombre del mes en el texto `fecha`
                params["or"] = "(" + ",".join(
                    f"fecha.ilike.%{m}%" for m in elegidos) + ")"
        if not excluir:
            r = self._r("sentencias", "GET", params={**params, "limit": str(limite)})
            return r.json() if r.ok else []
        encontrados: list[dict] = []
        offset = 0
        while len(encontrados) < limite:
            r = self._r("sentencias", "GET",
                        params={**params, "limit": str(limite),
                                "offset": str(offset)})
            if not r.ok:
                break
            filas = r.json()
            if not filas:
                break
            encontrados.extend(f for f in filas if f["id"] not in excluir)
            offset += len(filas)
            if len(filas) < limite:
                break  # no hay más filas
        return encontrados[:limite]

    def filas_por_sala_anio(self, anio: int, sala: str,
                            offset: int = 0, limite: int = 1000,
                            incluir_html: bool = False) -> list[dict]:
        """Filas de sentencias (id, fecha, r2_txt_key, url_tsj[, r2_html_key])."""
        cols = "id,fecha,r2_txt_key,url_tsj" + (",r2_html_key" if incluir_html else "")
        params = {
            "select": cols,
            "anio": f"eq.{anio}", "sala": f"eq.{sala}",
            "order": "id", "limit": str(limite), "offset": str(offset),
        }
        r = self._r("sentencias", "GET", params=params)
        if not r.ok:
            return []
        return r.json()

    def conteo_por_sala_anio(self, anio: int, sala: Optional[str] = None) -> int:
        r = self._r("sentencias", "GET", params={
            "select": "id", "anio": f"eq.{anio}",
            **({"sala": f"eq.{sala}"} if sala else {}),
            "limit": "0",
        }, headers={"Prefer": "count=exact", "Range": "0-0"})
        if not r.ok:
            return -1
        try:
            return int(r.headers.get("content-range", "0").split("/")[-1])
        except ValueError:
            return -1