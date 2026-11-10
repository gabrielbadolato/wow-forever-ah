import html
import json
import re
import threading

import requests

from . import bonus

# Parte dos itens novos do Forever não existe na API da Blizzard nem nas tabelas
# do cliente (chegam por hotfix). O Wowhead publica o tooltip deles; a classe e o
# tipo de slot, que o tooltip não traz em números, vêm da tabela Item do wago.tools.
TOOLTIP = "https://nether.wowhead.com/forever/tooltip/item/{}"
FONTE = "wowhead"
QUALIDADES = ("POOR", "COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "ARTIFACT", "HEIRLOOM")
INVENTARIO = {
    0: "NON_EQUIP", 1: "HEAD", 2: "NECK", 3: "SHOULDER", 4: "BODY", 5: "CHEST", 6: "WAIST",
    7: "LEGS", 8: "FEET", 9: "WRIST", 10: "HAND", 11: "FINGER", 12: "TRINKET", 13: "WEAPON",
    14: "SHIELD", 15: "RANGED", 16: "CLOAK", 17: "TWOHWEAPON", 18: "BAG", 19: "TABARD",
    20: "ROBE", 21: "WEAPONMAINHAND", 22: "WEAPONOFFHAND", 23: "HOLDABLE", 24: "AMMO",
    25: "THROWN", 26: "RANGEDRIGHT", 27: "QUIVER", 28: "RELIC",
}
MOEDAS = (("gold", 10000, "g"), ("silver", 100, "s"), ("copper", 1, "c"))
EFEITOS = ("Use:", "Equip:", "Chance on hit:")


def _preco_de_venda(tooltip):
    venda = re.search(r'<div class="whtt-sellprice">(.*?)</div>', tooltip, re.S)
    if not venda:
        return None
    return sum(int(valor) * peso for moeda, peso, _ in MOEDAS
               for valor in re.findall(rf'money{moeda}">(\d+)<', venda.group(1)))


def linhas(tooltip):
    """Converte o HTML do tooltip nas linhas que a página desenha: [texto, classe, direita?]."""
    t = tooltip
    # Receitas trazem em seguida o tooltip do item que ensinam; fica só o primeiro.
    if "whtt-sellprice" in t:
        t = t[:t.find("</div>", t.find("whtt-sellprice"))]
    t = re.sub(r"<!--nstart-->.*?<!--nend-->", "", t, flags=re.S)
    t = re.sub(r'<div class="[^"]*whtt-(extra|seasonal)[^"]*">.*?</div>', "", t, flags=re.S)
    t = re.sub(r'<span class="[^"]*whtt-extra[^"]*">.*?</span>', "", t, flags=re.S)
    for moeda, _, letra in MOEDAS:
        t = re.sub(rf'<span class="money{moeda}">(\d+)</span>', rf"\1{letra}", t)
    t = re.sub(r'<table width="100%"><tr><td>(.*?)</td><th>(.*?)</th></tr></table>', r"\n\1\t\2\n", t, flags=re.S)
    t = re.sub(r"<br\s*/?>|</?div[^>]*>|</?t[dr][^>]*>|</?table[^>]*>", "\n", t)
    t = html.unescape(re.sub(r"<!--.*?-->|<[^>]+>", "", t, flags=re.S)).replace("\xa0", " ")
    saida, no_conjunto = [], False
    for linha in t.split("\n"):
        texto, _, direita = (parte.strip() for parte in linha.partition("\t"))
        texto = re.sub(r"\s+", " ", texto)
        if not texto:
            continue
        if re.search(r"\(\d+/\d+\)$", texto):
            # Daqui até o preço vêm as peças e os bônus do conjunto, em cinza no jogo.
            classe, no_conjunto = "a", True
        elif texto.startswith("Item Level"):
            classe = "a"
        elif no_conjunto:
            classe = "c"
        elif texto.startswith(EFEITOS):
            classe = "v"
        elif texto.startswith("Sell Price") or re.match(r"\(\d+\) Set", texto):
            classe = "c"
        elif texto == "Crafted":
            classe = "z"
        else:
            classe = ""
        saida.append([texto, classe] + ([direita] if direita else []))
    return saida


class Reserva:
    """Busca no Wowhead os itens que a API da Blizzard não tem. Seguro para uso em várias threads."""

    def __init__(self, con):
        # Nomes de classe e subclasse como a API da Blizzard os escreve, tirados do que já está cadastrado.
        self.classes = dict(con.execute(
            "SELECT DISTINCT classe_id, classe FROM itens WHERE classe IS NOT NULL"))
        self.subclasses = {(c, s): nome for c, s, nome in con.execute(
            "SELECT DISTINCT classe_id, subclasse_id, subclasse FROM itens WHERE subclasse IS NOT NULL")}
        self._tabela = None
        self._trava = threading.Lock()

    def _item(self, item_id):
        # A tabela inteira tem 1,4 MB; só é baixada se algum item precisar, uma vez por rodada.
        with self._trava:
            if self._tabela is None:
                try:
                    versao = bonus._get("/api/builds").json()[bonus.PRODUTO][0]["version"]
                    self._tabela = {int(l["ID"]): l for l in bonus._tabela("Item", versao)}
                except (requests.RequestException, KeyError, IndexError, ValueError) as e:
                    print(f"wowhead: sem a tabela de classes do wago.tools ({e})")
                    self._tabela = {}
        return self._tabela.get(item_id)

    def buscar(self, item_id):
        """Devolve o cadastro no formato de catalogo.buscar_item, ou None se o Wowhead não tem."""
        try:
            r = requests.get(TOOLTIP.format(item_id), timeout=30,
                             headers={"User-Agent": "wow-forever-ah (monitor pessoal de leilões)"})
            if r.status_code != 200:
                return None
            d = r.json()
            tooltip = d["tooltip"]
        except (requests.RequestException, ValueError, KeyError):
            return None
        numero = lambda marca: next(iter(map(int, re.findall(rf"<!--{marca}-->(\d+)", tooltip))), None)
        linha = self._item(item_id) or {}
        classe_id = int(linha["ClassID"]) if linha else None
        subclasse_id = int(linha["SubclassID"]) if linha else None
        qualidade = d.get("quality")
        return {
            "item_id": item_id,
            "nome": d.get("name"),
            "qualidade": QUALIDADES[qualidade] if isinstance(qualidade, int) and qualidade < len(QUALIDADES) else None,
            "nivel": numero("ilvl"),
            "nivel_requerido": numero("rlvl"),
            "classe_id": classe_id,
            "classe": self.classes.get(classe_id),
            "subclasse_id": subclasse_id,
            "subclasse": self.subclasses.get((classe_id, subclasse_id)),
            "tipo_inventario": INVENTARIO.get(int(linha["InventoryType"])) if linha else None,
            "empilhavel": int("Max Stack" in tooltip),
            "preco_venda": _preco_de_venda(tooltip),
            "icone": d.get("icon"),
            "fonte": FONTE,
            "bruto": json.dumps({"linhas": linhas(tooltip)}, ensure_ascii=False, separators=(",", ":")),
        }
