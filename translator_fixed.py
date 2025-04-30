import json
import requests
from bs4 import BeautifulSoup
import hashlib
import os
import concurrent.futures
import random
import time
import sys
from pathlib import Path
from typing import Dict, List, Optional
import inquirer

DEFAULT_TARGET_LANG = 'fr'
EXCLUDED_LANG_SUFFIXES = ['fr', 'es', 'de']
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

    def extract_translatable(self, html_content: str) -> Dict:
        soup = BeautifulSoup(html_content, 'html.parser')
        for tag in self.translatable_config['elements']['text_content']:
            for element in soup.find_all(tag):
                if element.string and element.string.strip():
                    self._process_text_node(element)
        for attr in self.translatable_config['attributes']['global']:
            for element in soup.find_all(attrs={attr: True}):
                self._process_attribute(element, attr)
        return {'processed_html': str(soup), 'translation_data': self.translation_data}

    def _process_text_node(self, element: BeautifulSoup) -> None:
        text = element.string.strip()
        self._create_placeholder(element, text, 'text')

    def _process_attribute(self, element: BeautifulSoup, attr: str) -> None:
        self._create_placeholder(element, element[attr], 'attribute', attr)

    def _create_placeholder(self, element: BeautifulSoup, content: str, content_type: str, attr: Optional[str] = None) -> None:
        placeholder = self.placeholder_template.format(self.current_id)
        entry = {
            'id': self.current_id,
            'type': content_type,
            'content': content,
            'context': {'tag': element.name, 'attrs': element.attrs}
        }
        if content_type == 'attribute':
            element[attr] = placeholder
            entry['attribute'] = attr
        else:
            element.string.replace_with(placeholder)
        self.translation_data.append(entry)
        self.current_id += 1

class TranslationIntegrator:
    def __init__(self, deepl_key: str, libre_urls: List[str], chatgpt_key: str):
        self.deepl_key = deepl_key
        self.libre_urls = libre_urls or LIBRETRANSLATE_SERVERS
        self.chatgpt_key = chatgpt_key
        self.session = requests.Session()

    def translate_with_libre(self, text: str, target_lang: str) -> str:
        shuffled_servers = random.sample(self.libre_urls, len(self.libre_urls))
        for server in shuffled_servers:
            try:
                response = self.session.post(
                    f"{server}/translate",
                    json={"q": text, "source": "auto", "target": target_lang, "format": "text"},
                    timeout=20
                )
                if response.status_code == 200:
                    return response.json()['translatedText']
            except Exception:
                continue
        raise Exception("All LibreTranslate servers failed")

    def translate_with_deepl(self, text: str, target_lang: str) -> str:
        response = self.session.post(
            "https://api-free.deepl.com/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {self.deepl_key}"},
            data={"text": text, "target_lang": target_lang, "preserve_formatting": "1"}
        )
        data = response.json()
        if 'translations' not in data:
            raise ValueError("Invalid DeepL response format")
        return data['translations'][0]['text']

    def resolve_with_chatgpt(self, original: str, libre: str, deepl: str, context: Dict) -> str:
        prompt = f"""Choose the best French translation based on the original text and context.
Original: {original}
LibreTranslate: {libre}
DeepL: {deepl}
Context: {json.dumps(context, indent=2)}

Respond with the final translation only."""
        response = self.session.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.chatgpt_key}"},
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3
            }
        )
        data = response.json()
        return data['choices'][0]['message']['content'].strip()

class HTMLTranslationManager:
    def __init__(self, processor: HTMLTranslationProcessor, integrator: TranslationIntegrator):
        self.processor = processor
        self.integrator = integrator

    def process_file(self, html_file: Path, target_lang: str, output_file: Path) -> Path:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        extraction_result = self.processor.extract_translatable(html_content)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(self._translate_item, item, target_lang)
                for item in extraction_result['translation_data']
            ]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        merged_html = self._merge_translations(extraction_result['processed_html'], results)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(merged_html)
        return output_file

    def _translate_item(self, item: Dict, target_lang: str) -> Dict:
        libre = self.integrator.translate_with_libre(item['content'], target_lang)
        deepl = self.integrator.translate_with_deepl(item['content'], target_lang)
        final = self.integrator.resolve_with_chatgpt(item['content'], libre, deepl, item['context'])
        return {'id': item['id'], 'text': final}

    def _merge_translations(self, html_content: str, translations: List[Dict]) -> str:
        for item in translations:
            html_content = html_content.replace(f"<!-- TRANSLATION_ID_{item['id']} -->", item['text'])
        return html_content

def get_target_language() -> str:
    return os.getenv('TARGET_LANG', DEFAULT_TARGET_LANG)

def main():
    processor = HTMLTranslationProcessor()
    integrator = TranslationIntegrator(
        deepl_key=os.getenv('DEEPL_KEY'),
        libre_urls=os.getenv('LIBRE_URLS', '').split(','),
        chatgpt_key=os.getenv('CHATGPT_KEY')
    )
    manager = HTMLTranslationManager(processor, integrator)

    html_files = list(Path(".").glob("*.html"))
    html_files = [f for f in html_files if not any(f.name.endswith(f"-{s}.html") for s in EXCLUDED_LANG_SUFFIXES)]
    target_lang = get_target_language()

    for file in html_files:
        output_file = file.with_name(f"{file.stem}-{target_lang}{file.suffix}")
        try:
            manager.process_file(file, target_lang, output_file)
            print(f"Translated {file.name} -> {output_file.name}")
        except Exception as e:
            print(f"Failed to translate {file.name}: {str(e)}")

if __name__ == "__main__":
    main()