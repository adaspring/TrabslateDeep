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
EXCLUDED_LANG_SUFFIXES = ['fr', 'es', 'de']  # Can be overridden via env var
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
        
        return {
            'processed_html': str(soup),
            'translation_data': self.translation_data
        }

    def _process_text_node(self, element: BeautifulSoup) -> None:
        text = element.string.strip()
        self._create_placeholder(element, text, 'text')

    def _process_attribute(self, element: BeautifulSoup, attr: str) -> None:
        self._create_placeholder(element, element[attr], 'attribute', attr)

    def _create_placeholder(self, element: BeautifulSoup, content: str, 
                          content_type: str, attr: Optional[str] = None) -> None:
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
    def __init__(self, deepl_key: str, libre_urls: List[str], chatgpt_key: str):
        self.deepl_key = deepl_key
        self.libre_urls = libre_urls or LIBRETRANSLATE_SERVERS
        self.chatgpt_key = chatgpt_key
        self.session = requests.Session()

    def translate_with_libre(self, text: str, target_lang: str) -> str:
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
                errors.append(f"{server}: {response.status_code}")
            except Exception as e:
                errors.append(f"{server}: {str(e)}")
        
        raise Exception(f"All LibreTranslate servers failed: {', '.join(errors)}")

    def translate_with_deepl(self, text: str, target_lang: str) -> str:
        response = self.session.post(
            "https://api-free.deepl.com/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {self.deepl_key}"},
            data={
                "text": text,
                "target_lang": target_lang,
                "preserve_formatting": "1"
            }
        )
        data = response.json()
        if 'translations' not in data or not data['translations']:
            raise ValueError("Invalid DeepL response format")
        return data['translations'][0]['text']

    def resolve_with_chatgpt(self, original: str, libre: str, 
                           deepl: str, context: Dict) -> Dict:
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
                "temperature": 0.2,
                "response_format": {"type": "json_object"}
            }
        )
        data = response.json()
        if 'choices' not in data or not data['choices']:
            raise ValueError("Invalid ChatGPT response format")
        return json.loads(data['choices'][0]['message']['content'])

class HTMLTranslationManager:
    def __init__(self, processor: HTMLTranslationProcessor, 
                integrator: TranslationIntegrator):
        self.processor = processor
        self.integrator = integrator

    def process_file(self, html_file: Path, target_lang: str, 
                   output_file: Path) -> Path:
        """Process a single HTML file through translation pipeline"""
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
            results = [
                f.result() 
                for f in concurrent.futures.as_completed(futures)
            ]

        merged_html = self._merge_translations(
            extraction_result['processed_html'],
            results
        )
        
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(merged_html)
        
        return output_file

    def _translate_item(self, item: Dict, target_lang: str) -> Dict:
        """Handle translation of a single text segment"""
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
                'final_translation': analysis.get('combined_version') or analysis['chosen_translation'],
                'analysis': analysis
            }
        except Exception as e:
            print(f"Error translating ID {item['id']}: {str(e)}")
            return {
                'id': item['id'],
                'final_translation': "[TRANSLATION_ERROR]",
                'analysis': {'error': str(e)}
            }

    def _merge_translations(self, processed_html: str, 
                          translations: List[Dict]) -> str:
        """Reintegrate translations into HTML"""
        soup = BeautifulSoup(processed_html, 'html.parser')
        translation_map = {t['id']: t['final_translation'] for t in translations}
        
        # Process text nodes
        for tag in soup.find_all(text=True):
            if 'TRANSLATION_ID_' in tag:
                trans_id = int(tag.split('_')[-1].strip())
                if trans_id in translation_map:
                    tag.replace_with(translation_map[trans_id])
        
        # Process attributes
        for element in soup.find_all(attrs=True):
            for attr, value in element.attrs.items():
                if isinstance(value, str) and 'TRANSLATION_ID_' in value:
                    trans_id = int(value.split('_')[-1].strip())
                    if trans_id in translation_map:
                        element[attr] = translation_map[trans_id]
        
        return str(soup)

def select_html_files() -> List[Path]:
    """Find all HTML files in the script directory"""
    script_dir = Path(__file__).parent
    html_files = list(script_dir.glob('*.html'))
    
    excluded_suffixes = os.getenv('EXCLUDED_LANG_SUFFIXES', '').split(',') or EXCLUDED_LANG_SUFFIXES
    base_files = [
        f for f in html_files 
        if not any(f.name.endswith(f"-{lang}.html") for lang in excluded_suffixes)
    ]
    
    if not base_files:
        print("❌ No HTML files found in the script directory")
        return []
    
    try:
        questions = [
            inquirer.Checkbox('files',
                message="Select files to translate",
                choices=[f.name for f in base_files],
            ),
        ]
        selected = inquirer.prompt(questions)['files']
        return [script_dir / f for f in selected]
    except Exception:
        print("⚠️  Falling back to all available files (non-interactive mode)")
        return base_files

def confirm_translations(translations: Dict[Path, Path]) -> None:
    """Interactive translation approval"""
    for original, translated in translations.items():
        print(f"\n🔍 Review translation for {original.name}:")
        print(f"Original: {original.stat().st_size} bytes")
        print(f"Translated: {translated.stat().st_size} bytes")
        
        try:
            approve = inquirer.confirm(
                message=f"Approve translation for {original.name}?",
                default=True
            )
            if not approve:
                os.remove(translated)
                print(f"🗑️ Discarded translation for {original.name}")
            else:
                print(f"✅ Approved translation for {original.name}")
        except Exception:
            print("⚠️  Auto-approved (non-interactive mode)")

def get_target_language() -> str:
    """Get target language from env or prompt"""
    lang = os.getenv('TARGET_LANG')
    if lang:
        return lang
    
    try:
        return inquirer.text(
            message="Enter target language code (e.g. 'fr'):",
            validate=lambda _, x: len(x) == 2
        )
    except Exception:
        return DEFAULT_TARGET_LANG

def main() -> int:
    """Main execution flow"""
    try:
        # Initialize components
        processor = HTMLTranslationProcessor()
        integrator = TranslationIntegrator(
            deepl_key=os.getenv('DEEPL_KEY'),
            libre_urls=os.getenv('LIBRE_URLS', '').split(','),
            chatgpt_key=os.getenv('CHATGPT_KEY')
        )
        manager = HTMLTranslationManager(processor, integrator)
        
        # Select files and language
        files_to_translate = select_html_files()
        if not files_to_translate:
            return 1
            
        target_lang = get_target_language()
        translations = {}
        
        # Process files
        for file in files_to_translate:
            output_file = file.with_stem(f"{file.stem}-{target_lang}")
            print(f"\n🌐 Translating {file.name} to {target_lang}...")
            
            try:
                result = manager.process_file(
                    html_file=file,
                    target_lang=target_lang,
                    output_file=output_file
                )
                translations[file] = result
                print(f"✔️  Saved to {output_file.name}")
            except Exception as e:
                print(f"❌ Failed to translate {file.name}: {str(e)}")
        
        # Interactive approval
        if translations:
            confirm_translations(translations)
        
        return 0
        
    except Exception as e:
        print(f"💥 Critical error: {str(e)}")
        return 1

if __name__ == "__main__":
    # Install inquirer if missing
    try:
        import inquirer
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'inquirer'], check=True)
        import inquirer
    
    sys.exit(main())
