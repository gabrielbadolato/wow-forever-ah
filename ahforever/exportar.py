import json
import os
from datetime import datetime, timedelta, timezone

from . import banco, bonus, compactar
from .compactar import DIAS_POR_HORA, SERIE


def _gravar(caminho, dados):
    # Grava num temporário e troca, para a página nunca ler um arquivo pela metade.
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")))
    os.replace(temporario, caminho)


def _catalogo(con):
    classes, itens = {}, {}
    for (item_id, nome, qualidade, nivel, requerido, classe_id, classe, subclasse_id,
         subclasse, inventario, icone, fonte, preco_venda) in con.execute(
            "SELECT item_id, nome, qualidade, nivel, nivel_requerido, classe_id, classe, "
            "subclasse_id, subclasse, tipo_inventario, icone, fonte, preco_venda FROM itens "
            "WHERE fonte IS NOT NULL"):
        if classe_id is not None:
            c = classes.setdefault(classe_id, {"nome": classe, "sub": {}})
            if subclasse_id is not None:
                c["sub"][subclasse_id] = subclasse
        itens[item_id] = [nome, qualidade, nivel, requerido, classe_id, subclasse_id,
                          inventario, icone, fonte.removeprefix("static-"), preco_venda]
    return {"classes": classes, "itens": itens}


def _casa(con, ruleset_id, casa_id):
    """Linhas da foto mais recente de itens e de commodities da casa."""
    linhas, coletado_em = [], None
    for tipo in ("itens", "commodities"):
        coleta = con.execute(
            "SELECT id, coletado_em FROM coletas WHERE ruleset_id = ? AND casa_id = ? "
            "AND tipo = ? ORDER BY id DESC LIMIT 1", (ruleset_id, casa_id, tipo)).fetchone()
        if not coleta:
            continue
        coletado_em = max(coletado_em or "", coleta[1])
        linhas += [list(l) for l in con.execute(
            "SELECT item_id, variante, preco_min, preco_mercado, quantidade, n_anuncios "
            "FROM precos WHERE coleta_id = ?", (coleta[0],))]
    return coletado_em, linhas


def _texto(valor):
    """Texto de exibição de um campo do tooltip, venha ele como string ou como objeto."""
    if isinstance(valor, str):
        return valor
    if isinstance(valor, dict):
        return valor.get("display_string") or _texto(valor.get("display")) or valor.get("name")
    return None


