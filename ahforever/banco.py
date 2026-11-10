import sqlite3

ESQUEMA = """
CREATE TABLE IF NOT EXISTS casas (
    ruleset_id INTEGER NOT NULL,
    casa_id    INTEGER NOT NULL,
    nome       TEXT NOT NULL,
    PRIMARY KEY (ruleset_id, casa_id)
);

-- Uma linha por foto nova de uma casa. tipo: 'itens' ou 'commodities'.
CREATE TABLE IF NOT EXISTS coletas (
    id            INTEGER PRIMARY KEY,
    ruleset_id    INTEGER NOT NULL,
    casa_id       INTEGER NOT NULL,
    tipo          TEXT NOT NULL,
    coletado_em   TEXT NOT NULL,
    last_modified TEXT,
    sha256        TEXT NOT NULL,
    n_anuncios    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS coletas_casa ON coletas (ruleset_id, casa_id, tipo, id);

-- Preços em cobre. variante distingue bônus/modificadores do mesmo item.
CREATE TABLE IF NOT EXISTS precos (
    coleta_id     INTEGER NOT NULL REFERENCES coletas (id),
    item_id       INTEGER NOT NULL,
    variante      TEXT NOT NULL DEFAULT '',
    preco_min     INTEGER,
    preco_mercado INTEGER,
    quantidade    INTEGER NOT NULL,
    n_anuncios    INTEGER NOT NULL,
    PRIMARY KEY (coleta_id, item_id, variante)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS precos_item ON precos (item_id, coleta_id);

-- Cadastro dos itens. fonte é o namespace que respondeu; NULL = não achado ainda.
CREATE TABLE IF NOT EXISTS itens (
    item_id         INTEGER PRIMARY KEY,
    nome            TEXT,
    qualidade       TEXT,
    nivel           INTEGER,
    nivel_requerido INTEGER,
    classe_id       INTEGER,
    classe          TEXT,
    subclasse_id    INTEGER,
    subclasse       TEXT,
    tipo_inventario TEXT,
    empilhavel      INTEGER,
    preco_venda     INTEGER,
    icone           TEXT,
    fonte           TEXT,
    consultado_em   TEXT NOT NULL,
    bruto           TEXT  -- resposta completa da API (JSON), base dos tooltips e das receitas
);

-- Vendas estimadas entre a foto anterior e esta (ver vendas.py). valor em cobre.
CREATE TABLE IF NOT EXISTS vendas (
    coleta_id  INTEGER NOT NULL REFERENCES coletas (id),
    item_id    INTEGER NOT NULL,
    variante   TEXT NOT NULL DEFAULT '',
    quantidade INTEGER NOT NULL,
    valor      INTEGER NOT NULL,
    PRIMARY KEY (coleta_id, item_id, variante)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS vendas_item ON vendas (item_id, coleta_id);

-- Resumo por dia (UTC) de cada item, somando as variantes (ver compactar.py).
-- minimo/media são do menor preço de cada foto; quantidade é a média das fotos.
CREATE TABLE IF NOT EXISTS diario (
    ruleset_id    INTEGER NOT NULL,
    casa_id       INTEGER NOT NULL,
    dia           TEXT NOT NULL,
    item_id       INTEGER NOT NULL,
    minimo        INTEGER,
    media         INTEGER,
    quantidade    INTEGER,
    vendido       INTEGER,
    valor_vendido INTEGER,
    PRIMARY KEY (ruleset_id, casa_id, dia, item_id)
) WITHOUT ROWID;

-- Dias que já têm resumo em diario (uma casa vazia fecha o dia sem nenhuma linha lá).
CREATE TABLE IF NOT EXISTS dias_fechados (
    ruleset_id INTEGER NOT NULL,
    casa_id    INTEGER NOT NULL,
    dia        TEXT NOT NULL,
    PRIMARY KEY (ruleset_id, casa_id, dia)
) WITHOUT ROWID;

-- Receitas de profissão. reagentes é JSON: [[item_id, quantidade], ...].
CREATE TABLE IF NOT EXISTS receitas (
    receita_id    INTEGER PRIMARY KEY,
    profissao_id  INTEGER NOT NULL,
    profissao     TEXT NOT NULL,
    categoria     TEXT,
    nome          TEXT NOT NULL,
    item_id       INTEGER,
    quantidade    REAL NOT NULL,
    reagentes     TEXT NOT NULL,
    consultado_em TEXT NOT NULL
);
"""


def abrir(caminho):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(ESQUEMA)
    # Bancos criados antes de a coluna existir.
    if "bruto" not in [c[1] for c in con.execute("PRAGMA table_info(itens)")]:
        con.execute("ALTER TABLE itens ADD COLUMN bruto TEXT")
    # Segundos cobertos pela estimativa de vendas desta foto; NULL = não estimada.
    if "intervalo_vendas" not in [c[1] for c in con.execute("PRAGMA table_info(coletas)")]:
        con.execute("ALTER TABLE coletas ADD COLUMN intervalo_vendas INTEGER")
    # 1 = precos e vendas desta foto já foram apagados; só o resumo diário ficou.
    if "compactada" not in [c[1] for c in con.execute("PRAGMA table_info(coletas)")]:
        con.execute("ALTER TABLE coletas ADD COLUMN compactada INTEGER NOT NULL DEFAULT 0")
    return con


def registrar_casa(con, ruleset_id, casa_id, nome):
    con.execute(
        "INSERT INTO casas (ruleset_id, casa_id, nome) VALUES (?, ?, ?) "
        "ON CONFLICT (ruleset_id, casa_id) DO UPDATE SET nome = excluded.nome",
        (ruleset_id, casa_id, nome),
    )


def ultima_coleta(con, ruleset_id, casa_id, tipo):
    """Devolve (last_modified, sha256, coletado_em) da foto mais recente, ou três None."""
    linha = con.execute(
        "SELECT last_modified, sha256, coletado_em FROM coletas "
        "WHERE ruleset_id = ? AND casa_id = ? AND tipo = ? ORDER BY id DESC LIMIT 1",
        (ruleset_id, casa_id, tipo),
    ).fetchone()
    return linha or (None, None, None)


def gravar_coleta(con, ruleset_id, casa_id, tipo, coletado_em, resposta, n_anuncios, resumos):
    with con:
        cur = con.execute(
            "INSERT INTO coletas (ruleset_id, casa_id, tipo, coletado_em, last_modified, "
            "sha256, n_anuncios) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ruleset_id, casa_id, tipo, coletado_em, resposta.last_modified,
             resposta.sha256, n_anuncios),
        )
        con.executemany(
            "INSERT INTO precos VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (cur.lastrowid, item_id, variante, r.preco_min, r.preco_mercado,
                 r.quantidade, r.n_anuncios)
                for (item_id, variante), r in resumos.items()
            ],
        )
    return cur.lastrowid


def gravar_vendas(con, coleta_id, intervalo, vendas):
    with con:
        con.execute("UPDATE coletas SET intervalo_vendas = ? WHERE id = ?", (intervalo, coleta_id))
        con.executemany(
            "INSERT INTO vendas VALUES (?, ?, ?, ?, ?)",
            [(coleta_id, item_id, variante, quantidade, valor)
             for (item_id, variante), (quantidade, valor) in vendas.items()],
        )
