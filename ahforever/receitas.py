import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import banco
from .catalogo import PARALELOS

DIAS_ENTRE_VARREDURAS = 7


def listar(api, namespace):
    """Percorre profissão > nível > categoria e devolve (prof_id, profissão, categoria, receita_id, nome)."""
    indice = api.get("/data/wow/profession/index", namespace=namespace).dados or {}
    for prof in indice.get("professions", []):
        # "[DNT]" marca dados de teste da Blizzard.
        if "[DNT]" in prof["name"]:
            continue
        detalhe = api.get(f"/data/wow/profession/{prof['id']}", namespace=namespace).dados or {}
        for nivel in detalhe.get("skill_tiers") or []:
            caminho = f"/data/wow/profession/{prof['id']}/skill-tier/{nivel['id']}"
            for categoria in (api.get(caminho, namespace=namespace).dados or {}).get("categories") or []:
                for receita in categoria.get("recipes") or []:
                    yield prof["id"], prof["name"], categoria.get("name"), receita["id"], receita["name"]


def _quantidade(feita):
    """Unidades por fabricação; quando a API dá uma faixa, usa a média."""
    feita = feita or {}
    if "value" in feita:
        return feita["value"]
    if "minimum" in feita and "maximum" in feita:
        return (feita["minimum"] + feita["maximum"]) / 2
    return 1


def buscar_receita(api, namespace, receita_id):
    d = api.get(f"/data/wow/recipe/{receita_id}", namespace=namespace).dados or {}
    reagentes = [[r["reagent"]["id"], r.get("quantity", 1)] for r in d.get("reagents") or []]
    # Encantamentos e afins não produzem item: ficam com item_id vazio.
    return (d.get("crafted_item") or {}).get("id"), _quantidade(d.get("crafted_quantity")), reagentes


def baixar(cfg, api, forcar=False):
    """Guarda as receitas que ainda não estão no banco. Sem `forcar`, só varre a API de tempos em tempos."""
    con = banco.abrir(cfg.banco)
    agora = datetime.now(timezone.utc)
    ultima = con.execute("SELECT MAX(consultado_em) FROM receitas").fetchone()[0]
    limite = (agora - timedelta(days=DIAS_ENTRE_VARREDURAS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if ultima and ultima > limite and not forcar:
        con.close()
        return
    namespace = f"static-forever-{api.regiao}"
    conhecidas = {r[0] for r in con.execute("SELECT receita_id FROM receitas")}
    novas = [r for r in listar(api, namespace) if r[3] not in conhecidas]
    carimbo = agora.strftime("%Y-%m-%dT%H:%M:%SZ")
    with ThreadPoolExecutor(PARALELOS) as pool:
        detalhes = pool.map(lambda r: buscar_receita(api, namespace, r[3]), novas)
        for (prof_id, profissao, categoria, receita_id, nome), (item_id, quantidade, reagentes) in zip(novas, detalhes):
            con.execute(
                "INSERT OR REPLACE INTO receitas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (receita_id, prof_id, profissao, categoria, nome, item_id, quantidade,
                 json.dumps(reagentes), carimbo))
    # Marca a varredura mesmo sem receita nova, para não repetir a cada coleta.
    con.execute("UPDATE receitas SET consultado_em = ? WHERE receita_id = "
                "(SELECT MAX(receita_id) FROM receitas)", (carimbo,))
    con.commit()
    con.close()
    print(f"receitas: {len(novas)} novas, {len(conhecidas) + len(novas)} no total")
