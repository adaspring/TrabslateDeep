import json
import requests
from bs4 import BeautifulSoup
import hashlib
import os
import concurrent.futures
from difflib import SequenceMatcher
import random
import time

# Predefined list of working LibreTranslate servers
LIBRETRANSLATE_SERVERS = [
    "https://translate.argosopentech.com",
    "https://libretranslate.de",
    "https://libretranslate.terraprint.co",
    "https://lt.vern.cc",
    "https://trans.zillyhuhn.com"
]

class HTMLTranslationProcessor:
    def __init__(self):
        self.translation_data = []
        self.current_id = 0
        self.placeholder_template = "<!-- TRANSLATION_ID_{} -->"
        
        self.translatable_config = {
            'elements': {
                'text_content': [
                    'title', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'a', 
                    'button', 'span', 'div', 'li', 'td', 'th', 'label', 'address',
                    'figcaption', 'caption', 'summary', 'blockquote', 'q', 'cite',
                    'dt', 'dd', 'legend', 'option', 'strong', 'em', 'mark', 'time'
                ]
            },
            'attributes': {
                'global': ['title', 'alt', 'placeholder']
            }
        }

    def extract_translatable(self, html_content):
        soup = BeautifulSoup(html_content, 'html.parser')
        
        for tag in self.translatable_config['elements']['text_content']:
            for element in soup.find_all(tag):
                if element.string and element.string.strip():
                    self._process_text_node(element)
        
        for attr in self.translatable_config['attributes']['global']:
            for element in soup.find_all(attrs={attr: True}):
                self._process_attribute(element, attr)
        
        return {
            'processed_html': str(soup),
            'translation_data': self.translation_data
        }

    def _process_text_node(self, element):
        text = element.string.strip()
        self._create_placeholder(element, text, 'text')

    def _process_attribute(self, element, attr):
        self._create_placeholder(element, element[attr], 'attribute', attr)

    def _create_placeholder(self, element, content, content_type, attr=None):
        placeholder = self.placeholder_template.format(self.current_id)
        
        entry = {
            'id': self.current_id,
            'type': content_type,
            'content': content,
            'context': {
                'tag': element.name,
                'attrs': element.attrs
            }
        }

        if content_type == 'attribute':
            element[attr] = placeholder
            entry['attribute'] = attr
        else:
            element.string.replace_with(placeholder)

        self.translation_data.append(entry)
        self.current_id += 1

class TranslationIntegrator:
    def __init__(self, deepl_key, libre_urls, chatgpt_key):
        self.deepl_key = deepl_key
        self.libre_urls = libre_urls or LIBRETRANSLATE_SERVERS
        self.chatgpt_key = chatgpt_key
        self.session = requests.Session()

    def translate_with_libre(self, text, target_lang):
        errors = []
        shuffled_servers = random.sample(self.libre_urls, len(self.libre_urls))
        
        for server in shuffled_servers:
            try:
                response = self.session.post(
                    f"{server}/translate",
                    json={
                        "q": text,
                        "source": "auto",
                        "target": target_lang,
                        "format": "text"
                    },
                    timeout=15
                )
                if response.status_code == 200:
                    return response.json()['translatedText']
                else:
                    errors.append(f"{server}: {response.status_code}")
            except Exception as e:
                errors.append(f"{server}: {str(e)}")
        
        raise Exception(f"All LibreTranslate servers failed: {', '.join(errors)}")

    def translate_with_deepl(self, text, target_lang):
        response = self.session.post(
            "https://api-free.deepl.com/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {self.deepl_key}"},
            data={
                "text": text,
                "target_lang": target_lang,
                "preserve_formatting": "1"
            }
        )
        return response.json()['translations'][0]['text']

    def resolve_with_chatgpt(self, original, libre, deepl, context):
        prompt = f"""Compare translations:
        Original: {original}
        Libre: {libre}
        DeepL: {deepl}
        Context: {json.dumps(context, indent=2)}"""
        
        response = self.session.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.chatgpt_key}"},
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2
            }
        )
        return response.json()

class HTMLTranslationManager:
    def __init__(self, processor, integrator):
        self.processor = processor
        self.integrator = integrator

    def process_file(self, html_file, target_lang, output_file):
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        extraction_result = self.processor.extract_translatable(html_content)
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = []
            for item in extraction_result['translation_data']:
                futures.append(executor.submit(
                    self._translate_item,
                    item,
                    target_lang
                ))
            
            results = [future.result() for future in concurrent.futures.as_completed(futures)]

        merged_html = self._merge_translations(
            extraction_result['processed_html'],
            results
        )
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(merged_html)
        
        return output_file

    def _translate_item(self, item, target_lang):
        try:
            libre = self.integrator.translate_with_libre(item['content'], target_lang)
            deepl = self.integrator.translate_with_deepl(item['content'], target_lang)
            
            analysis = self.integrator.resolve_with_chatgpt(
                original=item['content'],
                libre=libre,
                deepl=deepl,
                context=item['context']
            )
            
            return {
                'id': item['id'],
                'final_translation': analysis.get('combined_version', analysis['chosen_translation']),
                'analysis': analysis
            }
        except Exception as e:
            print(f"Error translating ID {item['id']}: {str(e)}")
            return {
                'id': item['id'],
                'final_translation': "[TRANSLATION_ERROR]",
                'analysis': {}
            }

    def _merge_translations(self, processed_html, translations):
        soup = BeautifulSoup(processed_html, 'html.parser')
        translation_map = {t['id']: t['final_translation'] for t in translations}
        
        for tag in soup.find_all(text=True):
            if 'TRANSLATION_ID_' in tag:
                trans_id = int(tag.split('_')[-1].strip())
                if trans_id in translation_map:
                    tag.replace_with(translation_map[trans_id])
        
        for element in soup.find_all(attrs=True):
            for attr in element.attrs:
                if isinstance(element[attr], str) and 'TRANSLATION_ID_' in element[attr]:
                    trans_id = int(element[attr].split('_')[-1].strip())
                    if trans_id in translation_map:
                        element[attr] = translation_map[trans_id]
        
        return str(soup)

if __name__ == "__main__":
    processor = HTMLTranslationProcessor()
    integrator = TranslationIntegrator(
        deepl_key=os.getenv('DEEPL_KEY'),
        libre_urls=os.getenv('LIBRE_URLS'),
        chatgpt_key=os.getenv('CHATGPT_KEY')
    )
    
    manager = HTMLTranslationManager(processor, integrator)
    
    result_file = manager.process_file(
        html_file='input.html',
        target_lang='fr',
        output_file='name-fr.html'
    )
    
    print(f"Final translated file created: {result_file}")
