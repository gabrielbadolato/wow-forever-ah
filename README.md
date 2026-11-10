# Monitor da casa de leilões do WoW: Forever

Coleta os leilões do WoW: Forever pela API da Blizzard, guarda o histórico de
preços em SQLite, publica uma página de consulta no GitHub Pages, avisa no
Discord quando um preço cruza um limite e gera um addon que mostra os preços
no tooltip do jogo.

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

A tabela `degraus` guarda, só para a foto mais recente de cada casa, quanto
custa comprar as n unidades mais baratas de cada item (a lista de compras da
página usa isso). Cada arquivo de casa exportado leva também a média de 7 dias
do menor preço de cada item (`medias`) e o valor das vendas estimadas
(`vendas.valores`), base da aba Ofertas e da aba Entre casas.

O detalhe por foto (`precos`, `vendas`) só é guardado por 7 dias. A cada
exportação, `ahforever/compactar.py` resume os dias encerrados na tabela
`diario` (mínimo, média e vendas por item) e esvazia as fotos mais antigas,
menos a última de cada casa. Com isso o banco para de crescer depois da
primeira semana.

Itens que a API da Blizzard não tem (boa parte dos novos do Forever) são
cadastrados pelo tooltip do Wowhead (`ahforever/wowhead.py`, `fonte = 'wowhead'`).

## Vendas estimadas

A API mostra anúncios, não vendas. A cada coleta, `ahforever/vendas.py` compara
a foto nova com a anterior: o que sumiu sem poder ter expirado conta como venda,
tirando os cancelamentos mais prováveis (item bem acima do mercado; commodity
mais cara que outra que continua à venda). O resultado vai para a tabela
`vendas`, e `coletas.intervalo_vendas` guarda quantos segundos cada estimativa
cobre. É uma estimativa conservadora, não um registro de vendas.

## Alertas no Discord

As regras ficam em `alertas.toml` (preço abaixo ou acima de um valor, ou queda
em relação à média de 7 dias) e são conferidas a cada foto nova. A URL do
webhook fica em `segredos/discord_webhook`, fora do git; sem esse arquivo nada
é enviado. Cada regra avisa quando passa a valer e lembra de novo após 24 h.

## Addon

A cada exportação, `ahforever/addon.py` grava `site/addon/AHForever.zip` com os
preços da `casa_padrao`. Extraia a pasta `AHForever` em `Interface/AddOns` do
jogo; o tooltip dos itens passa a mostrar menor preço, mercado, quantidade à
venda e vendas por dia. Os preços são os do momento em que o zip foi baixado:
para atualizar, baixe e extraia de novo. O addon só lê esses dados; não
interage com a casa de leilões. Ainda não foi testado dentro do jogo.
