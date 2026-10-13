"""Utilidades compartidas: encoding, extracción de texto y normalización
para la validación de citas textuales de la IA."""
from __future__ import annotations

import html as html_mod
import re
from typing import Optional


def normalizar_utf8(raw: bytes) -> bytes:
    """Convierte bytes (el sitio sirve windows-1252) a UTF-8."""
    try:
        return raw.decode("windows-1252").encode("utf-8")
    except Exception:  # noqa: BLE001
        pass
    try:
        return raw.decode("utf-8").encode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("windows-1252", errors="replace").encode("utf-8")


def html_a_texto(html: str) -> str:
    """Extrae texto plano de un HTML (fallback si no hay TXT limpio en R2)."""
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<head[\s\S]*?</head>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<!--[\s\S]*?-->", " ", html)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html_mod.unescape(html)
    html = re.sub(r"\s+", " ", html).strip()
    return html


def normalizar_para_match(t: str) -> str:
    """Normaliza texto para comparar citas textuales (comillas y espacios)."""
    t = re.sub(r"\s+", " ", t).strip()
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    t = re.sub(r"\s+([.,;:])", r"\1", t)
    return t.lower()


def evidencia_coincide(texto: str, evidencia: str, umbral: int = 40) -> bool:
    """True si la cita textual de la IA existe en el texto fuente (normalizado).

    Acepta coincidencia total, o prefijo/sufijo de 200 chars para citas largas
    que el modelo pudiera recortar en los extremos.
    """
    ev = (evidencia or "").strip()
    if not ev or len(ev) < umbral:
        return False
    t = normalizar_para_match(texto)
    e = normalizar_para_match(ev)
    if e in t:
        return True
    if len(e) > 200 and e[:200] in t:
        return True
    if len(e) > 200 and e[-200:] in t:
        return True
    return False


# ---- Validación de referencias a sentencias citadas -------------------------
# Una doctrina puede citar OTRA sentencia del TSJ ("Sentencia Nº 397 de
# 21-06-2005, Sala de Casación Penal, ponente X"). La referencia la escribe la
# IA y DEBE verificarse contra el texto fuente: no basta substring rígido
# (la IA omite naturalmente "por la", "del Tribunal Supremo de Justicia"...)
# ni palabras sueltas (aceptaría inventos). Se validan COMPONENTES: cada dato
# que la referencia afirma (número, fecha, sala, ponente) debe existir en el
# texto. Si la referencia afirma un dato que NO está en el texto -> se rechaza.

RE_REF_NUM = re.compile(
    r"(?:sentencia\s*)?(?:n\s*[º°o]?|nro\.?|número|num\.?)\s*\.?\s*(\d{1,4})",
    re.I)
RE_REF_FECHA = re.compile(
    r"\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}-\d{1,2}-\d{4}|"
    r"\d{1,2}\s+de\s+[a-záéíóúñ]+\s+de\s+\d{4}|"
    r"(?<!\d)(18\d{2}|19\d{2}|20\d{2})(?!\d)", re.I)
RE_REF_SALA = re.compile(
    r"Sala\s+(?:de\s+)?(?:Casación Penal|Constitucional|Político[-\s]?Administrativa|"
    r"Electoral|Social|Civil|Plena)", re.I)
RE_REF_PONENTE = re.compile(
    r"(?:ponente|ponencia\s+de)\s+(?:del?\s+magistrad[oa]|de\s+la\s+magistrada|"
    r"de\s+la\s+magistrado)?\s*([A-ZÁÉÍÓÚÑ][\wáéíóúñÁÉÍÓÚÑ]*(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñÁÉÍÓÚÑ]*){0,3})",
    re.I)


