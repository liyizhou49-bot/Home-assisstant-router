import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec=importlib.util.spec_from_file_location('tested_providers',Path(__file__).parent/'custom_components/router_lab/providers.py')
providers=importlib.util.module_from_spec(spec);spec.loader.exec_module(providers)


class ProviderChecks(unittest.TestCase):
    def test_preset_cannot_redirect_credentials(self):
        self.assertEqual(providers.base_url('openai','https://untrusted.invalid'),'https://api.openai.com/v1')
        self.assertEqual(providers.base_url('gemini'),'https://generativelanguage.googleapis.com/v1beta/openai')

    def test_custom_requires_plain_http_endpoint(self):
        for url in ['file:///x','https://key:password@api.example','https://api.example/?key=x','https://api.example/#key']:
            with self.subTest(url=url),self.assertRaises(ValueError):providers.base_url('custom',url)

    def test_credentials_bound_to_full_base_url(self):
        entry=SimpleNamespace(unique_id='router_lab.provider.test',data={'base_url':'https://api.deepseek.com','api_key':'private-deepseek-test-value'},subentries={})
        hass=SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain:[entry]))
        self.assertFalse(providers.profile(hass,'openai')['llm_configured'])
        self.assertTrue(providers.profile(hass,'deepseek')['llm_configured'])
        self.assertFalse(providers.profile(hass,'custom','https://api.deepseek.com/different')['llm_configured'])
        self.assertNotIn('private-deepseek-test-value',repr(providers.profile(hass,'deepseek')))

    def test_unrelated_ha_account_is_not_adopted_or_changed(self):
        other=SimpleNamespace(entry_id='unrelated',unique_id=None,data={'base_url':'https://api.openai.com/v1','api_key':'unrelated-private-test'},subentries={})
        hass=SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain:[other]))
        self.assertFalse(providers.profile(hass,'openai')['llm_configured'])
        self.assertEqual(other.data['api_key'],'unrelated-private-test')

    def test_foreign_provider_does_not_get_deepseek_thinking_parameter(self):
        data={'extra_body':{'thinking':{'type':'disabled'}},'max_tokens':300}
        for name in ['openai','gemini','kimi','custom']:
            result=providers.request_options(name,'chat-model',data)
            self.assertNotIn('extra_body',result)
        self.assertIn('thinking',providers.request_options('deepseek','chat-model',data)['extra_body'])
        self.assertIn('thinking',data['extra_body'])

    def test_openai_token_parameter_and_reasoning_temperature(self):
        result=providers.request_options('openai','gpt-5',{'max_tokens':300,'temperature':0,'top_p':.9})
        self.assertEqual(result,{'max_completion_tokens':300})
        result=providers.request_options('openai','gpt-4.1-mini',{'max_tokens':300,'temperature':0})
        self.assertEqual(result['max_completion_tokens'],300);self.assertEqual(result['temperature'],0)

    def test_gemini_drops_user_field_only(self):
        result=providers.request_options('gemini','model',{'user':'private-conversation-id','tools':['definition'],'stream':True})
        self.assertNotIn('user',result);self.assertEqual(result['tools'],['definition']);self.assertTrue(result['stream'])

    def test_kimi_non_thinking_mode_and_fixed_sampling(self):
        result=providers.request_options('kimi','kimi-k2.6',{'temperature':0,'top_p':.5})
        self.assertEqual(result,{'extra_body':{'thinking':{'type':'disabled'}}})
        for model in ['kimi-k3','kimi-k2.7-code','kimi-k2-thinking']:
            with self.assertRaises(ValueError):providers.request_options('kimi',model,{})


if __name__=='__main__':unittest.main()
