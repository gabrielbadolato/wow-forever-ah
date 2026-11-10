import json
import os
from datetime import datetime, timedelta, timezone

from . import addon, banco, bonus, compactar
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


# Cada ruleset do jogo (PvP, Normal, Roleplay, Roleplay PvP) tem a sua economia: três casas
# (Aliança, Horda, neutra), e a API devolve as 15 seja qual for o ruleset pedido.
# Confirmado em 10/11/2026: na Coreia e em Taiwan, que só têm PvP e Normal, só os grupos 1 e 2
# têm anúncios; nos EUA e na Europa, com quatro rulesets, têm os grupos 1, 2, 4 e 5. O grupo 3
# (112/115/118) está vazio em todas as regiões; os textos do jogo citam um ruleset Hardcore.
# Qual grupo é qual ruleset é inferência: segue a ordem dos ids dos rulesets, na Coreia e em
# Taiwan o grupo 1 é várias vezes maior (lá o PvP sempre dominou) e a Horda é maioria nos
# grupos 1 e 5 e a Aliança nos grupos 2 e 4, como é costume em PvP e em PvE.
MERCADOS = {1: "PvP", 2: "Normal", 3: "Sem uso", 4: "Roleplay", 5: "Roleplay PvP"}

# Os tooltips vão em vários arquivos, para a página baixar só o pedaço do item apontado.
PARTES_TOOLTIPS = 16


def marcar_checagem(cfg, agora=None):
    """Grava a hora em que a coleta rodou pela última vez, tenha ou não achado foto nova."""
    destino = cfg.pasta_site / "dados"
    destino.mkdir(parents=True, exist_ok=True)
    agora = agora or datetime.now(timezone.utc)
    _gravar(destino / "estado.json", {"checado_em": agora.strftime("%Y-%m-%dT%H:%M:%SZ")})


def _casa(con, ruleset_id, casa_id):
    """Linhas da foto mais recente de itens e de commodities da casa, e os degraus de preço à parte.

    Os degraus ({"item|variante": [[n, custo], ...]}) pesam quase tanto quanto as linhas e só
    servem a algumas abas, então vão num arquivo que a página baixa quando precisa.
    """
    linhas, degraus_da_casa, coletado_em = [], {}, None
    for tipo in ("itens", "commodities"):
        coleta = con.execute(
            "SELECT id, coletado_em FROM coletas WHERE ruleset_id = ? AND casa_id = ? "
            "AND tipo = ? ORDER BY id DESC LIMIT 1", (ruleset_id, casa_id, tipo)).fetchone()
        if not coleta:
            continue
        coletado_em = max(coletado_em or "", coleta[1])
        for *linha, degraus in con.execute(
                "SELECT p.item_id, p.variante, p.preco_min, p.preco_mercado, p.quantidade, p.n_anuncios, "
                "d.degraus FROM precos p LEFT JOIN degraus d ON d.ruleset_id = ? AND d.casa_id = ? "
                "AND d.tipo = ? AND d.item_id = p.item_id AND d.variante = p.variante "
                "WHERE p.coleta_id = ?", (ruleset_id, casa_id, tipo, coleta[0])):
            linhas.append(linha)
            if degraus:
                degraus_da_casa[f"{linha[0]}|{linha[1]}"] = json.loads(degraus)
    return coletado_em, linhas, degraus_da_casa


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
    itens, valores = {}, {}
    for item_id, quantidade, valor in con.execute(
            "SELECT v.item_id, SUM(v.quantidade), SUM(v.valor) FROM vendas v JOIN coletas c ON c.id = v.coleta_id "
            "WHERE c.ruleset_id = ? AND c.casa_id = ? AND c.coletado_em >= ? GROUP BY 1", casa):
        itens[item_id], valores[item_id] = quantidade, valor
    # valores: cobre que essas vendas somaram; dividido pela quantidade dá o preço a que o item vende.
    return {"horas": round(segundos / 3600, 1), "itens": itens, "valores": valores}


# A média só vale com um mínimo de fotos; antes disso um item novo pareceria sempre em promoção.
FOTOS_PARA_MEDIA = 6