def referencia_coincide(texto: str, referencia: str) -> bool:
    """True si TODOS los componentes de la referencia citada están en el texto.

    Componentes: número de sentencia, fecha, sala, ponente. Si la referencia
    no declara ningún componente, se rechaza (inventada). Cada componente
    declarado DEBE existir en el texto fuente (normalizado); además, cuando la
    referencia tiene número + otros componentes, estos deben estar en la MISMA
    ventana del texto (cerca del número) para evitar mezclas: atribuir a la
    sentencia Nº 138 la sala o fecha de otra sentencia citada en el mismo
    documento.
    """
    ref = (referencia or "").strip()
    if not ref:
        return False
    t = normalizar_para_match(texto)
    r = normalizar_para_match(ref)

    nums = RE_REF_NUM.findall(r)
    fechas = RE_REF_FECHA.findall(r)
    salas = RE_REF_SALA.findall(r)
    ponentes = RE_REF_PONENTE.findall(r)

    # Referencia sin ningún dato verificable -> no se puede confiar.
    if not (nums or fechas or salas or ponentes):
        return False

    def _esta(s: str) -> bool:
        return normalizar_para_match(s) in t

    # Fechas y ponentes sueltos: deben estar en el texto.
    for f in fechas:
        if not _esta(f):
            return False
    for p in ponentes:
        palabras = p.split()
        if len(palabras) >= 2 and not _esta(p):
            return False

    # Números: deben existir en el texto.
    posiciones_num = []  # (numero, lista de posiciones en t)
    for n in nums:
        pats = [f"nº {n}", f"n° {n}", f"nro. {n}", f"nro {n}",
                f"sentencia nº {n}", f"sentencia n° {n}",
                f"sentencia nro. {n}", f"sentencia {n}"]
        pos = []
        for pat in pats:
            start = 0
            while True:
                i = t.find(pat, start)
                if i < 0:
                    break
                pos.append(i)
                start = i + 1
        if not pos:
            return False
        posiciones_num.append((n, pos))

    # Ventana contextual: si hay sala y/o fecha, deben estar CERCA de alguna
    # aparición del número (misma cita), no en otra parte del documento.
    if salas or fechas:
        ventana = 400
        for n, posiciones in posiciones_num:
            ok_num = False
            for i in posiciones:
                ini, fin = max(0, i - ventana), min(len(t), i + ventana)
                trozo = t[ini:fin]
                if all(_esta_en(trozo, s) for s in salas) and \
                   all(_esta_en(trozo, f) for f in fechas):
                    ok_num = True
                    break
            if not ok_num:
                return False
    return True


def _esta_en(trozo: str, s: str) -> bool:
    return normalizar_para_match(s) in trozo


RE_FECHA_DDMMYYYY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
RE_FECHA_LARGA = re.compile(r"^(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})$", re.I)
RE_FECHA_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}
MESES_INV = {v: k for k, v in MESES.items()}


def fecha_a_iso(fecha: str) -> Optional[str]:
    """Normaliza fechas a ISO: '30/01/2024', '20 de enero de 2004' o '2024-01-30'."""
    if not fecha:
        return None
    f = fecha.strip()
    m = RE_FECHA_DDMMYYYY.match(f)
    if m:
        d, mes, anio = m.groups()
        return f"{anio}-{int(mes):02d}-{int(d):02d}"
    m = RE_FECHA_ISO.match(f)
    if m:
        return f
    m = RE_FECHA_LARGA.match(f)
    if m:
        dia, mes, anio = m.group(1), m.group(2).lower(), m.group(3)
        if mes not in MESES:
            return None
        return f"{anio}-{MESES[mes]:02d}-{int(dia):02d}"
    return None


def fecha_larga(iso: str) -> Optional[str]:
    """'2024-01-30' -> '30 de enero de 2024' (formato usado por la app)."""
    m = RE_FECHA_ISO.match(iso or "")
    if not m:
        return None
    anio, mes, dia = m.groups()
    mes_nombre = MESES_INV.get(int(mes))
    if not mes_nombre:
        return None
    return f"{int(dia)} de {mes_nombre} de {anio}"


# --- Mojibake: texto UTF-8 cuyos bytes se leyeron como cp1252 ("MÃ¡rmol" -> "Mármol") ---
# 'Ã'(U+00C3) / 'Â'(U+00C2) / 'â'(U+00E2) seguidos de U+0080..U+00BF son bytes UTF-8
# mal decodificados. La reparación es el round-trip: cp1252.encode -> utf-8.decode.
RE_MOJIBAKE = re.compile(r"([\u00c2\u00c3\u00e2])([\u0080-\u00bf])")


def detectar_mojibake(texto: str) -> int:
    """Número de secuencias mojibake en `texto` (0 = limpio)."""
    if not texto:
        return 0
    return len(RE_MOJIBAKE.findall(texto))


def reparar_mojibake(texto: str) -> str:
    """Corrige secuencias mojibake (UTF-8 leído como cp1252) de forma dirigida."""
    if not texto or not RE_MOJIBAKE.search(texto):
        return texto

    def _rep(m: re.Match) -> str:
        try:
            return (m.group(1) + m.group(2)).encode("cp1252").decode("utf-8")
        except UnicodeDecodeError:
            return m.group(0)  # no era mojibake válido; se deja intacto

    return RE_MOJIBAKE.sub(_rep, texto)