from datetime import datetime, timedelta

# A página mostra ponto a ponto só os últimos dias; antes disso basta um resumo
# por dia. Sem isto o banco cresce uns 70 MB por dia para sempre.
DIAS_POR_HORA = 7

# Uma linha por (foto, item), somando as variantes: menor preço e quantidade total.
SERIE = (
    "SELECT c.coletado_em AS quando, p.item_id AS item_id, MIN(p.preco_min) AS minimo, "
    "SUM(p.quantidade) AS quantidade FROM coletas c JOIN precos p ON p.coleta_id = c.id "
    "WHERE c.ruleset_id = ? AND c.casa_id = ? AND c.coletado_em >= ? AND c.coletado_em < ? "
    "GROUP BY c.coletado_em, p.item_id"
)


def _seguinte(dia, dias=1):
    return (datetime.fromisoformat(dia) + timedelta(days=dias)).strftime("%Y-%m-%d")


def _fechar_dia(con, ruleset_id, casa_id, dia):
    casa_e_dia = (ruleset_id, casa_id, dia)
    periodo = (ruleset_id, casa_id, dia, _seguinte(dia))
    con.execute(
        "INSERT INTO diario (ruleset_id, casa_id, dia, item_id, minimo, media, quantidade) "
        "SELECT ?, ?, ?, item_id, MIN(minimo), CAST(AVG(minimo) AS INTEGER), "
        f"CAST(AVG(quantidade) AS INTEGER) FROM ({SERIE}) GROUP BY item_id",
        casa_e_dia + periodo)
    # Um item pode ter venda no dia sem ter preço: sumiu logo na primeira foto.
    con.execute(
        "INSERT INTO diario (ruleset_id, casa_id, dia, item_id, vendido, valor_vendido) "
        "SELECT ?, ?, ?, v.item_id, SUM(v.quantidade), SUM(v.valor) "
        "FROM vendas v JOIN coletas c ON c.id = v.coleta_id "
        "WHERE c.ruleset_id = ? AND c.casa_id = ? AND c.coletado_em >= ? AND c.coletado_em < ? "
        "GROUP BY v.item_id "
        "ON CONFLICT (ruleset_id, casa_id, dia, item_id) DO UPDATE SET vendido = excluded.vendido, valor_vendido = excluded.valor_vendido",
        casa_e_dia + periodo)
    con.execute("INSERT INTO dias_fechados VALUES (?, ?, ?)", casa_e_dia)


def compactar(con, hoje):
    """Resume em `diario` os dias já encerrados (UTC) e apaga o detalhe por foto dos antigos.

    Devolve (dias resumidos, fotos esvaziadas). A foto mais recente de cada casa
    nunca é esvaziada: é ela que a página mostra, mesmo se a coleta parar por dias.
    """
    abertos = con.execute(
        "SELECT DISTINCT ruleset_id, casa_id, substr(coletado_em, 1, 10) AS dia FROM coletas "
        "WHERE coletado_em < ? EXCEPT SELECT ruleset_id, casa_id, dia FROM dias_fechados "
        "ORDER BY 1, 2, 3", (hoje,)).fetchall()
    limite = _seguinte(hoje, -DIAS_POR_HORA)
    antigas = [r[0] for r in con.execute(
        "SELECT id FROM coletas WHERE coletado_em < ? AND compactada = 0 AND id NOT IN "
        "(SELECT MAX(id) FROM coletas GROUP BY ruleset_id, casa_id, tipo)", (limite,))]
    with con:
        for ruleset_id, casa_id, dia in abertos:
            _fechar_dia(con, ruleset_id, casa_id, dia)
        for coleta_id in antigas:
            con.execute("DELETE FROM precos WHERE coleta_id = ?", (coleta_id,))
            con.execute("DELETE FROM vendas WHERE coleta_id = ?", (coleta_id,))
            con.execute("UPDATE coletas SET compactada = 1 WHERE id = ?", (coleta_id,))
    return len(abertos), len(antigas)
