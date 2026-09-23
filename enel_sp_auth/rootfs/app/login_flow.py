"""Login SAML no portal da Enel SP via Playwright headless.

Reproduz, dentro de um navegador real, as etapas do login que o WAF
(Imperva/Incapsula) desafia quando feitas sem executar JavaScript:

1. Navega até o ponto de entrada do SAML SSO (``accounts.enel.com/samlsso``),
   deixando o desafio de fingerprinting do WAF rodar naturalmente.
2. Preenche e envia usuário/senha no formulário do WSO2 Identity Server.
3. Aguarda o auto-submit do ``SAMLResponse`` resultante para a Assertion
   Consumer Service URL do site (``www.enel.com.br/pt-saopaulo/login.html``)
   — a etapa que hoje toma ``403`` quando feita via ``aiohttp`` puro.

Ao final de um login bem-sucedido, devolve os cookies de sessão do contexto
do navegador (cobrindo os dois domínios percorridos), prontos para a
integração aplicar na sua própria sessão ``aiohttp``.

A heurística de classificação de erro abaixo (``invalid_credentials`` vs
``waf_blocked`` vs ``timeout``/``error``) é uma primeira aproximação — só
pode ser calibrada de fato observando o comportamento real e atual do
desafio Incapsula numa instância HAOS (os seletores de formulário e as
condições de bloqueio podem precisar de ajuste).

Cada chamada loga uma etapa por vez (nunca usuário/senha, ver ``_log_step``
abaixo) prefixada por um ``request_id`` curto, pra dar pra acompanhar uma
tentativa específica no log mesmo se outra rodar logo depois.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright

_LOGGER = logging.getLogger(__name__)

_SP_ENTITY_ID = "ENEL_SP_WEB_BRA"
SAMLSSO_URL = f"https://accounts.enel.com/samlsso?spEntityID={_SP_ENTITY_ID}"
ACS_URL = "https://www.enel.com.br/pt-saopaulo/login.html"

_NAVIGATION_TIMEOUT_MS = 60_000
_FIELD_TIMEOUT_MS = 5_000
# Tamanho do trecho de HTML logado quando um seletor não é encontrado, pra
# diagnosticar sem lotar o log — mesma ideia do `_DEBUG_SNIPPET_LEN` que
# `auth.py` já usava pra isso antes dessas etapas migrarem pro add-on. Agora
# só corta o <body> (ver `_log_page_snapshot`), então dá pra ser mais generoso.
_DEBUG_SNIPPET_LEN = 6000

# Confirmado numa captura real (ver log de diagnóstico): a página de login é
# uma SPA Angular (`app-enel-basiclogin`) cujo formulário de verdade fica
# dentro de <form id="formlogin" action="https://accounts.enel.com/samlsso?...">.
# Os campos visíveis que o usuário digita têm `name="data"` duplicado nos
# dois (username E senha!) — só o `id` (`email`/`password`) diferencia um do
# outro — e existe, à parte, um <input name="username" type="hidden"> que só
# espelha o valor pro POST nativo (nunca fica visível, por isso as duas
# primeiras tentativas com `:visible` sempre falhavam). Os ids abaixo são a
# fonte primária; as variantes por `name`/`formcontrolname` ficam como
# fallback caso a Enel troque o id no futuro.
_USERNAME_SELECTORS = [
    "#email:visible",
    "input[formcontrolname='username']:visible",
    "input[name='username']:visible",
]
_PASSWORD_SELECTORS = [
    "#password:visible",
    "input[formcontrolname='password']:visible",
]
# NUNCA usar um seletor de submit sem escopo pro form: a página tem outros
# <button> fora do formulário de login — o botão de "modo contraste" do
# cabeçalho (`#button-color-one`) e o "X" de fechar o modal — que vêm ANTES
# do botão de login de verdade na ordem do DOM. Um seletor genérico clicaria
# num desses por engano em vez de enviar o formulário.
#
# Repare que NÃO filtramos por `[type='submit']`: um <button> sem o atributo
# `type` no HTML já se comporta como submit por padrão do próprio HTML, mas
# o seletor CSS `[type='submit']` só bate com o atributo escrito de verdade
# no markup — não com esse comportamento implícito. Os três botões da
# página têm `type` "submit" quando lido via JS (`el.type`, que aplica o
# default do DOM), mas nenhum tem o atributo escrito, então
# `button[type='submit']` nunca batia com nada aqui.
_SUBMIT_SELECTORS = [
    "form#formlogin button:visible",
]

ErrorType = Literal["invalid_credentials", "waf_blocked", "timeout", "error"]


@dataclass
class LoginResult:
    success: bool
    cookies: list[dict[str, Any]] = field(default_factory=list)
    error_type: ErrorType | None = None
    message: str = ""


def _log_step(request_id: str, message: str, *args: object) -> None:
    """Log de uma etapa do fluxo — nunca recebe usuário/senha, só nomes de
    etapa e dados não sensíveis (URL, status HTTP, contagem de cookies)."""
    _LOGGER.info("[%s] " + message, request_id, *args)


async def async_login(username: str, password: str, request_id: str = "-") -> LoginResult:
    """Executa o login inteiro num Chromium headless isolado — um
    ``browser_context`` novo por chamada, sempre descartado ao final,
    então nenhum estado (cookies, cache) vaza entre contas diferentes."""
    started_at = time.monotonic()
    _log_step(request_id, "Iniciando Chromium headless")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        waf_blocked_response = False

        # Loga requisição/resposta/falha só pros hosts relevantes do fluxo
        # de login (accounts.enel.com e www.enel.com.br/bin) — o resto é
        # ruído de assets estáticos e rastreadores de terceiros (Adobe
        # Analytics, Dynatrace), que só atrapalha a leitura do log em uso
        # normal. Se precisar recalibrar de novo no futuro, tirar esse
        # filtro temporariamente ajuda a enxergar tudo sem exceção.
        _RELEVANT_HOSTS = ("accounts.enel.com", "enel-br")

        def _on_request(request: Any) -> None:
            if any(host in request.url for host in _RELEVANT_HOSTS):
                _log_step(request_id, "-> %s %s", request.method, request.url)

        def _on_response(response: Any) -> None:
            nonlocal waf_blocked_response
            if response.url.startswith(ACS_URL) and response.status == 403:
                _log_step(request_id, "WAF respondeu 403 em %s", response.url)
                waf_blocked_response = True
            if any(host in response.url for host in _RELEVANT_HOSTS):
                _log_step(request_id, "<- %s %s", response.status, response.url)

        def _on_request_failed(request: Any) -> None:
            # Dispara quando a requisição nunca chega a virar uma resposta
            # HTTP (conexão recusada/resetada, abortada, etc.) — diferente
            # de `_on_response`, que só cobre respostas HTTP de verdade.
            if any(host in request.url for host in _RELEVANT_HOSTS):
                _log_step(
                    request_id, "-x %s %s (falha de rede: %s)",
                    request.method, request.url, request.failure,
                )

        def _on_console(msg: Any) -> None:
            # Erros/exceções que o próprio Angular jogou no console do
            # navegador — mais confiável que inferir pelos eventos de rede
            # pra chamadas tipo `fetch(..., {keepalive: true})`, que o
            # Playwright às vezes não reporta via `response`/`requestfailed`
            # mesmo tendo sido concluídas de verdade no navegador.
            if msg.type in ("error", "warning"):
                _log_step(request_id, "console.%s: %s", msg.type, msg.text)

        def _on_page_error(err: Any) -> None:
            _log_step(request_id, "Exceção JS não tratada na página: %s", err)

        page.on("request", _on_request)
        page.on("response", _on_response)
        page.on("requestfailed", _on_request_failed)
        page.on("console", _on_console)
        page.on("pageerror", _on_page_error)

        try:
            result = await _async_run_login(page, username, password, request_id)
            if result.success:
                result.cookies = await context.cookies()
                _log_step(
                    request_id, "Login concluído com sucesso (%d cookies, %.1fs)",
                    len(result.cookies), time.monotonic() - started_at,
                )
            elif waf_blocked_response and result.error_type == "error":
                # A navegação genérica falhou, mas já vimos um 403 explícito
                # do WAF no ACS — reclassifica como bloqueio, não erro genérico.
                result.error_type = "waf_blocked"
                result.message = result.message or "WAF respondeu 403 no ACS"
            if not result.success:
                _log_step(
                    request_id, "Login falhou: error_type=%s (%.1fs)",
                    result.error_type, time.monotonic() - started_at,
                )
            return result
        finally:
            await context.close()
            await browser.close()
            _log_step(request_id, "Chromium encerrado")


_LIST_INPUTS_JS = """
els => els.map(el => ({
    tag: el.tagName.toLowerCase(),
    type: el.type || null,
    name: el.name || null,
    id: el.id || null,
    formcontrolname: el.getAttribute('formcontrolname'),
    placeholder: el.placeholder || null,
    text: (el.innerText || el.textContent || '').trim().slice(0, 80),
    classes: el.className || null,
    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
}))
"""


async def _log_page_snapshot(page: Page, context: str, request_id: str) -> None:
    """Loga a URL atual, a lista de todo `<input>`/`<button>` da página (com
    atributos, texto visível e se está visível) e o HTML do formulário de
    login — só chamado quando algo inesperado acontece, pra diagnosticar
    sem precisar reproduzir o problema de novo às cegas.

    A lista de inputs via JS é a parte mais útil (inclui o texto visível de
    cada botão — sem isso não dá pra diferenciar dois `<button>` sem
    `id`/`name`, como aconteceu numa captura real: um deles não era o botão
    de login). O HTML fica restrito a `#formlogin` (não o `<body>` inteiro):
    dumpar o body inteiro sempre desperdiçava o snippet no cabeçalho da
    página, que já é sempre o mesmo e não ajuda em nada.
    """
    try:
        inputs = await page.eval_on_selector_all("input, button", _LIST_INPUTS_JS)
    except Exception as err:  # noqa: BLE001 - log de diagnóstico não pode derrubar o fluxo
        inputs = f"<falha ao listar inputs: {err}>"
    try:
        form_html = await page.locator("#formlogin").inner_html()
        html_label = "HTML de #formlogin"
    except Exception:  # noqa: BLE001
        try:
            form_html = await page.locator("body").inner_html()
            html_label = "HTML do <body> (#formlogin não encontrado)"
        except Exception:  # noqa: BLE001
            form_html, html_label = "<não foi possível capturar o HTML>", "HTML"
    _LOGGER.warning(
        "[%s] %s (url=%s)\ncampos encontrados: %s\n%s (%d chars):\n%s",
        request_id, context, page.url, inputs, html_label, _DEBUG_SNIPPET_LEN,
        form_html[:_DEBUG_SNIPPET_LEN],
    )



async def _async_first_visible(page: Page, selectors: list[str], timeout_ms: int):
    """Tenta cada seletor candidato em ordem, devolvendo o primeiro que
    resolve pra um elemento visível. `None` se nenhum aparecer a tempo."""
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except PlaywrightTimeoutError:
            continue
    return None


_OUTCOME_TIMEOUT_S = 20.0
_OUTCOME_POLL_INTERVAL_S = 0.5


async def _wait_for_submit_outcome(page: Page, initial_url: str, request_id: str) -> str:
    """Espera um desfecho REAL e observável do clique em "Acessar", em vez
    de confiar em `networkidle`.

    Motivo: numa captura real, o POST de `validatePassword` (chamado pelo
    Angular antes de qualquer coisa ir pro WSO2) nunca gerou evento de
    resposta nem de falha no Playwright — só o pedido (`request`) — enquanto
    o botão de envio continuava visivelmente desabilitado com um ícone de
    "carregando" bem depois de `networkidle` já ter disparado. Ou seja,
    `networkidle` pode considerar a rede "parada" mesmo com uma chamada
    `fetch(..., {keepalive: true})` (usada por telemetria e, aparentemente,
    por essa validação) ainda pendente do ponto de vista do próprio app —
    limitação conhecida de rastreamento de rede via CDP pro Playwright, não
    um bloqueio de verdade.

    Poll em vez disso por um desfecho que o próprio app deixa visível:
      - "navigated": a URL mudou (avançou pro pós-login ou foi redirecionado
        pelo WSO2/ACS).
      - "form_reset": o botão de envio voltou a ficar habilitado — sinal de
        que o Angular concluiu a tentativa (com ou sem sucesso) e liberou o
        usuário pra tentar de novo. É esse o sinal real de "credenciais
        rejeitadas", não a mera reaparição do campo de senha (que já estava
        lá o tempo todo, só ficou escondido atrás do estado de carregamento).
      - "stuck": nenhum dos dois aconteceu dentro do timeout — o app ficou
        preso esperando uma resposta que nunca chegou. Isso é bem diferente
        de "credenciais inválidas": é um travamento (rede ou bloqueio
        silencioso), e deve ser reportado como tal.
    """
    # Timeout curto e explícito pra CADA leitura de estado (bem menor que o
    # padrão de 30s do Playwright): numa captura real, `is_enabled()` ficou
    # preso os 30s inteiros durante uma transição de página, sem levantar
    # nenhum dos erros que a gente já tratava como transitório — travando a
    # requisição inteira. Como o loop já tem seu próprio prazo total
    # (`_OUTCOME_TIMEOUT_S`), qualquer falha de leitura aqui — timeout,
    # navegação, contexto destruído, o que for — só significa "inconclusivo
    # nesta volta", nunca deve escapar do loop.
    _STATE_READ_TIMEOUT_MS = 1_000

    deadline = time.monotonic() + _OUTCOME_TIMEOUT_S
    while time.monotonic() < deadline:
        if page.url != initial_url:
            return "navigated"
        try:
            submit = page.locator("form#formlogin button").first
            if await submit.is_enabled(timeout=_STATE_READ_TIMEOUT_MS):
                return "form_reset"
        except Exception:  # noqa: BLE001 - qualquer falha aqui é só "tenta de novo"
            pass
        await asyncio.sleep(_OUTCOME_POLL_INTERVAL_S)
    return "stuck"


async def _async_run_login(
    page: Page, username: str, password: str, request_id: str
) -> LoginResult:
    _log_step(request_id, "Etapa 1/3: navegando para o SAMLSSO (%s)", SAMLSSO_URL)
    try:
        await page.goto(SAMLSSO_URL, wait_until="load", timeout=_NAVIGATION_TIMEOUT_MS)
    except PlaywrightTimeoutError as err:
        _log_step(request_id, "Timeout navegando para o SAMLSSO")
        return LoginResult(success=False, error_type="timeout", message=str(err))
    except Exception as err:  # noqa: BLE001 - qualquer falha aqui deve virar resposta pro chamador, não derrubar o add-on
        _log_step(request_id, "Erro navegando para o SAMLSSO: %s", err)
        return LoginResult(success=False, error_type="error", message=str(err))
    _log_step(request_id, "SAMLSSO carregado (url final=%s)", page.url)

    # A página final é uma SPA Angular: o evento "load" dispara antes do
    # Angular terminar de inicializar e renderizar o formulário de verdade
    # (o app ainda faz chamadas JS depois disso), então sem essa espera os
    # seletores de campo abaixo podem procurar antes do formulário existir.
    try:
        await page.wait_for_load_state("networkidle", timeout=_NAVIGATION_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        _log_step(request_id, "networkidle não atingido após o load do SAMLSSO, seguindo mesmo assim")

    _log_step(request_id, "Etapa 2/3: preenchendo e enviando o formulário de login")
    try:
        # O form tem um rádio "login_options" (Email/Celular) que o cliente
        # aiohttp antigo sempre mandava explicitamente como "Email" no
        # payload. Sem selecionar isso aqui, o backend pode não saber que o
        # texto em `data` é um e-mail (não um telefone) e rejeitar mesmo com
        # a senha certa — reproduzido numa captura real: credenciais
        # confirmadas corretas (login manual funcionando) ainda assim
        # voltaram como "formulário reapareceu" sem esse clique.
        email_radio = page.locator("#Email:visible").first
        try:
            await email_radio.wait_for(state="visible", timeout=_FIELD_TIMEOUT_MS)
            await email_radio.check()
            _log_step(request_id, "Opção de login 'Email' selecionada")
        except PlaywrightTimeoutError:
            _log_step(
                request_id,
                "Rádio 'Email' (#Email) não encontrado/visível — seguindo sem selecionar",
            )

        username_field = await _async_first_visible(page, _USERNAME_SELECTORS, _FIELD_TIMEOUT_MS)
        if username_field is None:
            await _log_page_snapshot(page, "Campo de usuário não encontrado", request_id)
            return LoginResult(
                success=False, error_type="error",
                message="Nenhum seletor de usuário conhecido apareceu visível na página",
            )
        await username_field.fill(username)
        _log_step(request_id, "Campo de usuário preenchido")

        password_field = await _async_first_visible(page, _PASSWORD_SELECTORS, _FIELD_TIMEOUT_MS)
        if password_field is None:
            await _log_page_snapshot(page, "Campo de senha não encontrado", request_id)
            return LoginResult(
                success=False, error_type="error",
                message="Nenhum seletor de senha conhecido apareceu visível na página",
            )
        await password_field.fill(password)
        _log_step(request_id, "Campo de senha preenchido")

        submit_button = await _async_first_visible(page, _SUBMIT_SELECTORS, _FIELD_TIMEOUT_MS)
        if submit_button is None:
            await _log_page_snapshot(page, "Botão de envio não encontrado", request_id)
            return LoginResult(
                success=False, error_type="error",
                message="Nenhum seletor de botão de envio conhecido apareceu visível na página",
            )
        initial_url = page.url
        _log_step(request_id, "Enviando formulário (clique no botão de submit)")
        await submit_button.click()
    except PlaywrightTimeoutError as err:
        _log_step(request_id, "Timeout preenchendo/enviando o formulário")
        return LoginResult(success=False, error_type="timeout", message=str(err))
    except Exception as err:  # noqa: BLE001
        _log_step(request_id, "Erro preenchendo/enviando o formulário: %s", err)
        return LoginResult(success=False, error_type="error", message=str(err))

    _log_step(request_id, "Etapa 3/3: aguardando um desfecho real do envio")
    outcome = await _wait_for_submit_outcome(page, initial_url, request_id)
    _log_step(request_id, "Desfecho do envio: %s (url=%s)", outcome, page.url)

    if outcome == "stuck":
        await _log_page_snapshot(
            page, "Envio travado — nem navegou nem o botão voltou a ficar habilitado",
            request_id,
        )
        return LoginResult(
            success=False, error_type="timeout",
            message=(
                "O formulário ficou preso processando o envio sem nunca navegar "
                "nem reabilitar o botão — provável travamento de rede/WAF, não "
                "confirma nem credencial inválida nem sucesso"
            ),
        )

    if outcome == "form_reset":
        await _log_page_snapshot(
            page, "Botão de envio reabilitado na mesma página (credenciais rejeitadas)",
            request_id,
        )
        return LoginResult(
            success=False,
            error_type="invalid_credentials",
            message="O formulário voltou a ficar disponível para nova tentativa na mesma página",
        )

    # outcome == "navigated": pode ainda haver mais saltos encadeados depois
    # desse primeiro (WSO2 -> ACS -> redirects internos) — espera a rede
    # quietar antes de decidir o resultado final pela URL.
    try:
        await page.wait_for_load_state("networkidle", timeout=_NAVIGATION_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        _log_step(request_id, "networkidle não atingido a tempo, seguindo mesmo assim")
    _log_step(request_id, "Navegação pós-envio concluída (url=%s)", page.url)

    if "/pt-saopaulo/login.html" in page.url:
        # Ainda na (ou de volta pra) página de login/ACS sem ter avançado
        # pro pós-login — sessão não foi estabelecida.
        _log_step(request_id, "Navegação final ainda em login.html — bloqueio de WAF")
        return LoginResult(
            success=False,
            error_type="waf_blocked",
            message=f"Navegação final ficou em {page.url}, sessão não estabelecida",
        )

    _log_step(request_id, "Sessão estabelecida (url final=%s)", page.url)
    return LoginResult(success=True)
