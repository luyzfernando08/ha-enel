"""Testes unitários do cliente do add-on enel_sp_auth (funções puras, sem
depender do pacote `homeassistant` — ver tests/conftest.py)."""
import aiohttp
import pytest
from yarl import URL

from custom_components.enel_sp.addon_client import cookies_to_simplecookie


def test_cookies_to_simplecookie_sets_domain_and_path_per_item():
    playwright_cookies = [
        {"name": "JSESSIONID", "value": "aaa", "domain": "accounts.enel.com", "path": "/"},
        {"name": "incap_ses", "value": "bbb", "domain": ".enel.com.br", "path": "/", "secure": True},
    ]

    jar = cookies_to_simplecookie(playwright_cookies)

    assert jar["JSESSIONID"].value == "aaa"
    assert jar["JSESSIONID"]["domain"] == "accounts.enel.com"
    assert jar["incap_ses"]["domain"] == ".enel.com.br"
    assert jar["incap_ses"]["secure"] is True


def test_cookies_to_simplecookie_skips_incomplete_entries():
    jar = cookies_to_simplecookie([{"name": "no_value"}, {"value": "no_name"}])
    assert len(jar) == 0


@pytest.mark.asyncio
async def test_cookies_apply_correctly_across_two_domains_via_real_cookie_jar():
    """Regressão: cookies de accounts.enel.com e www.enel.com.br precisam
    conviver na mesma aiohttp.CookieJar sem vazar entre os dois domínios,
    já que as etapas do login usam origens diferentes."""
    playwright_cookies = [
        {"name": "JSESSIONID", "value": "aaa", "domain": "accounts.enel.com", "path": "/"},
        {"name": "incap_ses", "value": "bbb", "domain": ".enel.com.br", "path": "/"},
        {"name": "nlbi", "value": "ccc", "domain": "www.enel.com.br", "path": "/"},
    ]

    jar = aiohttp.CookieJar()
    jar.update_cookies(cookies_to_simplecookie(playwright_cookies))

    accounts_cookies = jar.filter_cookies(URL("https://accounts.enel.com/samlsso"))
    www_cookies = jar.filter_cookies(URL("https://www.enel.com.br/pt-saopaulo/login.html"))

    assert set(accounts_cookies) == {"JSESSIONID"}
    assert set(www_cookies) == {"incap_ses", "nlbi"}
