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
from typing import Dict, List, Optional, Tuple
import inquirer

# Configuration
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
                    'title', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'a', 'button', 'span',
                    'div', 'li', 'td', 'th', 'label', 'address', 'figcaption', 'caption',
                    'summary', 'blockquote', 'q', 'cite', 'dt', 'dd', 'legend', 'option',
                    'strong', 'em', 'mark', 'time'
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
        return {
            'processed_html': str(soup),
            'translation_data': self.translation_data
        }

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
    def __init__(self, deepl_key: str, chatgpt_key: str):
        self.deepl_key = deepl_key
        self.libre_urls = LIBRETRANSLATE_SERVERS
        self.chatgpt_key = chatgpt_key
        self.session = requests.Session()
        self.max_retry_minutes = 10

    def translate_with_libre(self, text: str, target_lang: str) -> str:
        errors = []
        start_time = time.time()
        while time.time() - start_time < self.max_retry_minutes * 60:
            shuffled_servers = random.sample(self.libre_urls, len(self.libre_urls))
            for server in shuffled_servers:
                try:
                    response = self.session.post(
                        f"{server}/translate",
                        json={"q": text, "source": "auto", "target": target_lang, "format": "text"},
                        timeout=30
                    )
                    if response.status_code == 200:
                        return response.json()['translatedText']
                    errors.append(f"{server}: HTTP {response.status_code}")
                except Exception as e:
                    errors.append(f"{server}: {str(e)}")
                if time.time() - start_time >= self.max_retry_minutes * 60:
                    break
            print(f"Retrying LibreTranslate servers... (Attempts: {len(errors)})")
            time.sleep(5)
        raise Exception("All LibreTranslate attempts failed:\n" + "\n".join(errors[-10:]))

    def translate_with_deepl(self, text: str, target_lang: str) -> str:
        max_retries = 5
        base_delay = 2
        for attempt in range(max_retries):
            try:
                response = self.session.post(
                    "https://api-free.deepl.com/v2/translate",
                    headers={"Authorization": f"DeepL-Auth-Key {self.deepl_key}"},
                    data={"text": text, "target_lang": target_lang, "preserve_formatting": "1"},
                    timeout=15
                )
                data = response.json()
                if 'translations' not in data:
                    raise ValueError("Invalid DeepL response format")
                return data['translations'][0]['text']
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                delay = base_delay ** (attempt + 1)
                print(f"DeepL attempt {attempt + 1} failed. Retrying in {delay}s...")
                time.sleep(delay)

    def resolve_with_chatgpt(self, original: str, libre: str, deepl: str, context: Dict) -> Dict:
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
        data = response.json()
        if 'choices' not in data or not data['choices']:
            raise ValueError("Invalid ChatGPT response format")
        return json.loads(data['choices'][0]['message']['content'])


class HTMLTranslationManager:
    def __init__(self, processor: HTMLTranslationProcessor, integrator: TranslationIntegrator):
        self.processor = processor
        self.integrator = integrator

    def process_file(self, html_file: Path, target_lang: str, output_file: Path) -> Path:
        if not html_file.exists():
            raise FileNotFoundError(f"Input file not found: {html_file}")
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
        return self.integrator.resolve_with_chatgpt(
            original=item['content'],
            libre=libre,
            deepl=deepl,
            context=item['context']
        )

    def _merge_translations(self, html: str, translations: List[Dict]) -> str:
        for entry in translations:
            placeholder = f"<!-- TRANSLATION_ID_{entry['id']} -->"
            html = html.replace(placeholder, entry['content'])
        return html


def select_html_files() -> List[Path]:
    script_dir = Path(__file__).parent
    html_files = []
    for f in script_dir.glob('*'):
        if f.suffix.lower() == '.html':
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    BeautifulSoup(file.read(), 'html.parser')
                html_files.append(f)
            except Exception as e:
                print(f"Skipping invalid HTML file {f.name}: {str(e)}")

    excluded_suffixes = EXCLUDED_LANG_SUFFIXES
    base_files = [
        f for f in html_files 
        if not any(f.name.lower().endswith(f"-{lang}.html") for lang in excluded_suffixes)
    ]

    if not base_files:
        print("No valid HTML files found in repository")
        return []
    
    print(f"Found {len(base_files)} HTML files to translate:")
    for f in base_files:
        print(f"- {f.name}")
        
    return base_files


def confirm_translations(translations: Dict[Path, Path]) -> None:
    ci_mode = os.getenv('CI') == 'true'
    for original, translated in translations.items():
        print(f"Translation ready for {original.name}:")
        print(f"Original size: {original.stat().st_size} bytes")
        print(f"Translated size: {translated.stat().st_size} bytes")
        if ci_mode:
            print("CI auto-approval - saving translation")
        else:
            print(f"Translation saved for {original.name}")


def main() -> int:
    try:
        processor = HTMLTranslationProcessor()
        integrator = TranslationIntegrator(
            deepl_key=os.getenv('DEEPL_KEY'),
            chatgpt_key=os.getenv('CHATGPT_KEY')
        )
        manager = HTMLTranslationManager(processor, integrator)
        files_to_translate = select_html_files()
        if not files_to_translate:
            return 1

        target_lang = os.getenv('TARGET_LANG', DEFAULT_TARGET_LANG)
        print(f"Using target language: {target_lang}")
        
        translations = {}
        for file in files_to_translate:
            output_file = file.with_stem(f"{file.stem}-{target_lang}")
            print(f"Translating {file.name} to {target_lang}...")
            try:
                result = manager.process_file(
                    html_file=file,
                    target_lang=target_lang,
                    output_file=output_file
                )
                translations[file] = result
                print(f"Saved to {output_file.name}")
            except Exception as e:
                print(f"Failed to translate {file.name}: {str(e)}")
                continue

        if not translations:
            print("Failed to translate any files")
            return 1

        confirm_translations(translations)
        return 0
    except Exception as e:
        print(f"Critical error: {str(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
