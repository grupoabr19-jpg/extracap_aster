"""Regressoes dos filtros, com Chromium e HTML local sem acesso ao ERP."""
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright

from groq_client import GroqClient
from main import _apply_report_dates, _find_report_date_field, _find_visible_element, _find_with_groq_fallback, _open_report_page, _report_has_no_records


class ReportDateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.logger = logging.getLogger("test_report_dates")
        self.settings = SimpleNamespace(
            report_start_date="01/09/2026", report_end_date="07/09/2026",
            report_start_date_selector="", report_end_date_selector="",
            navigation_timeout_ms=500, groq_confidence_threshold=0.85,
        )

    def tearDown(self):
        self.page.close()

    def test_search_only_page_is_rejected_even_with_explicit_selector(self):
        self.page.set_content('<input readonly placeholder="Pesquisar..."><input placeholder="Pesquisar">')
        for selector in ("", "input"):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                _find_report_date_field(self.page, selector, "start_date", 100, self.logger)
        self.assertEqual(self.page.locator("input").nth(1).input_value(), "")

    def test_waits_for_form_after_search_inputs(self):
        self.page.set_content('''<input readonly placeholder="Pesquisar...">
          <script>setTimeout(() => {
            document.body.insertAdjacentHTML('beforeend',
              '<div><label>Data inicial<input type="date"></label><label>Data final<input type="date"></label></div>');
          }, 150)</script>''')
        self.settings.navigation_timeout_ms = 1500
        _apply_report_dates(self.page, self.settings, self.logger)
        self.assertEqual(self.page.locator('input[type=date]').nth(0).input_value(), "2026-09-01")
        self.assertEqual(self.page.locator('input[type=date]').nth(1).input_value(), "2026-09-07")

    def test_text_dates_with_accents_and_reversed_order(self):
        self.page.set_content('<div><label>Data final<input></label><label>Data início<input></label></div>')
        _apply_report_dates(self.page, self.settings, self.logger)
        self.assertEqual(self.page.locator('input').nth(0).input_value(), "07/09/2026")
        self.assertEqual(self.page.locator('input').nth(1).input_value(), "01/09/2026")

    def test_aster_labels_three_levels_above_inputs(self):
        self.page.set_content('''<section>
          <div><div class="title">Data Inicial *</div><div><div><input autocomplete="off"><div> </div></div></div></div>
          <div><div class="title">Data Final *</div><div><div><input autocomplete="off"><div> </div></div></div></div>
        </section>''')
        _apply_report_dates(self.page, self.settings, self.logger)
        self.assertEqual(self.page.locator('input').evaluate_all('els=>els.map(el=>el.value)'),
                         ['01/09/2026', '07/09/2026'])

    def test_ambiguous_dates_are_not_guessed(self):
        self.page.set_content('<input type="date"><input type="date">')
        with self.assertRaises(ValueError):
            _find_report_date_field(self.page, "", "start_date", 100, self.logger)

    def test_same_explicit_field_is_not_written_twice(self):
        self.page.set_content('<input id="period">')
        self.settings.report_start_date_selector = "#period"
        self.settings.report_end_date_selector = "#period"
        with self.assertRaisesRegex(ValueError, "mesmo campo"):
            _apply_report_dates(self.page, self.settings, self.logger)
        self.assertEqual(self.page.locator("input").input_value(), "")

    def test_readonly_date_is_rejected(self):
        self.page.set_content('<input type="date" id="start" readonly>')
        with self.assertRaises(ValueError):
            _find_report_date_field(self.page, "#start", "start_date", 100, self.logger)

    def test_groq_cannot_redirect_date_to_search(self):
        self.page.set_content('<input id="search" placeholder="Pesquisar">')
        client = Mock(enabled=True)
        client.ask_for_page_recovery.return_value = SimpleNamespace(
            status="ok", confidence=0.99,
            actions=[{"type": "fill", "target": {"selector": "#search"}}],
        )
        with self.assertRaises(ValueError):
            _find_with_groq_fallback(self.page, self.settings, "", "start_date", 100, self.logger, client)
        self.assertEqual(self.page.locator("#search").input_value(), "")

    def test_missing_end_field_prevents_partial_fill(self):
        self.page.set_content('<label>Data inicial<input type="date"></label>')
        self.settings.navigation_timeout_ms = 100
        with self.assertRaises(ValueError):
            _apply_report_dates(self.page, self.settings, self.logger)
        self.assertEqual(self.page.locator("input").input_value(), "")

    def test_invalid_period_is_rejected_before_accessing_page(self):
        self.settings.report_start_date = "08/09/2026"
        with self.assertRaisesRegex(ValueError, "posterior"):
            _apply_report_dates(self.page, self.settings, self.logger)

    def test_visible_element_waits_for_new_matches(self):
        self.page.set_content('''<input hidden>
            <script>setTimeout(() => document.body.insertAdjacentHTML('beforeend', '<input id="late">'), 150)</script>''')
        self.assertEqual(_find_visible_element(self.page, "input", 1500).get_attribute("id"), "late")

    def test_report_opens_from_all_instead_of_hidden_menu(self):
        self.page.route('https://aster.test/**', lambda route: route.fulfill(body='''
            <div hidden><span>Resumo Comercial</span></div>
            <button data-tab-id="all" onclick="document.querySelector('#card').hidden=false">Tudo</button>
            <button data-tab-id="Reports">Relatorios</button>
            <div id="card" hidden onclick="location.href='/ExecuteReport/test/report'">
              <div style="pointer-events:none"><span>Resumo Comercial</span></div>
              <button aria-label="Adicionar aos favoritos">Estrela</button>
            </div>
        '''))
        self.page.goto('https://aster.test/Workspace')
        self.settings.report_card_selector = 'text=Resumo Comercial'
        self.settings.navigation_timeout_ms = 2000
        report = _open_report_page(self.page, self.settings, self.logger)
        self.assertIn('/ExecuteReport/', report.url)

    def test_report_click_without_navigation_is_rejected(self):
        self.page.set_content('<button data-tab-id="all">Tudo</button><span>Resumo Comercial</span>')
        self.settings.report_card_selector = 'text=Resumo Comercial'
        self.settings.navigation_timeout_ms = 100
        with self.assertRaisesRegex(ValueError, 'nao abriu'):
            _open_report_page(self.page, self.settings, self.logger)

    def test_no_records_message_is_detected(self):
        self.page.set_content('<main><p>Nenhum registro encontrado</p></main>')
        self.assertTrue(_report_has_no_records(self.page, 100))


class GroqRetryTests(unittest.TestCase):
    def test_wait_responses_stop_at_maximum_rounds(self):
        client = GroqClient(api_key="fake-test-key", logger=Mock())
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content":
            '{"status":"retry","confidence":0.9,"actions":[{"type":"wait"}]}'}}]}
        with patch("main._find_report_date_field", side_effect=ValueError("missing")), \
             patch("main._capture_page_diagnostic", return_value={}), \
             patch("groq_client.requests.post", return_value=response) as post:
            with self.assertRaises(ValueError):
                _find_with_groq_fallback(Mock(), SimpleNamespace(groq_confidence_threshold=0.85),
                                         "", "start_date", 100, Mock(), client)
        self.assertEqual(post.call_count, client.MAX_ROUNDS)


if __name__ == "__main__":
    unittest.main()
