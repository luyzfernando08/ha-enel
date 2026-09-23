# Enel SP Auth

[![Adicionar repositório de add-ons](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fluyzfernando08%2Fha-enel)

Add-on auxiliar da integração [Enel São Paulo](https://github.com/luyzfernando08/ha-enel).
Faz o login no portal da Enel usando um navegador real (Playwright/Chromium
headless), a única forma de passar pelo desafio anti-bot (WAF Imperva/
Incapsula) que bloqueia o login automatizado via requisições HTTP simples.

Só roda em instâncias **Home Assistant OS ou Supervised**. Não há suporte a
Core/Container standalone.

## O que ele faz (e o que não faz)

- Não tem tela de login própria: você continua informando usuário e senha
  no formulário normal de configuração da integração "Enel São Paulo", como
  sempre. Este add-on só recebe essas credenciais internamente, pela rede do
  Supervisor, na hora de logar.
- Não guarda faturas, consumo, sensores nem nada relacionado à conta — isso
  continua sendo responsabilidade exclusiva da integração. A única função
  deste add-on é resolver o desafio do WAF e devolver os cookies de sessão.
- Não precisa de nenhuma configuração depois de instalado: sem opções, sem
  usuário/senha no add-on. A porta HTTP (padrão `8978`) fica exposta ao
  host por conveniência de diagnóstico (dá pra chamar `/api/health` de fora
  da instância), remapeável na aba **Rede** do add-on — mas isso não afeta
  a comunicação entre a integração e o add-on, que sempre usa a porta
  interna fixa, independente do que estiver configurado aí. O endpoint
  `/api/login` continua exigindo a API key mesmo exposto.

## Instalação

1. Clique no botão **"Adicionar repositório de add-ons"** acima — ele abre a
   sua própria instância do Home Assistant com o repositório já preenchido
   (precisa da integração **My Home Assistant** ativa, que já vem habilitada
   por padrão). Se preferir adicionar manualmente: **Configurações → Add-ons
   → Loja de add-ons → menu (⋮) → Repositórios**, e cole:

   ```text
   https://github.com/luyzfernando08/ha-enel
   ```

2. Na loja de add-ons, procure por **"Enel SP Auth"** e clique em
   **Instalar**.
3. Depois de instalado, clique em **Iniciar**. Não há nada para configurar —
   nenhuma aba de "Configuração" precisa ser preenchida.
4. Confirme que o add-on está com o status **Em execução** (bolinha verde).

Pronto — a partir daqui, é só configurar (ou reautenticar) a integração
**Enel São Paulo** normalmente, do jeito que sempre foi feito:

[![Adicionar integração](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=enel_sp)

## Diagnóstico

- **Logs do add-on**: Configurações → Add-ons → Enel SP Auth → aba
  **Registro (Log)** — mostra o resultado de cada tentativa de login
  (sucesso, credencial inválida, bloqueio de WAF, timeout), sem nunca
  registrar a senha.
- Se a integração mostrar o aviso **"Add-on 'Enel SP Auth' indisponível"**
  em Configurações → Repairs, confirme que o add-on está instalado e com o
  status **Em execução**.
- Se aparecer o aviso **"Login bloqueado pelo WAF da Enel"**, não é um
  problema de usuário/senha — a integração continua tentando sozinha nos
  próximos ciclos. Se persistir por muitos dias seguidos, abra uma issue no
  [repositório principal](https://github.com/luyzfernando08/ha-enel/issues).

## Por que isso é um add-on separado, e não parte da integração?

Rodar um navegador (Chromium) dentro do processo do Home Assistant Core não
é viável: falta as bibliotecas de sistema necessárias, e integrações via
HACS não têm como instalar um navegador em runtime. Por isso essa etapa vive
num container próprio — mesmo padrão usado por outros projetos do
ecossistema Home Assistant que também precisam de um navegador real para
autenticação (ex.: [HAFamilyLink](https://github.com/noiwid/HAFamilyLink),
[selenium-homeassistant](https://github.com/davida72/selenium-homeassistant)).
