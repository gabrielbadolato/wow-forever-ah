import csv
import io
import json
import os
import time

import requests

# Os anúncios trazem só o código do bônus (ex.: 12718). O que ele significa
# ("of the Eagle", +Intellect e +Stamina) não está na API da Blizzard: vem das
# tabelas do cliente do jogo, que o wago.tools publica por versão.
WAGO = "https://wago.tools"
PRODUTO = "wowf"  # WoW: Forever
DIAS_ENTRE_ATUALIZACOES = 7
TIPO_ATRIBUTO, TIPO_SUFIXO = 2, 5
NIVEL_MAXIMO = 120
QUALIDADES = {"UNCOMMON": "GoodF", "RARE": "SuperiorF", "EPIC": "EpicF"}


def _get(caminho, **parametros):
    r = requests.get(WAGO + caminho, params=parametros, timeout=90,
                     headers={"User-Agent": "wow-forever-ah (monitor pessoal de leilões)"})
    r.raise_for_status()
    return r


def _tabela(nome, versao):
    return list(csv.DictReader(io.StringIO(_get(f"/db2/{nome}/csv", build=versao).text)))


def montar(bonus, descricoes, pontos):
    """Junta as três tabelas no formato que a página usa.

    bonus[id] = [sufixo ou None, [[atributo, alocação], ...]]; a alocação é em
    décimos de milésimo do orçamento de pontos do item.
    pontos[nível][qualidade] = orçamento por grupo de slot (5 valores).
    """
    nomes = {int(d["ID"]): d["Description_lang"] for d in descricoes}
    listas = {}
    for linha in sorted(bonus, key=lambda l: int(l["OrderIndex"])):
        tipo = int(linha["Type"])
        if tipo not in (TIPO_ATRIBUTO, TIPO_SUFIXO):
            continue
        lista = listas.setdefault(int(linha["ParentItemBonusListID"]), [None, []])
        if tipo == TIPO_SUFIXO:
            lista[0] = nomes.get(int(linha["Value_0"]))
        else:
            lista[1].append([int(linha["Value_0"]), int(linha["Value_1"])])
    orcamento = {
        int(p["ID"]): {q: [float(p[f"{coluna}_{i}"]) for i in range(5)] for q, coluna in QUALIDADES.items()}
        for p in pontos if int(p["ID"]) <= NIVEL_MAXIMO
    }
    return {"bonus": listas, "pontos": orcamento}


def caminho(cfg):
    return cfg.banco.parent / "bonus.json"


def baixar(cfg, forcar=False):
    """Atualiza dados/bonus.json. Uma falha aqui só adia: o arquivo antigo continua valendo."""
    arquivo = caminho(cfg)
    recente = arquivo.exists() and time.time() - arquivo.stat().st_mtime < DIAS_ENTRE_ATUALIZACOES * 86400
    if recente and not forcar:
        return
    try:
        versao = _get("/api/builds").json()[PRODUTO][0]["version"]
        dados = montar(*(_tabela(t, versao) for t in ("ItemBonus", "ItemNameDescription", "RandPropPoints")))
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        print(f"bônus: não foi possível atualizar ({e})")
        return
    dados["versao"] = versao
    temporario = arquivo.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")))
    os.replace(temporario, arquivo)
    print(f"bônus: {len(dados['bonus'])} listas da versão {versao}")
