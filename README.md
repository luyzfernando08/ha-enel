# Enel São Paulo para Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/luyzfernando08/ha-enel)](https://github.com/luyzfernando08/ha-enel/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Integração não-oficial para trazer a fatura, o consumo de energia e a bandeira
tarifária da **Enel São Paulo** para dentro do Home Assistant.

Não possui nenhum vínculo com a Enel. Use por sua conta e risco.

[![Adicionar repositório no HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=luyzfernando08&repository=ha-enel&category=integration)
[![Adicionar integração](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=enel_sp)

## O que a integração traz

Para cada unidade consumidora cadastrada:

- Valor e vencimento da **última fatura**
- **Consumo** de energia do período atual (kWh)
- **Bandeira tarifária** vigente (verde / amarela / vermelha 1 / vermelha 2)
- **Status da conta** (em dia ou com fatura pendente) e o **total de contas em aberto**
- Se o **fornecimento** está normal ou cortado por falta de pagamento
- **Código Pix** (texto e QR Code) da fatura em aberto, quando disponível —
  some quando não há conta pendente
- Link para abrir a **fatura em PDF** (veja o aviso de segurança abaixo)
- Consumo e gasto médios diários
- Datas de leitura do medidor (atual e próxima)
- Um botão para **atualizar os dados** na hora, sem esperar o ciclo automático

Para unidades com **medidor inteligente**, o histórico de consumo também é
enviado direto para o **Painel de Energia** do Home Assistant.

Os dados são atualizados automaticamente a cada 24 horas (intervalo
conservador, para evitar bloqueios).

## Instalação

1. Clique no botão **"Adicionar repositório no HACS"** acima (ou, no HACS,
   vá em menu (⋮) → **Repositórios personalizados** e adicione a URL deste
   repositório como tipo **Integration**).
2. Instale "Enel São Paulo" e reinicie o Home Assistant.
3. Clique no botão **"Adicionar integração"** acima (ou vá em
   Configurações → Dispositivos e Serviços → Adicionar integração →
   **Enel São Paulo**).
4. Informe o mesmo usuário (e-mail ou CPF) e senha que você usa em
   [www.enel.com.br](https://www.enel.com.br/pt-saopaulo/login.html). Se a
   conta tiver mais de uma unidade consumidora, repita o passo 3 para
   adicionar as demais.

## Aviso de segurança: link da fatura em PDF

O link da fatura em PDF fica acessível **sem exigir login no Home Assistant**
para quem tiver acesso à rede/porta da sua instância — o mesmo mecanismo que
outras integrações usam para expor, por exemplo, fotos de câmera.

Se a sua instância estiver acessível pela internet sem proteção adicional
(proxy com autenticação própria, VPN, etc.), avalie se quer manter esse
sensor habilitado — dá para desativá-lo em Configurações → Entidades.

## Problemas para fazer login?

Ative o log de depuração para ver em qual etapa o login está falhando:

```yaml
logger:
  default: warning
  logs:
    custom_components.enel_sp: debug
```

Reinicie o Home Assistant, tente adicionar a integração de novo e confira o
log em Configurações → Sistema → Logs. A senha nunca é registrada no log.

## Documentação técnica

Detalhes sobre o fluxo de login, de onde vem cada campo e como o Painel de
Energia é alimentado estão em [TECHNICAL.md](TECHNICAL.md) — útil para quem
quer entender ou contribuir com o código.
