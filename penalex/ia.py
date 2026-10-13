"""Cliente de IA remota: análisis de sentencias con Cloudflare Workers AI
(JSON mode) y validación de citas textuales contra el documento fuente."""
from __future__ import annotations

import json
import random
import re
import time
from typing import Optional

import requests

from .config import Config
from .prompts import PROMPT_SISTEMA, construir_prompt
from .util import evidencia_coincide

INSTRUMENTOS_VALIDOS = {
    "copp": "COPP",
    "código orgánico procesal penal": "COPP",
    "codigo organico procesal penal": "COPP",
    "código orgánico del procedimiento penal": "COPP",
    "código adjetivo penal": "COPP",
    "código penal": "Código Penal",
    "codigo penal": "Código Penal",
    "constitución": "Constitución",
    "constitucion": "Constitución",
    "constitución de la república bolivariana de venezuela": "Constitución",
    "código de enjuiciamiento criminal": "Código de Enjuiciamiento Criminal",
    "codigo de enjuiciamiento criminal": "Código de Enjuiciamiento Criminal",
    "ley orgánica de amparo": "Ley Orgánica de Amparo sobre Derechos y Garantías Constitucionales",
    "ley orgánica de amparo sobre derechos y garantías constitucionales": "Ley Orgánica de Amparo sobre Derechos y Garantías Constitucionales",
    "ley orgánica sobre sustancias estupefacientes y psicotrópicas": "Ley Orgánica sobre Sustancias Estupefacientes y Psicotrópicas",
    "ley orgánica para la protección del niño y del adolescente": "LOPNA",
    "lopna": "LOPNA",
    "código orgánico de justicia militar": "Código Orgánico de Justicia Militar",
}

TIPOS_VALIDOS = {"interpretacion", "aplicacion", "reiteracion"}


def normalizar_instrumento(inst: Optional[str]) -> Optional[str]:
    if not inst:
        return None
    k = str(inst).strip().lower()
    if k in ("otro", "n/a", "null", "none", ""):
        return "otro"
    return INSTRUMENTOS_VALIDOS.get(k, "otro")


def limpiar_articulo(art: Optional[str]) -> Optional[str]:
    if not art:
        return None
    s = str(art).strip()
    if s.lower() in ("n/a", "no especificado", "no aplica", "ninguno", "null", "none", ""):
        return None
    s = re.sub(r"[^\d,\-]", "", s)
    s = re.sub(r",+", ",", s).strip(",-")
    return s or None


def _extraer_json(respuesta: str) -> dict:
    """Extrae el primer objeto JSON del texto de respuesta (tolerante)."""
    m = re.search(r"\{[\s\S]*\}", respuesta)
    if not m:
        raise ValueError("No se encontró JSON en la respuesta")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        # quita comas sobrantes antes de ] o }
        data = json.loads(re.sub(r",\s*([\]}])", r"\1", m.group(0)))
    # El modelo puede devolver "criterios" (nuevo) o "doctrinas" (legacy): unificar
    if "criterios" not in data and "doctrinas" in data:
        data["criterios"] = data.pop("doctrinas")
    return data


def validar_criterios(data: dict, texto: str) -> list[dict]:
    """Filtra criterios: solo los que tienen cita textual verificable en el documento."""
    validas = []
    for d in data.get("criterios", []) or []:
        if not isinstance(d, dict):
            continue
        ev = (d.get("parrafo_evidencia") or "").strip()
        if not ev or len(ev) < 40:
            continue
        if not evidencia_coincide(texto, ev):
            continue
        d["instrumento"] = normalizar_instrumento(d.get("instrumento"))
        d["articulo_numero"] = limpiar_articulo(d.get("articulo_numero"))
        d["tipo"] = d.get("tipo") if d.get("tipo") in TIPOS_VALIDOS else "interpretacion"
        validas.append(d)
    data["doctrinas"] = validas  # alias interno: la BD usa la tabla "doctrinas"
    data["criterios"] = validas
    return validas


# alias de compatibilidad (nombre anterior)
validar_doctrinas = validar_criterios


