import tomllib
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    regiao: str
    idioma: str
    rulesets: list[int]
    filtro_casas: list[str]
    banco: Path
    credenciais: Path
    pasta_brutos: Path
    brutos_por_casa: int
    pasta_site: Path | None = None
    casa_padrao: int | None = None
    repositorio_pages: str = ""
    chave_pages: Path | None = None
    pasta_pages: Path | None = None
    alertas: Path | None = None
    webhook_discord: Path | None = None


def carregar(caminho=None):
    caminho = Path(caminho) if caminho else RAIZ / "config.toml"
    with open(caminho, "rb") as f:
        bruto = tomllib.load(f)
    base = caminho.resolve().parent
    return Config(
        regiao=bruto["regiao"],
        idioma=bruto.get("idioma", "en_US"),
        rulesets=list(bruto["rulesets"]),
        filtro_casas=list(bruto.get("filtro_casas", [])),
        banco=base / bruto["banco"],
        credenciais=base / bruto["credenciais"],
        pasta_brutos=base / bruto["pasta_brutos"],
        brutos_por_casa=int(bruto.get("brutos_por_casa", 3)),
        pasta_site=base / bruto.get("pasta_site", "site"),
        casa_padrao=bruto.get("casa_padrao"),
        repositorio_pages=bruto.get("repositorio_pages", ""),
        chave_pages=base / bruto.get("chave_pages", "segredos/pages_deploy_key"),
        pasta_pages=base / bruto.get("pasta_pages", ".pages"),
        alertas=base / bruto.get("alertas", "alertas.toml"),
        webhook_discord=base / bruto.get("webhook_discord", "segredos/discord_webhook"),
    )


def ler_credenciais(caminho):
    """Lê BLIZZARD_CLIENT_ID e BLIZZARD_CLIENT_SECRET de um arquivo CHAVE=valor."""
    valores = {}
    for linha in Path(caminho).read_text().splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        valores[chave.strip()] = valor.strip().strip("\"'")
    client_id = valores.get("BLIZZARD_CLIENT_ID", "")
    segredo = valores.get("BLIZZARD_CLIENT_SECRET", "")
    if not client_id or not segredo:
        raise SystemExit(f"Preencha BLIZZARD_CLIENT_ID e BLIZZARD_CLIENT_SECRET em {caminho}")
    return client_id, segredo
