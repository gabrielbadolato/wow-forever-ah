import hashlib
import json
import threading
import time
from dataclasses import dataclass

import requests

URL_TOKEN = "https://oauth.battle.net/token"
TEMPO_LIMITE = 120
TENTATIVAS = 4


class ErroAPI(Exception):
    pass


@dataclass
class Resposta:
    status: int
    dados: dict | None
    corpo: bytes
    last_modified: str | None

    @property
    def sha256(self):
        return hashlib.sha256(self.corpo).hexdigest()


class ClienteBlizzard:
    def __init__(self, regiao, client_id, segredo, idioma="en_US", sessao=None):
        self.regiao = regiao
        self.idioma = idioma
        self._credenciais = (client_id, segredo)
        self._sessao = sessao or requests.Session()
        self._token = None
        self._token_expira = 0.0
        self._trava_token = threading.Lock()

    def _obter_token(self):
        with self._trava_token:
            return self._renovar_token()

    def _renovar_token(self):
        if self._token and time.time() < self._token_expira - 60:
            return self._token
        r = self._sessao.post(
            URL_TOKEN,
            data={"grant_type": "client_credentials"},
            auth=self._credenciais,
            timeout=TEMPO_LIMITE,
        )
        if r.status_code != 200:
            raise ErroAPI(f"Falha ao obter token: HTTP {r.status_code}")
        corpo = r.json()
        self._token = corpo["access_token"]
        self._token_expira = time.time() + corpo.get("expires_in", 3600)
        return self._token

    def get(self, caminho, tipo_namespace="dynamic", desde=None, namespace=None):
        """GET na API de dados do Forever. 304 e 404 voltam como Resposta sem dados.

        `namespace` troca o namespace inteiro (ex.: "static-classic1x-us").
        """
        cabecalhos = {"Authorization": f"Bearer {self._obter_token()}"}
        if desde:
            cabecalhos["If-Modified-Since"] = desde
        for tentativa in range(TENTATIVAS):
            r = self._sessao.get(
                f"https://{self.regiao}.api.blizzard.com{caminho}",
                params={
                    "namespace": namespace or f"{tipo_namespace}-forever-{self.regiao}",
                    "locale": self.idioma,
                },
                headers=cabecalhos,
                timeout=TEMPO_LIMITE,
            )
            # 429 = limite de pedidos; 5xx costuma ser passageiro.
            if r.status_code != 429 and r.status_code < 500:
                break
            time.sleep(2 * (tentativa + 1))
        if r.status_code in (304, 404):
            return Resposta(r.status_code, None, b"", r.headers.get("Last-Modified"))
        if r.status_code != 200:
            raise ErroAPI(f"HTTP {r.status_code} em {caminho}")
        try:
            dados = json.loads(r.content)
        except ValueError as e:
            raise ErroAPI(f"Resposta inválida em {caminho}: {e}") from e
        return Resposta(200, dados, r.content, r.headers.get("Last-Modified"))
