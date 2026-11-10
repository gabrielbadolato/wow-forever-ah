import zipfile
from datetime import datetime

NOME = "AHForever"
ARQUIVO = f"addon/{NOME}.zip"
# 1.60.1 pela convenção da Blizzard (maior * 10000 + menor * 100 + correção). Se o jogo
# acusar addon desatualizado, basta marcar "carregar addons desatualizados".
INTERFACE = 16001

TOC = f"""## Interface: {INTERFACE}
## Title: AH Forever
## Notes: Mostra no tooltip os preços coletados pelo monitor da casa de leilões.
## Version: {{versao}}
Dados.lua
{NOME}.lua
"""

# Só lê os dados e escreve no tooltip; não fala com a casa de leilões nem automatiza nada.
LUA = """local dados = AHForeverDados
if not dados then return end

local function moeda(cobre)
  if GetCoinTextureString then return GetCoinTextureString(cobre) end
  return string.format("%dg %ds %dc", cobre / 10000, cobre / 100 % 100, cobre % 100)
end

local function escrever(tooltip, link)
  local id = link and tonumber(link:match("item:(%d+)"))
  local p = id and dados.precos[id]
  if not p then return end
  tooltip:AddLine(" ")
  tooltip:AddDoubleLine("Leilão: menor preço", moeda(p[1]), 1, .82, 0, 1, 1, 1)
  if p[2] > 0 then tooltip:AddDoubleLine("Leilão: mercado", moeda(p[2]), 1, .82, 0, 1, 1, 1) end
  tooltip:AddDoubleLine("À venda", p[3], 1, .82, 0, 1, 1, 1)
  if p[4] then tooltip:AddDoubleLine("Vendas por dia", p[4], 1, .82, 0, 1, 1, 1) end
  tooltip:AddLine(dados.casa .. ", " .. dados.quando, .6, .6, .6)
  tooltip:Show()
end

if TooltipDataProcessor and Enum and Enum.TooltipDataType and Enum.TooltipDataType.Item then
  TooltipDataProcessor.AddTooltipPostCall(Enum.TooltipDataType.Item, function(tooltip)
    if tooltip.GetItem then
      local _, link = tooltip:GetItem()
      escrever(tooltip, link)
    end
  end)
else
  for _, tooltip in ipairs({GameTooltip, ItemRefTooltip}) do
    tooltip:HookScript("OnTooltipSetItem", function(self)
      local _, link = self:GetItem()
      escrever(self, link)
    end)
  end
end

SLASH_AHFOREVER1 = "/ahf"
SlashCmdList["AHFOREVER"] = function()
  print("AH Forever: preços de " .. dados.casa .. ", coletados em " .. dados.quando .. ".")
end
"""


def _dados(casa, coletado_em, linhas, vendas):
    """Dados.lua: {[item] = {menor preço, mercado, à venda, vendas por dia}}, somando as variantes."""
    itens = {}
    for item_id, _, minimo, mercado, quantidade, *_ in linhas:
        atual = itens.setdefault(item_id, [None, 0, 0])
        atual[2] += quantidade
        if minimo is not None and (atual[0] is None or minimo < atual[0]):
            atual[0], atual[1] = minimo, mercado or 0
    horas = vendas.get("horas") or 0
    quando = datetime.fromisoformat(coletado_em.replace("Z", "+00:00")).astimezone()
    saida = [f'AHForeverDados = {{casa = "{casa}", quando = "{quando:%d/%m/%Y %H:%M}", precos = {{']
    for item_id, (minimo, mercado, quantidade) in sorted(itens.items()):
        if minimo is None:
            continue
        por_dia = f",{round(vendas['itens'].get(item_id, 0) / horas * 24)}" if horas >= 6 else ""
        saida.append(f"[{item_id}]={{{minimo},{mercado},{quantidade}{por_dia}}},")
    saida.append("}}\n")
    return "\n".join(saida)


def gerar(destino, casa, coletado_em, linhas, vendas):
    """Grava <destino>/addon/AHForever.zip com os preços da casa e devolve o caminho relativo."""
    arquivo = destino / ARQUIVO
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(".tmp")
    partes = {"Dados.lua": _dados(casa, coletado_em, linhas, vendas), f"{NOME}.lua": LUA,
              f"{NOME}.toc": TOC.format(versao=coletado_em[:10])}
    with zipfile.ZipFile(temporario, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, texto in partes.items():
            # Data fixa: o zip só muda quando o conteúdo muda.
            z.writestr(zipfile.ZipInfo(f"{NOME}/{nome}", (2026, 1, 1, 0, 0, 0)), texto,
                       compress_type=zipfile.ZIP_DEFLATED)
    temporario.replace(arquivo)
    return ARQUIVO