def linhas_do_tooltip(previa):
    """Linhas do tooltip na ordem do jogo: [texto, classe] ou [texto, classe, texto à direita].

    Classes: "" branco, "v" verde (efeitos), "a" amarelo, "c" cinza, "z" azul.
    """
    linhas = []

    def linha(texto, classe="", direita=None):
        if texto:
            linhas.append([texto, classe] + ([direita] if direita else []))

    linha(_texto(previa.get("name_description")), "z")
    linha(_texto(previa.get("level")), "a")
    linha(_texto(previa.get("binding")))
    linha(_texto(previa.get("unique_equipped")))
    linha(_texto(previa.get("item_starts_quest")))
    tipo = previa.get("inventory_type") or {}
    if tipo.get("type") not in (None, "NON_EQUIP"):
        subclasse = None if previa.get("is_subclass_hidden") else _texto(previa.get("item_subclass"))
        linha(tipo.get("name"), "", subclasse)
    arma = previa.get("weapon") or {}
    linha(_texto(arma.get("damage")), "", _texto(arma.get("attack_speed")))
    linha(_texto(arma.get("dps")))
    linha(_texto(previa.get("armor")))
    linha(_texto(previa.get("shield_block")))
    for atributo in previa.get("stats") or []:
        linha(_texto(atributo), "v" if atributo.get("is_equip_bonus") else "")
    linha(_texto(previa.get("container_slots")))
    linha(_texto(previa.get("durability")))
    for requisito in (previa.get("requirements") or {}).values():
        linha(_texto(requisito))
    for efeito in previa.get("spells") or []:
        linha(efeito.get("description"), "v")
    linha(_texto(previa.get("charges")))
    conjunto = previa.get("set") or {}
    linha(_texto(conjunto.get("item_set")), "a")
    linha(_texto(previa.get("crafting_reagent")), "z")
    linha(_texto(previa.get("toy")), "z")
    if previa.get("description"):
        linha(f'"{previa["description"]}"', "a")
    linha((previa.get("recipe") or {}).get("reagents_display_string"))
    venda = (previa.get("sell_price") or {}).get("value")
    if venda:
        moedas = [(venda // 10000, "g"), (venda // 100 % 100, "s"), (venda % 100, "c")]
        linha("Sell Price: " + " ".join(f"{n}{m}" for n, m in moedas if n), "c")
    return linhas


def _tooltips(con):
    tooltips = {}
    for item_id, bruto in con.execute("SELECT item_id, bruto FROM itens WHERE bruto IS NOT NULL"):
        item = json.loads(bruto)
        # Itens vindos do Wowhead já chegam com as linhas prontas.
        tooltips[item_id] = item.get("linhas") or linhas_do_tooltip(item.get("preview_item") or {})
    return tooltips


# Reagentes vendidos por NPC a preço fixo (lista do Classic; o Forever pode ter mudado alguma coisa).
# Linhas, tintas, fundentes, frascos, temperos e afins.
VENDIDOS_POR_NPC = (
    2320, 2321, 4291, 8343, 14341, 2604, 2605, 4340, 4341, 4342, 6260, 6261, 10290, 2324, 2325,
    2880, 3466, 3857, 3371, 3372, 8925, 18256, 2678, 2692, 3713, 4289, 4399, 4400, 4470, 159,
    1179, 6217, 11291, 10647, 10648, 6530, 4536,
)


def _receitas(con):
    """Receitas que produzem item, mais o preço unitário dos reagentes de NPC."""
    profissoes, receitas = {}, []
    for receita_id, prof_id, profissao, categoria, nome, item_id, quantidade, reagentes in con.execute(
            "SELECT receita_id, profissao_id, profissao, categoria, nome, item_id, quantidade, reagentes "
            "FROM receitas WHERE item_id IS NOT NULL ORDER BY profissao, categoria, nome"):
        profissoes[prof_id] = profissao
        receitas.append([receita_id, prof_id, categoria, nome, item_id, quantidade, json.loads(reagentes)])
    npc = {}
    marcas = ",".join("?" * len(VENDIDOS_POR_NPC))
    for item_id, bruto in con.execute(
            f"SELECT item_id, bruto FROM itens WHERE bruto IS NOT NULL AND item_id IN ({marcas})",
            VENDIDOS_POR_NPC):
        item = json.loads(bruto)
        # Alguns (frascos) são vendidos em lotes: o preço da API é o do lote.
        if item.get("purchase_price"):
            npc[item_id] = round(item["purchase_price"] / (item.get("purchase_quantity") or 1))
    return {"profissoes": profissoes, "receitas": receitas, "npc": npc}


def _vendas(con, ruleset_id, casa_id, agora):
    """Vendas estimadas dos últimos 7 dias por item, e quantas horas de observação há por trás delas."""
    desde = (agora - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    casa = (ruleset_id, casa_id, desde)
    segundos = con.execute(
        "SELECT SUM(intervalo_vendas) FROM coletas WHERE ruleset_id = ? AND casa_id = ? "
        "AND coletado_em >= ? AND tipo = 'itens'", casa).fetchone()[0] or 0
    itens = dict(con.execute(
        "SELECT v.item_id, SUM(v.quantidade) FROM vendas v JOIN coletas c ON c.id = v.coleta_id "
        "WHERE c.ruleset_id = ? AND c.casa_id = ? AND c.coletado_em >= ? GROUP BY 1", casa))
    return {"horas": round(segundos / 3600, 1), "itens": itens}


def _dia_por_hora(con, ruleset_id, casa_id, dia):
    """Pontos de um dia (UTC): {"t": [fotos], "itens": {id: [[mínimos], [quantidades]]}}."""
    seguinte = (datetime.fromisoformat(dia) + timedelta(days=1)).strftime("%Y-%m-%d")
    linhas = con.execute(SERIE, (ruleset_id, casa_id, dia, seguinte)).fetchall()
    fotos = sorted({l[0] for l in linhas})
    posicao = {t: i for i, t in enumerate(fotos)}
    itens = {}
    for quando, item_id, minimo, quantidade in linhas:
        serie = itens.setdefault(item_id, [[None] * len(fotos), [None] * len(fotos)])
        serie[0][posicao[quando]] = minimo
        serie[1][posicao[quando]] = quantidade
    return {"t": fotos, "itens": itens}


def _mes_por_dia(con, ruleset_id, casa_id, dias):
    """Resumo diário: {"dias": [...], "itens": {id: [[mínimo], [média dos mínimos], [quantidade média]]}}."""
    posicao = {d: i for i, d in enumerate(dias)}
    itens = {}
    for dia, item_id, minimo, media, quantidade in con.execute(
            "SELECT dia, item_id, minimo, media, quantidade FROM diario WHERE ruleset_id = ? "
            "AND casa_id = ? AND dia >= ? AND dia <= ? AND minimo IS NOT NULL",
            (ruleset_id, casa_id, dias[0], dias[-1])):
        if dia not in posicao:
            continue
        serie = itens.setdefault(item_id, [[None] * len(dias) for _ in range(3)])
        for i, valor in enumerate((minimo, media, quantidade)):
            serie[i][posicao[dia]] = valor
    return {"dias": dias, "itens": itens}


def _historico(con, destino, ruleset_id, casa_id, hoje):
    """Atualiza <destino>/hist/<ruleset>-<casa>/ e devolve os dias e meses disponíveis."""
    pasta = destino / "hist" / f"{ruleset_id}-{casa_id}"
    pasta.mkdir(parents=True, exist_ok=True)
    dias = [r[0] for r in con.execute(
        "SELECT DISTINCT substr(coletado_em, 1, 10) FROM coletas "
        "WHERE ruleset_id = ? AND casa_id = ? AND n_anuncios > 0 ORDER BY 1", (ruleset_id, casa_id))]
    ontem = (datetime.fromisoformat(hoje) - timedelta(days=1)).strftime("%Y-%m-%d")

    # Hoje e ontem mudam (o dia fecha depois da última foto); os outros já estão prontos.
    # O detalhe por foto dos dias mais antigos já foi apagado pela compactação.
    limite = (datetime.fromisoformat(hoje) - timedelta(days=DIAS_POR_HORA)).strftime("%Y-%m-%d")
    recentes = [d for d in dias[-DIAS_POR_HORA:] if d >= limite]
    for dia in recentes:
        arquivo = pasta / f"h-{dia}.json"
        if dia >= ontem or not arquivo.exists():
            _gravar(arquivo, _dia_por_hora(con, ruleset_id, casa_id, dia))
    for arquivo in pasta.glob("h-*.json"):
        if arquivo.stem[2:] not in recentes:
            arquivo.unlink()

    fechados = [d for d in dias if d < hoje]
    meses = sorted({d[:7] for d in fechados})
    for mes in meses:
        do_mes = [d for d in fechados if d.startswith(mes)]
        arquivo = pasta / f"d-{mes}.json"
        if arquivo.exists() and json.loads(arquivo.read_text()).get("dias") == do_mes:
            continue
        _gravar(arquivo, _mes_por_dia(con, ruleset_id, casa_id, do_mes))
    return recentes, meses


def exportar(cfg):
    """Gera em <pasta_site>/dados os JSON que a página lê."""
    destino = cfg.pasta_site / "dados"
    destino.mkdir(parents=True, exist_ok=True)
    con = banco.abrir(cfg.banco)
    agora = datetime.now(timezone.utc)
    # Antes de tudo: o histórico mensal sai do resumo diário que a compactação mantém.
    resumidos, esvaziadas = compactar.compactar(con, agora.strftime("%Y-%m-%d"))
    if resumidos or esvaziadas:
        print(f"banco: {resumidos} dias resumidos, {esvaziadas} fotos antigas esvaziadas")
    _gravar(destino / "itens.json", _catalogo(con))
    _gravar(destino / "tooltips.json", _tooltips(con))
    _gravar(destino / "receitas.json", _receitas(con))
    if bonus.caminho(cfg).exists():
        _gravar(destino / "bonus.json", json.loads(bonus.caminho(cfg).read_text()))

    casas, vistas = [], {}
    for ruleset_id, casa_id, nome in con.execute(
            "SELECT ruleset_id, casa_id, nome FROM casas ORDER BY 1, 2").fetchall():
        # A API não diz quais casas formam um mesmo mercado; a n-ésima de cada facção
        # (Aliança, Horda, neutra) parece ser do mesmo, pelo tamanho. Conta as vazias também.
        grupo = vistas[ruleset_id, nome] = vistas.get((ruleset_id, nome), 0) + 1
        coletado_em, linhas = _casa(con, ruleset_id, casa_id)
        if not linhas:
            continue
        arquivo = f"casa-{ruleset_id}-{casa_id}.json"
        _gravar(destino / arquivo, {"coletado_em": coletado_em, "linhas": linhas,
                                    "vendas": _vendas(con, ruleset_id, casa_id, agora)})
        horas, meses = _historico(con, destino, ruleset_id, casa_id, agora.strftime("%Y-%m-%d"))
        casas.append({"ruleset": ruleset_id, "id": casa_id, "nome": nome, "grupo": grupo,
                      "coletado_em": coletado_em, "itens": len(linhas), "arquivo": arquivo,
                      "horas": horas, "meses": meses})
    con.close()

    _gravar(destino / "meta.json", {
        "gerado_em": agora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "casa_padrao": cfg.casa_padrao,
        "casas": casas,
    })
    print(f"site: {len(casas)} casas exportadas para {destino}")