class AnalizadorIA:
    """Analiza sentencias usando un modelo remoto (Gemini API OpenAI-compatible
    o Cloudflare Workers AI, según qué key exista en el entorno)."""

    def __init__(self, cfg: Config, timeout: int = 120, reintentos: int = 12):
        cfg.requerir("ia")
        self.cfg = cfg
        self.timeout = timeout
        self.reintentos = reintentos
        # True cuando TODAS las keys del proveedor devuelven 429 de forma
        # persistente (RPD del día agotado en el plan gratuito): el batch debe
        # CORTAR la pasada y esperar al reset en vez de fallar doc por doc.
        self.rate_limited = False
        self._s = requests.Session()
        # Gemini gana si está presente su key; si no, Workers AI (fallback).
        if cfg.gemini_api_keys:
            self.proveedor = "gemini"
            # Rotación de keys gratis: cada key tiene ~230 RPD/día;
            # N keys => N×230 llamadas diarias antes del corte.
            self._keys = list(cfg.gemini_api_keys)
            self._key_idx = 0
            self._s.headers.update({
                "Authorization": f"Bearer {self._keys[0]}",
                "Content-Type": "application/json",
            })
        else:
            self.proveedor = "workers"
            self._s.headers.update({
                "Authorization": f"Bearer {cfg.cf_api_key}",
                "Content-Type": "application/json",
            })

    def _rotar_key(self) -> bool:
        """Cambia a la siguiente key Gemini. False si todas quedaron agotadas."""
        if self.proveedor != "gemini" or not self._keys:
            return False
        if self._key_idx + 1 < len(self._keys):
            self._key_idx += 1
            self._s.headers.update({
                "Authorization": f"Bearer {self._keys[self._key_idx]}"})
            print(f"      [key] rotando a key #{self._key_idx + 1} "
                  f"de {len(self._keys)}", flush=True)
            return True
        return False

    def _chat_url(self, modelo: Optional[str]) -> str:
        """Endpoint del proveedor activo."""
        if self.proveedor == "gemini":
            # OpenAI-compatible: https://ai.google.dev/gemini-api/docs/openai
            return f"{self.cfg.gemini_base_url}/chat/completions"
        # Workers AI: /ai/run nativo (techo ~38 req/min) en vez del
        # OpenAI-compat /ai/v1/chat/completions (techo ~3-5 req/min).
        m = modelo or self.cfg.modelo_analisis
        return f"{self.cfg.api_base_run()}/{m}"

    def _modelo(self, modelo: Optional[str]) -> str:
        """Nombre del modelo a usar según el proveedor activo."""
        if self.proveedor == "gemini":
            return (modelo or self.cfg.gemini_model).replace("@cf/", "")
        return modelo or self.cfg.modelo_analisis

    def analizar(self, texto: str, modelo: Optional[str] = None) -> Optional[dict]:
        """Devuelve {extracto, ratio_decidendi, doctrinas} validado, o None si falla."""
        prompt = construir_prompt(texto, max_chars=self.cfg.max_chars_texto)
        modelo_resuelto = self._modelo(modelo)
        body = {
            "model": modelo_resuelto,
            "messages": [
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 2048,
            "temperature": 0,
        }
        ultimo = None
        for i in range(self.reintentos):
            try:
                r = self._s.post(
                    self._chat_url(modelo), json=body, timeout=self.timeout
                )
                if r.status_code == 200:
                    j = r.json()
                    # Workers AI envuelve en {"result": {...}}; Gemini
                    # OpenAI-compat devuelve {"choices": [...]} al nivel raíz.
                    resultado = j.get("result") or j
                    try:
                        content = (resultado.get("choices") or [{}])[0] \
                            .get("message", {}).get("content") or ""
                    except (AttributeError, IndexError):
                        content = ""
                    if not content.strip():
                        # El modelo devuelve HTTP 200 con contenido vacío de
                        # forma ALEATORIA (~50% hoy, independiente de la carga):
                        # 2-3 intentos recuperan la mayoría. Los docs que fallan
                        # siempre (hard-fail) agotan el contador y quedan
                        # pendientes (el batch los detecta como estancamiento).
                        ultimo = "contenido vacío"
                        if i < 3:
                            time.sleep(4 + i * 4)
                            continue
                        return None
                    data = _extraer_json(content)
                    validar_doctrinas(data, texto)
                    return data
                ultimo = f"HTTP {r.status_code}: {r.text[:120]}"
                if r.status_code == 429:
                    # rate limit. Plan gratuito: ~230 RPD por key. Si hay más
                    # keys, rotar (cada key duplica el tope diario). Cuando
                    # todas están agotadas, cortar la pasada (rate_limited) y
                    # esperar al reset en vez de fallar doc por doc.
                    if self._rotar_key():
                        continue  # nueva key, reintentar de inmediato
                    self.rate_limited = True
                    return None
                if r.status_code in (500, 503):
                    if i >= 3:
                        return None
                    time.sleep(8 * (i + 1))
                    continue
                return None
            except ValueError as e:
                # Respuesta sin JSON parseable: no reintentar (la sentencia
                # queda pendiente y se reintenta en una pasada posterior).
                return None
            except Exception as e:  # noqa: BLE001
                # timeouts/errores de red: el modelo a veces se cuelga; 3
                # reintentos máx (si no, 40 min por doc en el peor caso)
                ultimo = f"{type(e).__name__}: {str(e)[:120]}"
                if i >= 3:
                    return None
                time.sleep(6 * (i + 1))
        print(f"      [IA falló] {ultimo}", flush=True)
        return None