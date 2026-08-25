# Documentação técnica

> Este documento é voltado para quem quer entender ou contribuir com a
> implementação. Para instalar e usar a integração, veja o [README](README.md).

Detalhes de implementação da integração não-oficial (HACS) da Enel São Paulo:
de onde vem cada campo, como funciona o login SAML reproduzido de
[www.enel.com.br/pt-saopaulo](https://www.enel.com.br/pt-saopaulo/login.html) e
como a integração alimenta o Painel de Energia do Home Assistant.

Não possui nenhum vínculo com a Enel. Use por sua conta e risco — é engenharia
reversa de uma API não documentada, que pode mudar sem aviso.

## Entidades criadas por unidade consumidora

- **Última fatura fechada** (`sensor`, R$) — valor e vencimento da conta em aberto mais recente.
- **Consumo do período** (`sensor`, kWh) — consumo do ciclo de faturamento atual.
- **Bandeira tarifária** (`sensor`) — verde / amarela / vermelha 1 / vermelha 2.
- **Medidor inteligente** (`binary_sensor`, diagnóstico) — indica se a UC tem smart meter.
- **Data da leitura atual** / **Data da próxima leitura** (`sensor`, timestamp).
- **Valor do medidor atual** (`sensor`, kWh, sem casas decimais) — leitura do
  registrador do medidor.
- **Valor do medidor anterior** (`sensor`, kWh, sem casas decimais) — calculado
  (leitura atual menos o consumo do período atual, arredondado), pois a API
  não devolve esse valor diretamente.
- **Link da fatura em PDF** (`sensor`, diagnóstico) — URL local pra abrir o PDF
  da fatura mais recente direto do navegador. **Leia o aviso de segurança
  abaixo antes de usar.**
- **Código Pix** (`sensor`) — o "copia e cola" da fatura em aberto, quando a
  Enel disponibiliza um pra ela. Some quando não há fatura pendente (mesmo
  critério do "Status da conta" abaixo).
- **QR Code Pix** (`image`) — a mesma informação do sensor acima, renderizada
  como QR Code (gerado localmente pela integração com a lib `qrcode`, a
  Enel não manda uma imagem pronta). Fica indisponível pelo mesmo motivo que
  o sensor de texto some: sem fatura pendente, não há o que mostrar.
- **Status da conta** (`sensor`) — `Conta pendente` se **qualquer** fatura
  consultada estiver em aberto (não só a mais recente), senão `Nenhuma conta
  em aberto`. Atributos: `quantidade_pendentes`, `valor_total_pendente`,
  `meses_pendentes`.
- **Fornecimento normal** (`binary_sensor`, diagnóstico) — `ligado` quando o
  fornecimento está normal, `desligado` quando há corte por falta de
  pagamento. Atributo `mensagem` quando disponível.
- **Mensagem de análise da fatura** (`sensor`) — resumo da Enel sobre a
  variação da conta mais recente (ex.: "Você gastou R$ 10,55 a menos que o
  mês anterior...").
- **Consumo médio diário** (`sensor`, kWh/d) e **Gasto médio diário**
  (`sensor`, R$/d) — médias do ciclo de faturamento mais recente.
- **Atualizar dados** (`button`, config) — força uma atualização imediata,
  sem esperar o próximo ciclo de 24h. O estado do próprio botão já é o horário
  do último aperto manual (padrão do HA); o atributo `ultima_atualizacao`
  guarda o horário da última atualização bem-sucedida, seja manual (por esse
  botão) ou automática (do ciclo periódico).

O sensor de consumo traz em `historico` os últimos meses de consumo (kWh) e valor
(R$) faturado, úteis para gráficos.

O `entity_id` de cada entidade segue o padrão `<domínio>.enel_sp_<login antes
do "@">_<chave>` (`domínio` é `sensor`, `binary_sensor`, `button` ou `image`) — por
exemplo, para o login `fulano@example.com`, a última fatura fechada fica em
`sensor.enel_sp_fulano_valor_ultima_fatura_fechada`, o fornecimento normal em
`binary_sensor.enel_sp_fulano_fornecimento_normal` e o botão de atualizar em
`button.enel_sp_fulano_atualizar_dados`. Se o login for CPF (sem `@`), usa
ele inteiro. Essa formatação só vale na primeira vez que a entidade é
criada; depois disso, renomear pela UI do HA é respeitado normalmente.

## De onde vêm os dados de cada sensor

Todas as chamadas usam o token (`enel-jwt-token`) e o `SID` obtidos no login.
As três primeiras vêm de uma leva de chamadas feita a cada atualização; o
`billanalysis` roda em seguida, sobre a fatura mais recente encontrada no
`getClientBills`.

**Faturas: `getClientBills`, não `portalinfo`.** O `portalinfo` também
devolve `ET_CONTAS[]`, mas sem o campo `QRCODE` — por isso o Código Pix nunca
vinha preenchido. O `getClientBills` devolve o mesmo formato de item (`BELNR`,
`SITUACAO`, `VENCIMENTO`, `ANO_MES_REF`, `MONTANTE`, `O_COD_BARRAS`, etc.) só
que com `QRCODE` populado, então substituiu o `portalinfo` como fonte de
`ET_CONTAS[]` (parâmetro extra: `I_QTDE_FAT: "99"`, quantidade de faturas a
retornar).

| Sensor (chave) | Endpoint (`Funcionalidad`) | Campo(s) de origem |
|---|---|---|
| Última fatura fechada (`valor_ultima_fatura_fechada`) | `getClientBills` | `ET_CONTAS[]` com `SITUACAO != "Paga"` (a mais recente por `VENCIMENTO`) → `MONTANTE`. Atributos: `VENCIMENTO`, `SITUACAO`, `ANO_MES_REF`, `COD_BARRAS_NOVO`/`O_COD_BARRAS` |
| Consumo do período (`consumo_periodo_atual`) | `getAnaliseConsumo` | `ET_INSTALACAO[]` (item com `ANLAGE` da UC) → `ATUAL_CONSUMO`. Atributos: `PERIODO` (mesma resposta) e `historico` (de `portalhistoryinfo` → `ET_MEDIA_CONS`) |
| Bandeira tarifária (`bandeira_tarifaria`) | `currentuser` | `E_BANDEIRA` |
| Medidor inteligente (`medidor_inteligente`, `binary_sensor`) | `currentuser` | `ET_INST[].SMARTMETER == "X"`. Atributo `numero_serie`: `ET_INST[].SERIE` |
| Data da leitura atual (`data_leitura_atual`) | `billanalysis` | `E_DT_LEITURA_ATUAL` (`YYYYMMDD`) |
| Data da próxima leitura (`data_proxima_leitura`) | `billanalysis` | `E_PROX_LEIT` (texto tipo "10 de Setembro", sem ano — o ano é calculado a partir de `E_DT_LEITURA_ATUAL`) |
| Valor do medidor atual (`valor_medidor_atual`) | `billanalysis` | `ET_MENU_RAPIDO[]` (item `ID == "LEITATUAL"`) → `VALOR2` |
| Valor do medidor anterior (`valor_medidor_anterior`) | `billanalysis` + `getAnaliseConsumo` | **Calculado**, não vem pronto: `VALOR2 - ATUAL_CONSUMO`, arredondado. Usa o mesmo `ATUAL_CONSUMO` do sensor "Consumo do período" (não `E_CONS_TOTAL`/`E_CONSTANTE` do `billanalysis`, que se referem ao ciclo de faturamento da última fatura, não ao período atual) |
| Link da fatura em PDF (`link_fatura_pdf`) | `generatepdf` | `E_BIN_FAT` (PDF em base64, decodificado e salvo em disco) — veja a seção abaixo |
| Código Pix (`codigo_pix`) | `getClientBills` | Mesmo item de `ET_CONTAS[]` da última fatura fechada → campo `QRCODE`. `None` se `next_due_bill` for `None` (nenhuma fatura com `SITUACAO != "Paga"`) — não basta o campo `QRCODE` existir na fatura, ela também precisa estar pendente (a Enel manda `QRCODE` até em faturas já pagas). Se passar de 255 caracteres (limite de estado do HA), o valor completo vai pro atributo `codigo_completo` |
| QR Code Pix (`qrcode_pix`, `image`) | — | Não chama a API: gera a imagem localmente (lib `qrcode`) a partir do mesmo texto do sensor "Código Pix" acima. Mesmo gate: sem `next_due_bill`, a entidade fica `unavailable` |
| Status da conta (`status_conta`) | `getClientBills` | Todo o `ET_CONTAS[]` (não só a mais recente) — `"Conta pendente"` se algum item tiver `SITUACAO != "Paga"` |
| Fornecimento normal (`fornecimento_normal`, `binary_sensor`) | `getAnaliseConsumo` | `ET_INSTALACAO[]` → `SUSPENSA != "X"` (invertido: `ligado` = normal). Atributo `mensagem`: `MSG_SUSPENSAO` |
| Mensagem de análise da fatura (`mensagem_analise_fatura`) | `billanalysis` | `E_MSG`/`DescripcionResultado` |
| Consumo médio diário (`consumo_medio_diario`) | `billanalysis` | `E_CONS_DIA` |
| Gasto médio diário (`gasto_medio_diario`) | `billanalysis` | `E_VALOR_DIA` |

A **fatura usada no `billanalysis` e no `generatepdf`** é sempre a de maior
`VENCIMENTO` dentro de `ET_CONTAS` (não a posição 0 do array — a API não
garante essa ordem).

O campo `QRCODE` é o mesmo que o site oficial usa para renderizar o QR Code
de pagamento (`chosenBill.QRCODE` no código-fonte deles). Confirmado numa
captura real que ele vem preenchido tanto em faturas pendentes quanto pagas;
se mesmo assim o sensor ficar vazio com uma fatura em aberto, pode ser que
essa conta/UC específica não tenha Pix habilitado pelo lado da Enel.

## Link da fatura em PDF — aviso de segurança

O PDF é salvo em `config/www/enel_sp/<anlage>.pdf` e o sensor guarda o
caminho relativo `/local/enel_sp/<anlage>.pdf` (sem host — o navegador
resolve em cima da origem de onde você estiver acessando o HA no momento).
O Home Assistant serve automaticamente tudo que está em `config/www/` nesse
caminho **sem exigir login** — é o mesmo mecanismo usado por outras
integrações para expor fotos de câmera, por exemplo, não é uma falha
específica desta integração.

Na prática isso quer dizer que **qualquer um com acesso à rede/porta do seu
HA consegue abrir esse link e ver a fatura** (nome, endereço, valores, código
de barras) sem entrar na sua conta do HA. Se sua instância estiver acessível
pela internet sem proteção adicional (proxy reverso com autenticação própria,
VPN, Nabu Casa com esse detalhe em mente, etc.), avalie se quer mesmo manter
esse sensor habilitado — dá pra desabilitá-lo em Configurações → Entidades.

Cada atualização substitui o arquivo pelo PDF da fatura mais recente daquele
momento; o nome do arquivo (`<anlage>.pdf`) é fixo por unidade consumidora,
então o link não muda com o tempo.

## Painel de Energia

Para UCs com medidor inteligente (`SMARTMETER: "X"`), a integração alimenta o
**Painel de Energia** do HA diretamente com estatísticas de longo prazo — sem
criar nenhum sensor visível para isso. A série fica disponível em
**Configurações → Dispositivos e Serviços → Estatísticas de longo prazo**
como `Enel SP consumo (<apelido/endereço da UC>)` (id interno
`enel_sp:consumption_<anlage>`) — adicione-a como fonte de consumo da rede no
Painel de Energia.

| Trecho da série | Endpoint (`Funcionalidad`) | Campo(s) de origem |
|---|---|---|
| Ciclos já fechados (base histórica) | `smartmetergetconsumptionchartdata` | `T_GRAPHIC_MONTH[].ConsumoKW`, um ponto no início de cada ciclo — só os que já fecharam de verdade (ver abaixo). Fallback: `ET_MEDIA_CONS` do `portalhistoryinfo`, se o medidor não trouxer `T_GRAPHIC_MONTH` |
| Ciclo em andamento, por hora | `smartmetergetconsumptionchartdata` | `T_GRAPHIC_HOUR[]` com `Register == "03"` (energia ativa) → `ConsumoKW` (kWh, apesar do nome), por `Date`+`Time` |

Os dois trechos vêm da **mesma chamada** ao `smartmetergetconsumptionchartdata`,
mas se comportam diferente quanto à janela de datas pedida
(`StartDate`/`EndDate`, hoje fixada nos últimos 7 dias): `T_GRAPHIC_MONTH`
devolve o histórico inteiro do medidor **independente dessa janela**
(confirmado numa captura real: uma janela de 8 dias pedida trouxe 11 meses de
histórico), enquanto `T_GRAPHIC_HOUR` só traz as horas dentro da janela
pedida. Por isso a base histórica usa `T_GRAPHIC_MONTH` (mais completo que o
`portalhistoryinfo`, que costuma trazer bem menos meses) e só a parte
horária do ciclo em andamento precisa do truque abaixo.

**O ciclo de leitura do medidor não é necessariamente alinhado ao mês
calendário** — o dia do mês em que ele fecha varia de conta pra conta e pode
até mudar ao longo do tempo (não é um valor fixo, então o código nunca
assume um dia específico). Numa captura real, por exemplo, o ciclo fechou em
10/08 — um ponto de `T_GRAPHIC_MONTH` rotulado "JUL" tinha `Date` em agosto,
porque é o ciclo que cobre majoritariamente julho mas só fecha em 10/08
daquela conta. A resposta traz um campo `LastReading` (`AAAAMMDD`) com a
data em que o ciclo mais recente fechou de verdade, seja qual for esse dia —
essa é a fronteira real usada nos dois lados: só entram na base histórica os
ciclos de `T_GRAPHIC_MONTH` com `Date <= LastReading`, e só entram na soma
horária os pontos de `T_GRAPHIC_HOUR` com `Date > LastReading`. Usar o mês
calendário (dia 1) como fronteira, em vez do `LastReading`, abriria uma
lacuna entre a virada do mês e o fechamento de fato do ciclo (quando esse
não cai no dia 1), e a seguir contaria esses mesmos dias em dobro assim que
o ciclo fechasse e entrasse como total mensal fechado.

Como `T_GRAPHIC_HOUR` é limitado à janela de 7 dias pedida, a integração
mantém um **cache local por UC** (arquivo em
`.storage/enel_sp_smartmeter_hours_<anlage>`) que vai fundindo cada nova
janela recebida, acumulando o ciclo em andamento hora a hora em vez de
descartar os dias que saem da janela a cada atualização. O cache é podado
pelo mesmo `LastReading`: assim que o ciclo fecha, os dias que ele cobre
saem do cache (já entram pelo total mensal fechado) e só sobra o que ainda
não fechou. Na prática isso só deixa uma lacuna real se a integração
instalar no meio de um ciclo (sem cobrir os dias anteriores à instalação) ou
se o Home Assistant ficar mais de 7 dias seguidos sem conseguir atualizar.

A cada atualização a série inteira é recalculada a partir do histórico
fechado + cache acumulado e reenviada (é seguro, o Home Assistant faz
*upsert* por horário), então não há risco de contar consumo em duplicidade
— e correções que a Enel eventualmente fizer em dados passados (leitura
estimada trocada por real, por exemplo) se propagam sozinhas no próximo
ciclo.

## Instalação

1. HACS → menu (⋮) → **Repositórios personalizados** → adicione a URL deste
   repositório como tipo **Integration**.
2. Instale "Enel São Paulo" e reinicie o Home Assistant.
3. Configurações → Dispositivos e Serviços → Adicionar integração → **Enel São Paulo**.
4. Informe o mesmo usuário (e-mail ou CPF) e senha usados em
   [www.enel.com.br](https://www.enel.com.br/pt-saopaulo/login.html). Se a conta
   tiver mais de uma unidade consumidora, você escolhe qual adicionar (repita o
   fluxo para adicionar as demais).

## Fluxo de login

O site usa um **WSO2 Identity Server** (`accounts.enel.com`) para SSO via
SAML2 e uma **API de negócio hospedada no Mulesoft** para os dados de
fatura/consumo. Não há endpoint de *refresh*: o token dura ~4h e cada
atualização periódica (padrão: 24h) repete o login inteiro.

```mermaid
sequenceDiagram
    participant I as Integração (HA)
    participant W as accounts.enel.com<br/>(WSO2 Identity Server)
    participant A as www.enel.com.br<br/>(Adobe AEM)
    participant M as Mulesoft<br/>(APIs de negócio)

    I->>W: GET /samlsso?spEntityID=ENEL_SP_WEB_BRA
    W-->>I: 302 (redireciona até A com ?sessionDataKey=...)

    I->>W: POST /samlsso<br/>username, password, tocommonauth, sessionDataKey
    W-->>I: 200 HTML com &lt;input name='SAMLResponse' value='...'&gt;

    Note over I: extrai o SAMLResponse<br/>(aspas simples, não duplas!)

    I->>A: POST /pt-saopaulo/login.html (Assertion Consumer Service)<br/>SAMLResponse
    A-->>I: sessão estabelecida (redirects internos: logininterceptor → post-login.html)

    I->>A: POST /bin/enel-br/pt-saopaulo/currentuser<br/>header sid (gerado no cliente)
    A-->>I: access_token (JWT ~4h) + unidades consumidoras<br/>(ANLAGE/VERTRAG/VKONT/PARTNER)

    Note over I,M: headers SID + enel-jwt-token em toda chamada seguinte

    I->>M: POST getAnaliseConsumo / getClientBills / portalhistoryinfo /<br/>billanalysis / smartmetergetconsumptionchartdata
    M-->>I: fatura, consumo, leitura do medidor
```

Detalhes que só aparecem numa captura real de rede, não em nenhuma
documentação da Enel:

- **`SAMLResponse` com aspas simples**: a página de login do WSO2 usa
  `name='SAMLResponse' value='...'`, não aspas duplas — a maioria dos
  exemplos de SAML por aí usa aspas duplas, então isso quebra um parser HTML
  ingênuo (foi exatamente o bug que corrigimos numa iteração anterior).
- **O header `SID`** não vem do servidor: o app oficial gera um UUID aleatório
  no navegador (`crypto.randomUUID()`) e reaproveita durante a sessão — não
  há validação nenhuma contra ele no backend. A integração faz o mesmo.
- **O header `enel-jwt-token`** é literalmente o `access_token` devolvido pelo
  `currentuser`.
- **Origin/Referer/Host** de cada requisição são copiados fielmente do que um
  navegador de verdade manda (o site fica atrás de um WAF Imperva que
  desconfia de requisições sem essa cara).

## Diagnóstico (login falhando?)

Se o login der erro, ative o log de depuração em `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.enel_sp: debug
```

Reinicie o HA e tente adicionar a integração de novo. Em **Configurações →
Sistema → Logs** vai aparecer, passo a passo, cada requisição do login (status
HTTP e, quando falhar, um trecho da resposta da Enel) — isso mostra exatamente
em qual etapa parou e, se for erro de credenciais/CAPTCHA/bloqueio do WAF, a
mensagem que a Enel devolveu. A senha nunca é logada.

## Desenvolvimento

```bash
pip install -r requirements_test.txt
pytest
```

Os testes usam fixtures anonimizadas em `tests/fixtures/`, obtidas de uma
captura de rede real com os dados pessoais removidos.
