import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

PAGINA = Path(__file__).resolve().parent.parent / "site" / "index.html"
# Textos que são iguais em português e inglês e por isso não precisam de tradução.
IGUAIS = {"Goblinomics", "EN", "PT", "Item", "Material", "Total", "Req.", "24 h", "World of Warcraft: Forever"}
ATRIBUTOS = ("title", "placeholder", "aria-label", "label")
SEM_FILHOS = {"img", "input", "br", "meta", "link", "hr"}


class Textos(HTMLParser):
    """Junta os textos e atributos visíveis do HTML fixo da página, fora dos blocos marcados com data-nt."""

    def __init__(self):
        super().__init__()
        self.pilha, self.achados = [], []

    def _fora(self):
        return any(self.pilha)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        fora = tag in ("script", "style") or "data-nt" in attrs
        if not fora and not self._fora():
            self.achados += [attrs[a] for a in ATRIBUTOS if attrs.get(a)]
        if tag not in SEM_FILHOS:
            self.pilha.append(fora)

    def handle_endtag(self, tag):
        if tag not in SEM_FILHOS and self.pilha:
            self.pilha.pop()

    def handle_data(self, data):
        if not self._fora():
            self.achados.append(data)


class TesteTraducao(unittest.TestCase):
    def test_todo_texto_fixo_da_pagina_tem_traducao(self):
        """Texto novo no HTML sem entrada nas tabelas de tradução apareceria em português na versão em inglês.

        Cobre só o HTML fixo; os textos que o script monta precisam ser conferidos no navegador.
        """
        fonte = PAGINA.read_text()
        tabela = fonte[fonte.index("const TEXTOS_EN = {"):fonte.index("const FRASES_EN = [")]
        frases = fonte[fonte.index("const FRASES_EN = ["):fonte.index("const NUM = ")]
        inteiros = set(re.findall(r'"((?:[^"\\]|\\.)*)":\s*"', tabela))
        trechos = [t.replace('\\"', '"') for t in re.findall(r'\[\s*"((?:[^"\\]|\\.)*)",', frases)]
        leitor = Textos()
        leitor.feed(fonte[fonte.index("<body>"):])
        faltam = []
        for texto in leitor.achados:
            limpo = re.sub(r"\s+", " ", texto).strip()
            if not re.search(r"[A-Za-zÀ-ú]{2}", limpo) or limpo in IGUAIS or limpo in inteiros:
                continue
            if not any(t in limpo for t in trechos):
                faltam.append(limpo)
        self.assertEqual(faltam, [])


if __name__ == "__main__":
    unittest.main()
