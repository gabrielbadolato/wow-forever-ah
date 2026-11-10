import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import banco

# O cadastro do Forever ainda tem buracos; os itens que faltam quase sempre
# existem com o mesmo id nos namespaces do Classic e do Retail.
FONTES = ("static-forever-{r}", "static-classic1x-{r}", "static-classic-{r}", "static-{r}")
PARALELOS = 8
DIAS_PARA_TENTAR_DE_NOVO = 7


def _nome_do_icone(api, item_id, namespace):
    r = api.get(f"/data/wow/media/item/{item_id}", namespace=namespace)
    for asset in (r.dados or {}).get("assets", []):
        if asset.get("key") == "icon" and asset.get("value"):
            # .../icons/56/inv_misc_bag_08.jpg -> inv_misc_bag_08
            return asset["value"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return None


def _sem_links(valor):
    """Tira os links de navegação da API, que são metade do tamanho da resposta."""
    if isinstance(valor, dict):
        return {k: _sem_links(v) for k, v in valor.items() if k not in ("_links", "key")}
    if isinstance(valor, list):
        return [_sem_links(v) for v in valor]
    return valor


def buscar_item(api, item_id):
    """Procura o item em cada fonte, na ordem. Devolve um dict com fonte=None se não achar."""
    for modelo in FONTES:
        namespace = modelo.format(r=api.regiao)
        r = api.get(f"/data/wow/item/{item_id}", namespace=namespace)
        if r.status != 200:
            continue
        d = r.dados
        return {
            "item_id": item_id,
            "nome": d.get("name"),
            "qualidade": (d.get("quality") or {}).get("type"),
            "nivel": d.get("level"),
            "nivel_requerido": d.get("required_level"),
            "classe_id": (d.get("item_class") or {}).get("id"),
            "classe": (d.get("item_class") or {}).get("name"),
            "subclasse_id": (d.get("item_subclass") or {}).get("id"),
            "subclasse": (d.get("item_subclass") or {}).get("name"),
            "tipo_inventario": (d.get("inventory_type") or {}).get("type"),
            "empilhavel": int(bool(d.get("is_stackable"))),
            "preco_venda": d.get("sell_price"),
            "icone": _nome_do_icone(api, item_id, namespace),
            "fonte": namespace,
            "bruto": json.dumps(_sem_links(d), ensure_ascii=False, separators=(",", ":")),
        }
    return {"item_id": item_id, "fonte": None}


COLUNAS = ("item_id", "nome", "qualidade", "nivel", "nivel_requerido", "classe_id", "classe",
           "subclasse_id", "subclasse", "tipo_inventario", "empilhavel", "preco_venda",
           "icone", "fonte", "consultado_em", "bruto")


def pendentes(con, agora):
    """Itens com preço ou usados em receitas, sem cadastro completo (ou não achados há dias)."""
    limite = (agora - timedelta(days=DIAS_PARA_TENTAR_DE_NOVO)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [r[0] for r in con.execute(
        "SELECT p.item_id FROM (SELECT item_id FROM precos "
        "  UNION SELECT item_id FROM receitas WHERE item_id IS NOT NULL "
        "  UNION SELECT json_extract(j.value, '$[0]') FROM receitas, json_each(receitas.reagentes) j) p "
        "LEFT JOIN itens i ON i.item_id = p.item_id "
        "WHERE i.item_id IS NULL OR (i.fonte IS NULL AND i.consultado_em < ?) "
        "OR (i.fonte IS NOT NULL AND i.bruto IS NULL) ORDER BY 1",
        (limite,),
    )]


def catalogar(cfg, api, limite=None):
    con = banco.abrir(cfg.banco)
    agora = datetime.now(timezone.utc)
    ids = pendentes(con, agora)[:limite]
    carimbo = agora.strftime("%Y-%m-%dT%H:%M:%SZ")
    achados = 0
    with ThreadPoolExecutor(PARALELOS) as pool:
        for n, item in enumerate(pool.map(lambda i: buscar_item(api, i), ids), 1):
            item["consultado_em"] = carimbo
            achados += item["fonte"] is not None
            con.execute(
                f"INSERT OR REPLACE INTO itens ({', '.join(COLUNAS)}) "
                f"VALUES ({', '.join('?' * len(COLUNAS))})",
                [item.get(c) for c in COLUNAS],
            )
            if n % 200 == 0:
                con.commit()
    con.commit()
    con.close()
    print(f"catálogo: {len(ids)} itens consultados, {achados} achados, {len(ids) - achados} sem cadastro")
