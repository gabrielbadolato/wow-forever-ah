import tomllib
from datetime import datetime, timedelta, timezone

import requests

from . import banco

# Uma regra que continua valendo é lembrada de novo depois deste tempo.
HORAS_PARA_REPETIR = 24
# A queda é medida contra a média dos menores preços das fotos destes dias.
DIAS_DA_MEDIA = 7
FOTOS_MINIMAS = 6
LIMITE_DISCORD = 1900
FORMATO = "%Y-%m-%dT%H:%M:%SZ"
ESQUEMA = """
CREATE TABLE IF NOT EXISTS alertas (
    chave      TEXT PRIMARY KEY,
    ativo      INTEGER NOT NULL,
    avisado_em TEXT
)
"""


def moeda(cobre):
    partes = [(cobre // 10000, "g"), (cobre // 100 % 100, "s"), (cobre % 100, "c")]
    return " ".join(f"{n}{letra}" for n, letra in partes if n) or "0c"


def carregar(caminho):
    """Lê as regras de alertas.toml. Sem arquivo, não há regras."""
    if not caminho or not caminho.exists():
        return []
    with open(caminho, "rb") as f:
        return tomllib.load(f).get("alerta", [])


def _item(con, referencia):
    if isinstance(referencia, int):
        linha = con.execute("SELECT item_id, nome FROM itens WHERE item_id = ?", (referencia,)).fetchone()
        return (referencia, (linha and linha[1]) or f"Item #{referencia}")
    return con.execute("SELECT item_id, nome FROM itens WHERE nome = ? COLLATE NOCASE ORDER BY item_id",
                       (referencia,)).fetchone()


def _preco_atual(con, ruleset_id, casa_id, item_id):
    """Menor preço do item nas fotos mais recentes da casa (itens e commodities)."""
    return con.execute(
        "SELECT MIN(preco_min) FROM precos WHERE item_id = ? AND coleta_id IN "
        "(SELECT MAX(id) FROM coletas WHERE ruleset_id = ? AND casa_id = ? GROUP BY tipo)",
        (item_id, ruleset_id, casa_id)).fetchone()[0]


def _media(con, ruleset_id, casa_id, item_id, agora):
    desde = (agora - timedelta(days=DIAS_DA_MEDIA)).strftime(FORMATO)
    fotos, media = con.execute(
        "SELECT COUNT(*), AVG(minimo) FROM (SELECT MIN(p.preco_min) AS minimo FROM precos p "
        "JOIN coletas c ON c.id = p.coleta_id WHERE p.item_id = ? AND c.ruleset_id = ? "
        "AND c.casa_id = ? AND c.coletado_em >= ? GROUP BY c.id HAVING minimo IS NOT NULL)",
        (item_id, ruleset_id, casa_id, desde)).fetchone()
    return media if fotos >= FOTOS_MINIMAS else None


def avaliar(con, regras, ruleset_id, casa_padrao, agora):
    """Devolve {chave: texto} das regras que valem agora, e a lista de regras que não deu para ler."""
    valendo, problemas = {}, []
    for regra in regras:
        achado = _item(con, regra.get("item"))
        if not achado:
            problemas.append(f"item não encontrado: {regra.get('item')!r}")
            continue
        item_id, nome = achado
        casa = regra.get("casa", casa_padrao)
        preco = _preco_atual(con, ruleset_id, casa, item_id)
        if preco is None:
            continue
        onde = f"**{nome}** a {moeda(preco)} na casa {casa}"
        if "abaixo" in regra and preco <= regra["abaixo"] * 10000:
            valendo[f"{casa}:{item_id}:abaixo:{regra['abaixo']}"] = f"{onde} (abaixo de {regra['abaixo']}g)"
        if "acima" in regra and preco >= regra["acima"] * 10000:
            valendo[f"{casa}:{item_id}:acima:{regra['acima']}"] = f"{onde} (acima de {regra['acima']}g)"
        if "queda" in regra:
            media = _media(con, ruleset_id, casa, item_id, agora)
            if media and preco <= media * (1 - regra["queda"] / 100):
                valendo[f"{casa}:{item_id}:queda:{regra['queda']}"] = (
                    f"{onde}: {round((1 - preco / media) * 100)}% abaixo da média de "
                    f"{DIAS_DA_MEDIA} dias ({moeda(round(media))})")
    return valendo, problemas


def novos(con, valendo, agora):
    """Filtra o que merece aviso: regra que passou a valer agora, ou que vale há muito sem lembrete."""
    con.execute(ESQUEMA)
    antes = {chave: (ativo, avisado) for chave, ativo, avisado in con.execute("SELECT * FROM alertas")}
    repetir = (agora - timedelta(hours=HORAS_PARA_REPETIR)).strftime(FORMATO)
    return {chave: texto for chave, texto in valendo.items()
            if not antes.get(chave, (0, None))[0] or (antes[chave][1] or "") < repetir}


def registrar(con, valendo, avisados, agora):
    with con:
        con.execute("UPDATE alertas SET ativo = 0")
        for chave in valendo:
            con.execute("INSERT INTO alertas VALUES (?, 1, NULL) ON CONFLICT (chave) DO UPDATE SET ativo = 1",
                        (chave,))
        for chave in avisados:
            con.execute("UPDATE alertas SET avisado_em = ? WHERE chave = ?", (agora.strftime(FORMATO), chave))


def _enviar(webhook, textos):
    # O Discord aceita até 2000 caracteres por mensagem.
    lote = ""
    for texto in [*textos, None]:
        if texto is None or len(lote) + len(texto) + 1 > LIMITE_DISCORD:
            if lote:
                requests.post(webhook, json={"content": lote}, timeout=30).raise_for_status()
            lote = ""
        if texto:
            lote += texto[:LIMITE_DISCORD] + "\n"


def verificar(cfg, enviar=_enviar, agora=None):
    """Confere as regras contra a foto mais recente e avisa no Discord o que passou a valer."""
    regras = carregar(cfg.alertas)
    if not regras:
        return 0
    if not cfg.webhook_discord or not cfg.webhook_discord.exists():
        print(f"alertas: {len(regras)} regras, mas falta o webhook em {cfg.webhook_discord}; nada enviado")
        return 0
    agora = agora or datetime.now(timezone.utc)
    con = banco.abrir(cfg.banco)
    try:
        valendo, problemas = avaliar(con, regras, cfg.rulesets[0], cfg.casa_padrao, agora)
        for problema in problemas:
            print(f"alertas: {problema}")
        avisar = novos(con, valendo, agora)
        if avisar:
            enviar(cfg.webhook_discord.read_text().strip(), [f"🔔 {texto}" for texto in avisar.values()])
        # Só depois do envio: se o Discord falhar, a próxima rodada tenta de novo.
        registrar(con, valendo, avisar, agora)
    finally:
        con.close()
    print(f"alertas: {len(valendo)} regras valendo, {len(avisar)} avisos enviados")
    return len(avisar)