def _medias(con, ruleset_id, casa_id, agora):
    """Média do menor preço de cada item nas fotos dos últimos 7 dias: {item: cobre}."""
    desde = (agora - timedelta(days=DIAS_POR_HORA)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return dict(con.execute(
        f"SELECT item_id, CAST(AVG(minimo) AS INTEGER) FROM ({SERIE}) WHERE minimo IS NOT NULL "
        "GROUP BY item_id HAVING COUNT(*) >= ?", (ruleset_id, casa_id, desde, "9999", FOTOS_PARA_MEDIA)))


# Muda quando o formato dos arquivos de histórico muda, para os já gravados serem refeitos.
VERSAO_HIST = 2


def _dia_por_hora(con, ruleset_id, casa_id, dia):
    """Pontos de um dia (UTC): {"t": [fotos], "itens": {id: [[mínimos], [quantidades]]},
    "vendas": {id: [[posição da foto, unidades vendidas desde a foto anterior], ...]}}."""
    seguinte = (datetime.fromisoformat(dia) + timedelta(days=1)).strftime("%Y-%m-%d")
    linhas = con.execute(SERIE, (ruleset_id, casa_id, dia, seguinte)).fetchall()
    fotos = sorted({l[0] for l in linhas})
    posicao = {t: i for i, t in enumerate(fotos)}
    itens = {}
    for quando, item_id, minimo, quantidade in linhas:
        serie = itens.setdefault(item_id, [[None] * len(fotos), [None] * len(fotos)])
        serie[0][posicao[quando]] = minimo
        serie[1][posicao[quando]] = quantidade
    vendas = {}
    for quando, item_id, vendido in con.execute(
            "SELECT c.coletado_em, v.item_id, SUM(v.quantidade) FROM vendas v "
            "JOIN coletas c ON c.id = v.coleta_id WHERE c.ruleset_id = ? AND c.casa_id = ? "
            "AND c.coletado_em >= ? AND c.coletado_em < ? GROUP BY 1, 2 ORDER BY 2, 1",
            (ruleset_id, casa_id, dia, seguinte)):
        if quando in posicao:
            vendas.setdefault(item_id, []).append([posicao[quando], vendido])
    return {"v": VERSAO_HIST, "t": fotos, "itens": itens, "vendas": vendas}


def _mes_por_dia(con, ruleset_id, casa_id, dias):
    """Resumo diário: {"dias": [...], "itens": {id: [[mínimo], [média dos mínimos], [quantidade média], [vendido]]}}."""
    posicao = {d: i for i, d in enumerate(dias)}
    itens = {}
    for dia, item_id, minimo, media, quantidade, vendido in con.execute(
            "SELECT dia, item_id, minimo, media, quantidade, vendido FROM diario WHERE ruleset_id = ? "
            "AND casa_id = ? AND dia >= ? AND dia <= ? AND minimo IS NOT NULL",
            (ruleset_id, casa_id, dias[0], dias[-1])):
        if dia not in posicao:
            continue
        serie = itens.setdefault(item_id, [[None] * len(dias) for _ in range(4)])
        for i, valor in enumerate((minimo, media, quantidade, vendido)):
            serie[i][posicao[dia]] = valor
    return {"v": VERSAO_HIST, "dias": dias, "itens": itens}


def _atual(arquivo, dias=None):
    """O arquivo existe, está no formato de hoje e (resumo mensal) cobre estes dias."""
    if not arquivo.exists():
        return False
    dados = json.loads(arquivo.read_text())
    return dados.get("v") == VERSAO_HIST and (dias is None or dados.get("dias") == dias)


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
        if dia >= ontem or not _atual(arquivo):
            _gravar(arquivo, _dia_por_hora(con, ruleset_id, casa_id, dia))
    for arquivo in pasta.glob("h-*.json"):
        if arquivo.stem[2:] not in recentes:
            arquivo.unlink()

    fechados = [d for d in dias if d < hoje]
    meses = sorted({d[:7] for d in fechados})
    for mes in meses:
        do_mes = [d for d in fechados if d.startswith(mes)]
        arquivo = pasta / f"d-{mes}.json"
        if _atual(arquivo, do_mes):
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
    tooltips = _tooltips(con)
    for parte in range(PARTES_TOOLTIPS):
        _gravar(destino / f"tooltips-{parte}.json",
                {i: linhas for i, linhas in tooltips.items() if i % PARTES_TOOLTIPS == parte})
    (destino / "tooltips.json").unlink(missing_ok=True)
    _gravar(destino / "nomes-pt.json", dict(con.execute(
        "SELECT item_id, nome_pt FROM itens WHERE nome_pt IS NOT NULL AND nome_pt != ''")))
    _gravar(destino / "receitas.json", _receitas(con))
    if bonus.caminho(cfg).exists():
        _gravar(destino / "bonus.json", json.loads(bonus.caminho(cfg).read_text()))

    casas, vistas, zip_do_addon = [], {}, None
    for ruleset_id, casa_id, nome in con.execute(
            "SELECT ruleset_id, casa_id, nome FROM casas ORDER BY 1, 2").fetchall():
        # A n-ésima casa de cada facção (Aliança, Horda, neutra) pertence ao mesmo ruleset;
        # ver MERCADOS. Conta as vazias também.
        grupo = vistas[ruleset_id, nome] = vistas.get((ruleset_id, nome), 0) + 1
        coletado_em, linhas, degraus = _casa(con, ruleset_id, casa_id)
        if not linhas:
            continue
        arquivo = f"casa-{ruleset_id}-{casa_id}.json"
        vendas = _vendas(con, ruleset_id, casa_id, agora)
        _gravar(destino / arquivo, {"coletado_em": coletado_em, "linhas": linhas, "vendas": vendas,
                                    "medias": _medias(con, ruleset_id, casa_id, agora)})
        if casa_id == cfg.casa_padrao and zip_do_addon is None:
            zip_do_addon = addon.gerar(cfg.pasta_site, f"{nome} {casa_id}", coletado_em, linhas, vendas)
        horas, meses = _historico(con, destino, ruleset_id, casa_id, agora.strftime("%Y-%m-%d"))
        _gravar(destino / f"degraus-{ruleset_id}-{casa_id}.json", degraus)
        casas.append({"ruleset": ruleset_id, "id": casa_id, "nome": nome, "grupo": grupo,
                      "mercado": MERCADOS.get(grupo),
                      "coletado_em": coletado_em, "itens": len(linhas), "arquivo": arquivo,
                      "degraus": f"degraus-{ruleset_id}-{casa_id}.json",
                      "horas": horas, "meses": meses})
    con.close()

    _gravar(destino / "meta.json", {
        "gerado_em": agora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "casa_padrao": cfg.casa_padrao,
        "addon": zip_do_addon,
        "partes_tooltips": PARTES_TOOLTIPS,
        "casas": casas,
    })
    marcar_checagem(cfg, agora)
    print(f"site: {len(casas)} casas exportadas para {destino}")
