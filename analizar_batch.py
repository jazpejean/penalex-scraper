"""analizar_batch v5 (por año, con exclusión de fallados): procesa todos los
pendientes de un rango de años (sala única). Por cada año, itera: trae hasta
`limite` pendientes NUEVOS (excluyendo los que ya fallaron en esta ejecución),
los intenta (≤4 intentos de IA por doc — la flakiness del modelo es aleatoria)
y acumula los fallados en un set que se excluye en la siguiente iteración.
Así cada iteración procesa docs distintos y el año converge; los hard-fail
(residuales) quedan pendientes para una ejecución futura.

Uso: python analizar_batch.py <sala> <anio_ini> <anio_fin> [limite]
"""
import sys
import time

sys.path.insert(0, ".")

from penalex.analizar import analizar_pendientes


def main():
    sala, ini, fin = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    limite = int(sys.argv[4]) if len(sys.argv) > 4 else 300
    max_iter_por_anio = int(sys.argv[5]) if len(sys.argv) > 5 else 40
    total_ok = total_fall = 0
    falladas_global = set()
    for anio in range(ini, fin + 1):
        falladas = set()
        for it in range(1, max_iter_por_anio + 1):
            try:
                res = analizar_pendientes(sala=sala, anio=anio, limite=limite,
                                          vectorizar=True, excluir=falladas)
            except Exception as e:
                print(f"  excepción {type(e).__name__}: {str(e)[:80]} — sigo",
                      flush=True)
                time.sleep(15)
                continue
            ok = res.get("ok", 0)
            tram = res.get("tramite", 0)
            fall = res.get("ia_falló", 0) + res.get("sin_txt", 0)
            total_ok += ok
            total_fall += fall
            falladas |= set(res.get("ids_falladas", []))
            falladas_global |= falladas
            print(f"  {sala}/{anio} iter {it}: ok={ok} trámite={tram} "
                  f"fallos={fall} "
                  f"pendientes_nuevos={not res.get('sin_pendientes')}",
                  flush=True)
            if res.get("rate_limited"):
                # RPD diario del plan gratuito agotado: no tiene sentido seguir
                # con el resto de años (todos fallarían igual). Salir; el
                # launcher duerme y reintenta tras el reset.
                print(f"  ⛔ RPD agotado en {sala}/{anio} — "
                      f"saliendo para esperar el reset", flush=True)
                break
            if res.get("sin_pendientes") or (ok == 0 and fall == 0):
                print(f"  {sala}/{anio}: sin pendientes nuevos — "
                      f"{len(falladas)} fallados quedan para otro run",
                      flush=True)
                break
        if res.get("rate_limited"):
            break  # no seguir con más años; esperar al reset
        # salir pronto si el año no tenía nada
    print(f"ANALIZAR_DONE {sala} {ini}-{fin}: ok={total_ok} fallos={total_fall}",
          flush=True)


if __name__ == "__main__":
    main()