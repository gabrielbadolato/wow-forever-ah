from dataclasses import dataclass

# Fração das unidades mais baratas usada no preço de mercado, para ignorar
# anúncios isolados muito abaixo ou muito acima do resto.
FRACAO_MERCADO = 0.15


@dataclass
class Resumo:
    preco_min: int | None
    preco_mercado: int | None
    quantidade: int
    n_anuncios: int


def chave_variante(item):
    """Identifica versões diferentes do mesmo item (bônus e modificadores)."""
    partes = []
    bonus = item.get("bonus_lists")
    if bonus:
        partes.append("b" + ":".join(str(b) for b in sorted(bonus)))
    modificadores = item.get("modifiers")
    if modificadores:
        pares = sorted((m.get("type"), m.get("value")) for m in modificadores)
        partes.append("m" + ":".join(f"{t}={v}" for t, v in pares))
    return ";".join(partes)


def preco_unitario(anuncio):
    """Preço por unidade em cobre, ou None para anúncio só com lance."""
    if anuncio.get("unit_price"):
        return int(anuncio["unit_price"])
    if anuncio.get("buyout"):
        return int(anuncio["buyout"]) // max(int(anuncio.get("quantity", 1)), 1)
    return None


def _preco_mercado(precos):
    """Média ponderada das unidades mais baratas. `precos` é [(preço, qtd)] ordenado."""
    total = sum(q for _, q in precos)
    alvo = max(1, round(total * FRACAO_MERCADO))
    soma = usadas = 0
    for preco, quantidade in precos:
        pega = min(quantidade, alvo - usadas)
        soma += preco * pega
        usadas += pega
        if usadas >= alvo:
            break
    return soma // usadas


def resumir(anuncios):
    """Agrupa anúncios por (item_id, variante) e devolve {chave: Resumo}."""
    grupos = {}
    for anuncio in anuncios:
        item = anuncio.get("item") or {}
        if "id" not in item:
            continue
        chave = (int(item["id"]), chave_variante(item))
        grupos.setdefault(chave, []).append(anuncio)

    resumos = {}
    for chave, lista in grupos.items():
        precos = sorted(
            (p, int(a.get("quantity", 1)))
            for a in lista
            if (p := preco_unitario(a)) is not None
        )
        resumos[chave] = Resumo(
            preco_min=precos[0][0] if precos else None,
            preco_mercado=_preco_mercado(precos) if precos else None,
            quantidade=sum(int(a.get("quantity", 1)) for a in lista),
            n_anuncios=len(lista),
        )
    return resumos
