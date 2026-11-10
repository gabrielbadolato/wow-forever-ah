import argparse
import gzip
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import banco, bonus, catalogo, config, exportar, publicar, receitas, vendas
from .api import ClienteBlizzard, ErroAPI
from .resumo import resumir

TIPOS = ("itens", "commodities")


def listar_casas(api, ruleset_id, filtro):
    """Casas de leilão do ruleset cujo nome casa com o filtro (vazio = todas)."""
    # A barra final é obrigatória: sem ela a rota devolve 404.
    r = api.get(f"/data/wow/ruleset/{ruleset_id}/auctions/")
    if r.status != 200:
        raise SystemExit(f"Índice de casas do ruleset {ruleset_id} devolveu HTTP {r.status}")
    casas = [(int(c["id"]), str(c.get("name", ""))) for c in r.dados.get("auctions", [])]
    if filtro:
        termos = [t.lower() for t in filtro]
        casas = [c for c in casas if any(t in c[1].lower() for t in termos)]
    return casas


def _caminho(ruleset_id, casa_id, tipo):
    base = f"/data/wow/ruleset/{ruleset_id}/auctions/{casa_id}"
    return base + "/commodities" if tipo == "commodities" else base


def _anuncios(dados):
    # Casas vazias vêm sem o campo; em commodities ele pode ser só um link.
    anuncios = dados.get("auctions")
    return anuncios if isinstance(anuncios, list) else []


# Acima disto entre duas fotos, expirações se misturam demais com vendas.
INTERVALO_MAXIMO_VENDAS = 2.5 * 3600


def _instante(last_modified, coletado_em):
    """Quando a Blizzard gerou a foto; sem o cabeçalho, vale a hora em que foi coletada."""
    if last_modified:
        try:
            return parsedate_to_datetime(last_modified)
        except ValueError:
            pass
    return datetime.strptime(coletado_em, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _foto_guardada(cfg, ruleset_id, casa_id, tipo, coletado_em):
    """Anúncios da foto bruta guardada naquela coleta, ou None se ela já foi descartada."""
    carimbo = coletado_em.replace(":", "").replace("-", "")
    arquivo = cfg.pasta_brutos / f"r{ruleset_id}-c{casa_id}-{tipo}-{carimbo}.json.gz"
    if not arquivo.exists():
        return None
    with gzip.open(arquivo) as f:
        return _anuncios(json.load(f))


def _guardar_bruto(cfg, ruleset_id, casa_id, tipo, coletado_em, corpo):
    if cfg.brutos_por_casa <= 0:
        return
    cfg.pasta_brutos.mkdir(parents=True, exist_ok=True)
    prefixo = f"r{ruleset_id}-c{casa_id}-{tipo}-"
    carimbo = coletado_em.replace(":", "").replace("-", "")
    with gzip.open(cfg.pasta_brutos / f"{prefixo}{carimbo}.json.gz", "wb") as f:
        f.write(corpo)
    antigos = sorted(cfg.pasta_brutos.glob(prefixo + "*.json.gz"))[: -cfg.brutos_por_casa]
    for arquivo in antigos:
        arquivo.unlink()


def coletar_casa(api, con, cfg, ruleset_id, casa_id, tipo, agora):
    """Baixa a foto de uma casa e grava o resumo. Devolve o nº de anúncios ou None se não mudou."""
    last_modified, sha_anterior, coleta_anterior = banco.ultima_coleta(con, ruleset_id, casa_id, tipo)
    r = api.get(_caminho(ruleset_id, casa_id, tipo), desde=last_modified)
    if r.status != 200 or r.sha256 == sha_anterior:
        return None
    anuncios = _anuncios(r.dados)
    coleta_id = banco.gravar_coleta(con, ruleset_id, casa_id, tipo, agora, r, len(anuncios), resumir(anuncios))
    if coleta_anterior:
        intervalo = int((_instante(r.last_modified, agora) - _instante(last_modified, coleta_anterior)).total_seconds())
        anteriores = _foto_guardada(cfg, ruleset_id, casa_id, tipo, coleta_anterior)
        if anteriores is not None and 0 < intervalo <= INTERVALO_MAXIMO_VENDAS:
            banco.gravar_vendas(con, coleta_id, intervalo,
                                vendas.estimar(anteriores, anuncios, tipo == "commodities"))
    _guardar_bruto(cfg, ruleset_id, casa_id, tipo, agora, r.corpo)
    return len(anuncios)


def coletar(cfg, api, agora=None):
    con = banco.abrir(cfg.banco)
    agora = agora or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for ruleset_id in cfg.rulesets:
        for casa_id, nome in listar_casas(api, ruleset_id, cfg.filtro_casas):
            with con:
                banco.registrar_casa(con, ruleset_id, casa_id, nome)
            for tipo in TIPOS:
                n = coletar_casa(api, con, cfg, ruleset_id, casa_id, tipo, agora)
                estado = "sem mudança" if n is None else f"{n} anúncios"
                print(f"ruleset {ruleset_id} casa {casa_id} ({nome}) {tipo}: {estado}")
    con.close()


def sondar(cfg, api):
    """Só leitura: mostra o que a API devolve hoje, sem gravar nada."""
    for ruleset_id in cfg.rulesets:
        r = api.get(f"/data/wow/ruleset/{ruleset_id}", tipo_namespace="dynamic")
        nome = (r.dados or {}).get("name", "?")
        print(f"ruleset {ruleset_id}: HTTP {r.status} nome={nome}")
        for casa_id, nome_casa in listar_casas(api, ruleset_id, cfg.filtro_casas):
            for tipo in TIPOS:
                r = api.get(_caminho(ruleset_id, casa_id, tipo))
                if r.status != 200:
                    print(f"  casa {casa_id} ({nome_casa}) {tipo}: HTTP {r.status}")
                    continue
                anuncios = _anuncios(r.dados)
                print(
                    f"  casa {casa_id} ({nome_casa}) {tipo}: {len(anuncios)} anúncios, "
                    f"{len(resumir(anuncios))} itens distintos, {len(r.corpo) / 1e6:.1f} MB, "
                    f"Last-Modified={r.last_modified}, campos={sorted(r.dados)}"
                )
                if anuncios:
                    print(f"    exemplo: {anuncios[0]}")


def main():
    parser = argparse.ArgumentParser(description="Monitor da casa de leilões do WoW: Forever")
    parser.add_argument("comando", choices=["coletar", "catalogar", "receitas", "bonus", "exportar", "publicar", "sondar"])
    parser.add_argument("--config", help="caminho do config.toml")
    args = parser.parse_args()

    cfg = config.carregar(args.config)
    client_id, segredo = config.ler_credenciais(cfg.credenciais)
    api = ClienteBlizzard(cfg.regiao, client_id, segredo, cfg.idioma)
    if args.comando == "sondar":
        sondar(cfg, api)
        return
    if args.comando in ("exportar", "publicar"):
        exportar.exportar(cfg)
        publicar.publicar(cfg)
        return
    if args.comando == "coletar":
        coletar(cfg, api)
    # Itens novos aparecem a cada coleta; uma falha aqui não invalida os preços já gravados.
    bonus.baixar(cfg, forcar=args.comando == "bonus")
    try:
        receitas.baixar(cfg, api, forcar=args.comando == "receitas")
        catalogo.catalogar(cfg, api)
    except ErroAPI as e:
        if args.comando != "coletar":
            raise SystemExit(f"catálogo: {e}")
        print(f"catálogo: adiado para a próxima rodada ({e})")
    exportar.exportar(cfg)
    publicar.publicar(cfg)


if __name__ == "__main__":
    main()
