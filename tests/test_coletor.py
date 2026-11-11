import json
import tempfile
from datetime import datetime, timezone
import unittest
from pathlib import Path

import zipfile

from ahforever import addon, alertas, banco, bonus, catalogo, coletor, compactar, exportar, receitas, vendas, wowhead
from ahforever.api import Resposta
from ahforever.config import Config
from ahforever.resumo import chave_variante, resumir


class TesteResumo(unittest.TestCase):
    def test_preco_unitario_de_pilha(self):
        r = resumir([{"item": {"id": 1}, "buyout": 1000, "quantity": 10}])
        self.assertEqual(r[(1, "")].preco_min, 100)
        self.assertEqual(r[(1, "")].quantidade, 10)

    def test_so_lance_conta_mas_nao_tem_preco(self):
        r = resumir([{"item": {"id": 1}, "bid": 50, "quantity": 1}])
        self.assertIsNone(r[(1, "")].preco_min)
        self.assertIsNone(r[(1, "")].preco_mercado)
        self.assertEqual(r[(1, "")].n_anuncios, 1)

    def test_preco_mercado_ignora_cauda_cara(self):
        anuncios = [
            {"item": {"id": 7}, "unit_price": 10, "quantity": 10},
            {"item": {"id": 7}, "unit_price": 20, "quantity": 20},
            {"item": {"id": 7}, "unit_price": 9999, "quantity": 70},
        ]
        r = resumir(anuncios)[(7, "")]
        # 15% de 100 unidades = 15: dez a 10 e cinco a 20.
        self.assertEqual(r.preco_mercado, (10 * 10 + 5 * 20) // 15)
        self.assertEqual(r.preco_min, 10)

    def test_degraus_dao_o_custo_de_comprar_em_quantidade(self):
        anuncios = [
            {"item": {"id": 7}, "unit_price": 10, "quantity": 3},
            {"item": {"id": 7}, "unit_price": 20, "quantity": 4},
            {"item": {"id": 8}, "unit_price": 5, "quantity": 9},
        ]
        r = resumir(anuncios)
        # 1 e 2 unidades a 10; 5 = três a 10 e duas a 20; tudo (7) = 30 + 80.
        self.assertEqual(r[(7, "")].degraus, [[1, 10], [2, 20], [5, 70], [7, 110]])
        # Preço único: comprar n custa n vezes o menor preço, não há o que guardar.
        self.assertIsNone(r[(8, "")].degraus)

    def test_variantes_ficam_separadas(self):
        a = {"id": 5, "bonus_lists": [3, 1]}
        b = {"id": 5, "bonus_lists": [1, 3], "modifiers": [{"type": 9, "value": 30}]}
        self.assertEqual(chave_variante(a), "b1:3")
        self.assertEqual(chave_variante(b), "b1:3;m9=30")
        r = resumir([{"item": a, "buyout": 1, "quantity": 1}, {"item": b, "buyout": 2, "quantity": 1}])
        self.assertEqual(len(r), 2)


class ApiFalsa:
    def __init__(self, rotas):
        self.rotas = rotas
        self.pedidos = []

    regiao = "us"
    last_modified = "Mon, 09 Nov 2026 12:00:00 GMT"

    def get(self, caminho, tipo_namespace="dynamic", desde=None, namespace=None):
        self.pedidos.append((caminho, desde))
        dados = self.rotas.get((caminho, namespace) if namespace else caminho)
        if dados is None:
            return Resposta(404, None, b"", None)
        return Resposta(200, dados, json.dumps(dados).encode(), self.last_modified)


INDICE = {"auctions": [
    {"id": 113, "name": "Horde Auction House"},
    {"id": 110, "name": "Alliance Auction House"},
    {"id": 115, "name": "Horde Auction House"},
]}
CASA_113 = {"id": 113, "name": "Horde Auction House", "commodities": {"href": "x"}, "auctions": [
    {"id": 1, "item": {"id": 720}, "buyout": 5000, "quantity": 1, "time_left": "LONG"},
    {"id": 2, "item": {"id": 720}, "bid": 100, "quantity": 1, "time_left": "SHORT"},
]}
COMMODITIES_113 = {"auctions": [
    {"id": 3, "item": {"id": 2589}, "unit_price": 34, "quantity": 200, "time_left": "LONG"},
]}
# Casa vazia: a API omite o campo auctions.
CASA_115 = {"id": 115, "name": "Horde Auction House"}


class TesteColetor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.cfg = Config("us", "en_US", [2], ["Horde"], base / "ah.sqlite",
                          base / "cred.env", base / "brutos", 2)
        self.api = ApiFalsa({
            "/data/wow/ruleset/2/auctions/": INDICE,
            "/data/wow/ruleset/2/auctions/113": CASA_113,
            "/data/wow/ruleset/2/auctions/113/commodities": COMMODITIES_113,
            "/data/wow/ruleset/2/auctions/115": CASA_115,
        })

    def consultar(self, sql):
        con = banco.abrir(self.cfg.banco)
        self.addCleanup(con.close)
        return con.execute(sql).fetchall()

    def test_coleta_filtra_casas_e_grava_resumo(self):
        coletor.coletar(self.cfg, self.api)
        self.assertEqual(self.consultar("SELECT casa_id FROM casas ORDER BY 1"), [(113,), (115,)])
        self.assertEqual(
            self.consultar("SELECT casa_id, tipo, n_anuncios FROM coletas ORDER BY casa_id, tipo"),
            [(113, "commodities", 1), (113, "itens", 2), (115, "itens", 0)],
        )
        self.assertEqual(
            self.consultar("SELECT item_id, preco_min, quantidade, n_anuncios FROM precos ORDER BY 1"),
            [(720, 5000, 2, 2), (2589, 34, 200, 1)],
        )
        self.assertEqual(len(list(self.cfg.pasta_brutos.glob("*.json.gz"))), 3)

    def test_foto_repetida_nao_duplica(self):
        self.assertEqual(coletor.coletar(self.cfg, self.api), 3)
        # Sem foto nova a rodada devolve zero, e o comando não republica a página.
        self.assertEqual(coletor.coletar(self.cfg, self.api), 0)
        self.assertEqual(self.consultar("SELECT count(*) FROM coletas"), [(3,)])
        # Na segunda rodada o pedido leva o Last-Modified da primeira.
        self.assertIn(("/data/wow/ruleset/2/auctions/113", "Mon, 09 Nov 2026 12:00:00 GMT"),
                      self.api.pedidos)

    def test_catalogo_usa_fonte_reserva_e_marca_o_que_falta(self):
        item = {"name": "Brawler Gloves", "quality": {"type": "UNCOMMON"}, "level": 13,
                "required_level": 8, "item_class": {"id": 4, "name": "Armor"},
                "item_subclass": {"id": 2, "name": "Leather"},
                "inventory_type": {"type": "HAND"}, "is_stackable": False, "sell_price": 120}
        midia = {"assets": [{"key": "icon", "value": "https://x/icons/56/inv_gauntlets_05.jpg"}]}
        # 720 só existe no Classic; 2589 não existe em lugar nenhum.
        self.api.rotas[("/data/wow/item/720", "static-classic1x-us")] = item
        self.api.rotas[("/data/wow/media/item/720", "static-classic1x-us")] = midia
        coletor.coletar(self.cfg, self.api)
        catalogo.catalogar(self.cfg, self.api)
        self.assertEqual(
            self.consultar("SELECT item_id, nome, nivel, classe, icone, fonte FROM itens ORDER BY 1"),
            [(720, "Brawler Gloves", 13, "Armor", "inv_gauntlets_05", "static-classic1x-us"),
             (2589, None, None, None, None, None)],
        )
        # O nome em português vem da mesma fonte, com o cliente em pt_BR; sem resposta fica vazio e não é pedido de novo.
        api_pt = ApiFalsa({("/data/wow/item/720", "static-classic1x-us"): {"name": "Luvas de Brigão"}})
        catalogo.traduzir(self.cfg, api_pt)
        catalogo.traduzir(self.cfg, api_pt)
        self.assertEqual(self.consultar("SELECT nome_pt FROM itens WHERE item_id = 720"), [("Luvas de Brigão",)])
        self.assertEqual(sum(c.startswith("/data/wow/item/") for c, _ in api_pt.pedidos), 1)
        # Quem não foi achado só é tentado de novo depois de alguns dias.
        n = len(self.api.pedidos)
        catalogo.catalogar(self.cfg, self.api)
        self.assertEqual(len(self.api.pedidos), n)

    def test_historico_por_hora_e_resumo_do_dia_fechado(self):
        con = banco.abrir(self.cfg.banco)
        self.addCleanup(con.close)
        fotos = [("2026-11-08T10:20:00Z", 100, 5), ("2026-11-08T11:20:00Z", 80, 7),
                 ("2026-11-09T10:20:00Z", 90, 4)]
        for i, (quando, preco, quantidade) in enumerate(fotos, 1):
            con.execute("INSERT INTO coletas (id, ruleset_id, casa_id, tipo, coletado_em, sha256, n_anuncios) "
                        "VALUES (?, 2, 113, 'itens', ?, ?, 3)", (i, quando, str(i)))
            # Duas variantes do mesmo item viram um ponto só: menor preço, quantidades somadas.
            con.execute("INSERT INTO precos VALUES (?, 720, '', ?, ?, ?, 1)", (i, preco, preco, quantidade))
            con.execute("INSERT INTO precos VALUES (?, 720, 'b1', ?, ?, 1, 1)", (i, preco + 50, preco + 50))
        con.execute("INSERT INTO vendas VALUES (2, 720, '', 3, 300)")
        con.commit()
        destino = self.cfg.pasta_brutos.parent / "site"
        self.assertEqual(compactar.compactar(con, "2026-11-09"), (1, 0))
        horas, meses = exportar._historico(con, destino, 2, 113, "2026-11-09")
        self.assertEqual((horas, meses), (["2026-11-08", "2026-11-09"], ["2026-11"]))
        pasta = destino / "hist" / "2-113"
        dia = json.loads((pasta / "h-2026-11-08.json").read_text())
        self.assertEqual(dia["t"], ["2026-11-08T10:20:00Z", "2026-11-08T11:20:00Z"])
        self.assertEqual(dia["itens"]["720"], [[100, 80], [6, 8]])
        # A venda fica na posição da foto que a revelou (a segunda do dia).
        self.assertEqual(dia["vendas"], {"720": [[1, 3]]})
        mes = json.loads((pasta / "d-2026-11.json").read_text())
        self.assertEqual(mes, {"v": exportar.VERSAO_HIST, "dias": ["2026-11-08"],
                               "itens": {"720": [[80], [90], [7], [3]]}})
        # Arquivo gravado num formato antigo é refeito, mesmo sendo de um dia já pronto.
        (pasta / "h-2026-11-08.json").write_text('{"t": [], "itens": {}}')
        exportar._historico(con, destino, 2, 113, "2026-11-12")
        self.assertIn("vendas", json.loads((pasta / "h-2026-11-08.json").read_text()))

        # Semanas depois, o detalhe por foto some; ficam o resumo diário e a foto mais recente.
        self.assertEqual(compactar.compactar(con, "2026-11-30"), (1, 2))
        self.assertEqual(con.execute("SELECT DISTINCT coleta_id FROM precos").fetchall(), [(3,)])
        self.assertEqual(con.execute("SELECT count(*) FROM vendas").fetchone(), (0,))
        self.assertEqual(
            con.execute("SELECT dia, minimo, media, quantidade, vendido, valor_vendido FROM diario "
                        "ORDER BY 1").fetchall(),
            [("2026-11-08", 80, 90, 7, 3, 300), ("2026-11-09", 90, 90, 5, None, None)])
        self.assertEqual(compactar.compactar(con, "2026-11-30"), (0, 0))

    def test_linhas_do_tooltip_seguem_a_ordem_do_jogo(self):
        previa = {
            "binding": {"type": "ON_EQUIP", "name": "Binds when equipped"},
            "inventory_type": {"type": "TWOHWEAPON", "name": "Two-Hand"},
            "item_subclass": {"name": "Staff", "id": 10},
            "weapon": {"damage": {"display_string": "120 - 181 Damage"},
                       "attack_speed": {"display_string": "Speed 3.60"},
                       "dps": {"display_string": "(41.8 damage per second)"}},
            "stats": [{"display": {"display_string": "+8 Stamina"}}],
            "spells": [{"description": "Equip: Restores 4 mana per 5 sec."}],
            "requirements": {"level": {"display_string": "Requires Level 49"}},
            "sell_price": {"value": 35714},
        }
        self.assertEqual(exportar.linhas_do_tooltip(previa), [
            ["Binds when equipped", ""],
            ["Two-Hand", "", "Staff"],
            ["120 - 181 Damage", "", "Speed 3.60"],
            ["(41.8 damage per second)", ""],
            ["+8 Stamina", ""],
            ["Requires Level 49", ""],
            ["Equip: Restores 4 mana per 5 sec.", "v"],
            ["Sell Price: 3g 57s 14c", "c"],
        ])
        self.assertEqual(exportar.linhas_do_tooltip({}), [])

    def test_receitas_sao_baixadas_uma_vez_e_exportadas_com_preco_de_npc(self):
        ns = "static-forever-us"
        self.api.rotas.update({
            ("/data/wow/profession/index", ns): {"professions": [
                {"id": 171, "name": "Alchemy"}, {"id": 2933, "name": "Test Profession [DNT]"}]},
            ("/data/wow/profession/171", ns): {"skill_tiers": [{"id": 9}]},
            ("/data/wow/profession/171/skill-tier/9", ns): {"categories": [
                {"name": "Potions", "recipes": [{"id": 1, "name": "Minor Healing Potion"}]}]},
            ("/data/wow/recipe/1", ns): {"crafted_item": {"id": 118}, "crafted_quantity": {"value": 1.0},
                                         "reagents": [{"reagent": {"id": 3371}, "quantity": 1},
                                                      {"reagent": {"id": 2447}, "quantity": 2}]},
        })
        receitas.baixar(self.cfg, self.api)
        n = len(self.api.pedidos)
        receitas.baixar(self.cfg, self.api)  # varredura recente: não consulta de novo
        self.assertEqual(len(self.api.pedidos), n)
        con = banco.abrir(self.cfg.banco)
        self.addCleanup(con.close)
        # Frasco vendido por NPC em lote de 5: o preço exportado é o da unidade.
        con.execute("INSERT INTO itens (item_id, fonte, consultado_em, bruto) VALUES (3371, 'x', 'x', ?)",
                    (json.dumps({"purchase_price": 20, "purchase_quantity": 5}),))
        self.assertEqual(exportar._receitas(con), {
            "profissoes": {171: "Alchemy"},
            "receitas": [[1, 171, "Potions", "Minor Healing Potion", 118, 1.0, [[3371, 1], [2447, 2]]]],
            "npc": {3371: 4},
        })
        # Os itens das receitas entram na fila do catálogo mesmo sem nunca terem aparecido no leilão.
        self.assertEqual(catalogo.pendentes(con, datetime.now(timezone.utc)), [118, 2447])

    def test_bonus_junta_sufixo_atributos_e_orcamento(self):
        linhas = [
            {"ParentItemBonusListID": "12718", "Type": "5", "Value_0": "14300", "Value_1": "0", "OrderIndex": "2"},
            {"ParentItemBonusListID": "12718", "Type": "2", "Value_0": "7", "Value_1": "6666", "OrderIndex": "1"},
            {"ParentItemBonusListID": "12718", "Type": "2", "Value_0": "5", "Value_1": "6666", "OrderIndex": "0"},
            {"ParentItemBonusListID": "9", "Type": "23", "Value_0": "1", "Value_1": "0", "OrderIndex": "0"},
        ]
        ponto = {"ID": "36", **{f"{c}_{i}": str(v) for c, base in (("GoodF", 14), ("SuperiorF", 20), ("EpicF", 26))
                                for i, v in enumerate((base, base - 3, base - 6, base - 8, base - 10))}}
        dados = bonus.montar(linhas, [{"ID": "14300", "Description_lang": "of the Eagle"}],
                             [ponto, {**ponto, "ID": "999"}])
        self.assertEqual(dados["bonus"], {12718: ["of the Eagle", [[5, 6666], [7, 6666]]]})
        self.assertEqual(dados["pontos"], {36: {"UNCOMMON": [14.0, 11.0, 8.0, 6.0, 4.0],
                                                "RARE": [20.0, 17.0, 14.0, 12.0, 10.0],
                                                "EPIC": [26.0, 23.0, 20.0, 18.0, 16.0]}})

    def test_dificuldade_da_receita(self):
        habilidades = [{"ID": "5", "TrivialSkillLineRankLow": "250", "TrivialSkillLineRankHigh": "290"},
                       {"ID": "6", "TrivialSkillLineRankLow": "0", "TrivialSkillLineRankHigh": "0"}]
        self.assertEqual(bonus.dificuldades(habilidades), {5: [250, 290]})

    def test_recarga_vem_do_feitico_ou_da_categoria(self):
        habilidades = [{"ID": "10487", "Spell": "18560"}, {"ID": "9840", "Spell": "17187"},
                       {"ID": "3324", "Spell": "2657"}, {"ID": "1", "Spell": "5"}]
        tempos = [{"SpellID": "18560", "RecoveryTime": "342000000", "CategoryRecoveryTime": "0"},
                  {"SpellID": "17187", "RecoveryTime": "0", "CategoryRecoveryTime": "169200000"},
                  {"SpellID": "5", "RecoveryTime": "3000", "CategoryRecoveryTime": "0"}]
        self.assertEqual(bonus.recargas(habilidades, tempos), {10487: 95.0, 9840: 47.0})

    def test_tooltip_do_wowhead_vira_linhas(self):
        tooltip = ('<table><tr><td><!--nstart--><b class="q3">Sandals</b><!--nend--><span class="q"><br>Item Level '
                   '<!--ilvl-->33</span><br>Binds when equipped<table width="100%"><tr><td>Feet</td><th>'
                   '<span class="q1">Cloth</span></th></tr></table><span>34 Armor</span><br><span>+7 Stamina</span>'
                   '</td></tr></table><table><tr><td>Requires Level <!--rlvl-->28<br><span class="q">'
                   '<a href="/x">Vestments</a> (0/5)</span><div class="q0 indent"><span>Mantle</span></div>'
                   '<span class="q0"><span>(2) Set : <a href="/y">+5 Arcane Resistance.</a></span></span>'
                   '<div class="whtt-sellprice">Sell Price: <span class="moneysilver">21</span> '
                   '<span class="moneycopper">86</span></div><div class="whtt-extra">Dropped by: X</div>'
                   '<br /><span class="q2">Outro item</span></td></tr></table>')
        self.assertEqual(wowhead.linhas(tooltip), [
            ["Item Level 33", "a"], ["Binds when equipped", ""], ["Feet", "", "Cloth"], ["34 Armor", ""],
            ["+7 Stamina", ""], ["Requires Level 28", ""], ["Vestments (0/5)", "a"], ["Mantle", "c"],
            ["(2) Set : +5 Arcane Resistance.", "c"], ["Sell Price: 21s 86c", "c"]])
        self.assertEqual(wowhead._preco_de_venda(tooltip), 2186)

    def test_vendas_de_itens_ignoram_expirados_e_cancelamentos_caros(self):
        def anuncio(id, preco, tempo="LONG"):
            return {"id": id, "item": {"id": 720}, "buyout": preco, "quantity": 1, "time_left": tempo}
        antes = [anuncio(1, 100), anuncio(2, 110), anuncio(3, 105, "SHORT"), anuncio(4, 900), anuncio(5, 120),
                 {"id": 6, "item": {"id": 720}, "bid": 50, "quantity": 1, "time_left": "LONG"}]
        depois = [anuncio(5, 120)]
        # 1 e 2 venderam; 3 pode ter expirado; 4 custava muito acima do mercado; 6 só tinha lance.
        self.assertEqual(vendas.estimar(antes, depois, commodities=False), {(720, ""): [2, 210]})

    def test_vendas_de_commodities_contam_parciais_e_respeitam_o_mais_barato(self):
        def pilha(id, preco, quantidade, tempo="LONG"):
            return {"id": id, "item": {"id": 2589}, "unit_price": preco, "quantity": quantidade, "time_left": tempo}
        antes = [pilha(1, 10, 50), pilha(2, 11, 40), pilha(3, 12, 30), pilha(4, 30, 20)]
        # A 1 foi comprada inteira e a 2 em parte. A 3 continua, então a 4 (mais cara) saiu por cancelamento.
        depois = [pilha(2, 11, 25), pilha(3, 12, 30)]
        self.assertEqual(vendas.estimar(antes, depois, commodities=True), {(2589, ""): [65, 50 * 10 + 15 * 11]})

    def test_alerta_avisa_quando_passa_a_valer_e_nao_repete(self):
        coletor.coletar(self.cfg, self.api)
        con = banco.abrir(self.cfg.banco)
        self.addCleanup(con.close)
        con.execute("INSERT INTO itens (item_id, nome, consultado_em) VALUES (2589, 'Linen Cloth', 'x')")
        con.commit()
        base = Path(self.tmp.name)
        self.cfg.alertas, self.cfg.webhook_discord, self.cfg.casa_padrao = base / "alertas.toml", base / "webhook", 113
        self.cfg.alertas.write_text('[[alerta]]\nitem = "linen cloth"\nabaixo = 0.01\n'
                                    '[[alerta]]\nitem = 720\nacima = 9\n[[alerta]]\nitem = "Nada"\nabaixo = 1\n')
        enviados = []
        enviar = lambda webhook, textos: enviados.append((webhook, textos))
        agora = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        # Sem o arquivo do webhook nada sai, e a regra não é dada como avisada.
        self.assertEqual(alertas.verificar(self.cfg, enviar, agora), 0)
        self.cfg.webhook_discord.write_text("https://exemplo/webhook\n")
        # 34c está abaixo de 0,01g (100c); 720 custa 50s, longe dos 9g.
        self.assertEqual(alertas.verificar(self.cfg, enviar, agora), 1)
        self.assertEqual(enviados, [("https://exemplo/webhook",
                                     ["🔔 **Linen Cloth** a 34c na casa 113 (abaixo de 0.01g)"])])
        self.assertEqual(alertas.verificar(self.cfg, enviar, agora), 0)
        # Continua valendo um dia depois: lembra de novo.
        self.assertEqual(alertas.verificar(self.cfg, enviar, agora.replace(day=11, hour=13)), 1)

    def test_addon_leva_o_menor_preco_de_cada_item(self):
        destino = Path(self.tmp.name)
        linhas = [[720, "", 5000, 5000, 1, 1], [720, "b1", 4000, 4100, 2, 2], [9, "", None, None, 1, 1]]
        caminho = addon.gerar(destino, "Horde Auction House 113", "2026-11-10T16:40:09Z", linhas,
                              {"horas": 12, "itens": {720: 6}})
        with zipfile.ZipFile(destino / caminho) as z:
            self.assertEqual(sorted(z.namelist()),
                             ["AHForever/AHForever.lua", "AHForever/AHForever.toc", "AHForever/Dados.lua"])
            dados = z.read("AHForever/Dados.lua").decode()
        # Menor preço entre as variantes, mercado dele, total à venda e vendas por dia; só lance fica de fora.
        self.assertIn("[720]={4000,4100,3,12},", dados)
        self.assertNotIn("[9]=", dados)

    def test_coleta_seguinte_grava_as_vendas_estimadas(self):
        self.api.rotas["/data/wow/ruleset/2/auctions/113"] = {**CASA_113, "auctions": [
            {"id": 1, "item": {"id": 720}, "buyout": 5000, "quantity": 1, "time_left": "LONG"}]}
        instantes = iter(["2026-11-10T10:20:00Z", "2026-11-10T11:20:00Z"])
        relogio = lambda cfg, api: coletor.coletar(cfg, api, agora=next(instantes))
        relogio(self.cfg, self.api)
        # O intervalo vem da hora em que a Blizzard gerou cada foto, não da hora da coleta.
        self.api.rotas["/data/wow/ruleset/2/auctions/113"] = {**CASA_113, "auctions": []}
        self.api.last_modified = "Mon, 09 Nov 2026 13:00:00 GMT"
        relogio(self.cfg, self.api)
        self.assertEqual(self.consultar("SELECT item_id, quantidade, valor FROM vendas"), [(720, 1, 5000)])
        self.assertEqual(self.consultar(
            "SELECT intervalo_vendas FROM coletas WHERE tipo = 'itens' AND casa_id = 113 ORDER BY id"), [(None,), (3600,)])


if __name__ == "__main__":
    unittest.main()
