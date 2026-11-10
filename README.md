# Monitor da casa de leilões do WoW: Forever

Coleta os leilões do WoW: Forever pela API da Blizzard, guarda o histórico de
preços em SQLite e (nas próximas etapas) publica uma página de consulta no
GitHub Pages e alertas no Discord.

A API do Forever ainda não é documentada pela Blizzard. O que este projeto
assume vem de testes da comunidade e pode mudar:
https://us.forums.blizzard.com/en/blizzard/t/wow-forever-api-what-works-so-far-plus-a-ts-client/60334

## Chave da Blizzard

1. Crie um cliente em https://develop.battle.net/access/clients.
2. Preencha `segredos/credenciais.env` (fica fora do git, permissão 600):

```
BLIZZARD_CLIENT_ID=...
BLIZZARD_CLIENT_SECRET=...
```

## Uso

```bash
python3 -m ahforever.coletor sondar    # só leitura: mostra o que a API devolve
python3 -m ahforever.coletor coletar   # grava uma foto de cada casa e cataloga itens novos
python3 -m ahforever.coletor catalogar # só busca o cadastro dos itens que faltam
python3 -m unittest discover -s tests  # testes com dados de exemplo
```

Rulesets e casas coletadas ficam em `config.toml`.

A Blizzard publica uma foto nova por hora, mas às vezes atrasa. O timer roda
`coletar` a cada 20 minutos; sem foto nova, o comando termina sem republicar.

## Dados

- `dados/ah.sqlite`: tabelas `casas`, `coletas` (uma linha por foto nova) e
  `precos` (resumo por item em cada foto, em cobre).
- Tabela `itens`: nome, qualidade, nível, classe e ícone. `fonte` é o namespace
  que respondeu (o do Forever ou, na falta, o do Classic). O ícone fica em
  `https://render.worldofwarcraft.com/<fonte sem "static-">/icons/56/<icone>.jpg`.
- `dados/brutos/`: últimas fotos brutas de cada casa, comprimidas.

`preco_mercado` é a média das 15% unidades mais baratas de cada item.

## Vendas estimadas

A API mostra anúncios, não vendas. A cada coleta, `ahforever/vendas.py` compara
a foto nova com a anterior: o que sumiu sem poder ter expirado conta como venda,
tirando os cancelamentos mais prováveis (item bem acima do mercado; commodity
mais cara que outra que continua à venda). O resultado vai para a tabela
`vendas`, e `coletas.intervalo_vendas` guarda quantos segundos cada estimativa
cobre. É uma estimativa conservadora, não um registro de vendas.
