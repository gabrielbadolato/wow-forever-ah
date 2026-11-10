from .resumo import chave_variante, preco_unitario, resumir

# A API mostra anúncios, não vendas. A estimativa compara duas fotos seguidas:
# o que sumiu e não podia ter expirado foi vendido ou cancelado. Os filtros
# abaixo tiram os cancelamentos mais prováveis, então o número é conservador.

# Com fotos de hora em hora, anúncios nestas faixas de tempo podem ter expirado.
PODE_TER_EXPIRADO = {"SHORT", "MEDIUM"}
# Item único que some custando bem mais que o mercado costuma ser cancelamento.
FOLGA_SOBRE_O_MERCADO = 1.5


def _chave(anuncio):
    item = anuncio.get("item") or {}
    return (int(item["id"]), chave_variante(item)) if "id" in item else None


def estimar(antes, depois, commodities):
    """Vendas estimadas entre duas fotos: {(item_id, variante): [quantidade, valor em cobre]}."""
    atuais = {a["id"]: a for a in depois}
    mercado = {chave: r.preco_mercado for chave, r in resumir(antes).items()}

    # Commodity é comprada sempre pela mais barata: se um anúncio antigo mais
    # barato continua lá, o que sumiu acima dele não foi comprado.
    sobrevivente = {}
    if commodities:
        for a in antes:
            chave, preco = _chave(a), preco_unitario(a)
            if chave and preco is not None and a["id"] in atuais:
                sobrevivente[chave] = min(preco, sobrevivente.get(chave, preco))

    vendas = {}
    for a in antes:
        chave, preco = _chave(a), preco_unitario(a)
        if chave is None or preco is None:
            continue
        antes_qtd = int(a.get("quantity", 1))
        agora = atuais.get(a["id"])
        if agora is not None:
            # Compra parcial de uma pilha de commodity.
            vendido = antes_qtd - int(agora.get("quantity", 1)) if commodities else 0
        elif a.get("time_left") in PODE_TER_EXPIRADO:
            continue
        elif commodities and chave in sobrevivente and preco > sobrevivente[chave]:
            continue
        elif not commodities and mercado.get(chave) and preco > FOLGA_SOBRE_O_MERCADO * mercado[chave]:
            continue
        else:
            vendido = antes_qtd
        if vendido > 0:
            total = vendas.setdefault(chave, [0, 0])
            total[0] += vendido
            total[1] += vendido * preco
    return vendas
