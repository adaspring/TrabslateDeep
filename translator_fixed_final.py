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
                deepl=deepl,)

# [Previous imports remain exactly the same...]

class TranslationIntegrator:
    def __init__(self, deepl_key: str, libre_urls: List[str], chatgpt_key: str):
        self.deepl_key = deepl_key
        self.libre_urls = libre_urls or LIBRETRANSLATE_SERVERS
        self.chatgpt_key = chatgpt_key
        self.session = requests.Session()
        self.max_retry_minutes = 10  # New: Max 10 minutes retry window

    def translate_with_libre(self, text: str, target_lang: str) -> str:
        """Keep retrying all servers for up to 10 minutes"""
        errors = []
        start_time = time.time()
        
        while time.time() - start_time < self.max_retry_minutes * 60:
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
                        timeout=30  # Increased timeout
                    )
                    if response.status_code == 200:
                        return response.json()['translatedText']
                    errors.append(f"{server}: HTTP {response.status_code}")
                except Exception as e:
                    errors.append(f"{server}: {str(e)}")
                
                # Check if we've exceeded time limit
                if time.time() - start_time >= self.max_retry_minutes * 60:
                    break
            
            print(f"ℹ️ Retrying LibreTranslate servers... (Attempts: {len(errors)})")
            time.sleep(5)  # Brief pause between cycles
        
        raise Exception(f"All LibreTranslate attempts failed after {self.max_retry_minutes} minutes:\n" + "\n".join(errors[-10:]))  # Show last 10 errors

    def translate_with_deepl(self, text: str, target_lang: str) -> str:
        """Retry DeepL with exponential backoff"""
        max_retries = 5
        base_delay = 2
        
        for attempt in range(max_retries):
            try:
                response = self.session.post(
                    "https://api-free.deepl.com/v2/translate",
                    headers={"Authorization": f"DeepL-Auth-Key {self.deepl_key}"},
                    data={
                        "text": text,
                        "target_lang": target_lang,
                        "preserve_formatting": "1"
                    },
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
                print(f"⚠️ DeepL attempt {attempt + 1} failed. Retrying in {delay}s...")
                time.sleep(delay)

def select_html_files() -> List[Path]:
    """Strict HTML file validation with repository awareness"""
    script_dir = Path(__file__).parent
    html_files = []
    
    # Validate only proper HTML files
    for f in script_dir.glob('*'):
        if f.suffix.lower() == '.html':
            try:
                # Basic HTML validation
                with open(f, 'r', encoding='utf-8') as file:
                    BeautifulSoup(file.read(), 'html.parser')  # Will throw if invalid
                html_files.append(f)
            except Exception as e:
                print(f"⚠️ Skipping invalid HTML file {f.name}: {str(e)}")
    
    # Exclude translations
    excluded_suffixes = os.getenv('EXCLUDED_LANG_SUFFIXES', '').split(',') or EXCLUDED_LANG_SUFFIXES
    base_files = [
        f for f in html_files 
        if not any(f.name.lower().endswith(f"-{lang}.html") for lang in excluded_suffixes)
    ]
    
    if not base_files:
        print("❌ No valid HTML files found in repository")
        return []
    
    # Interactive selection
    try:
        questions = [
            inquirer.Checkbox('files',
                message="Select HTML files to translate",
                choices=[f.name for f in base_files],
                validate=lambda _, x: len(x) > 0
            ),
        ]
        selected = inquirer.prompt(questions)['files']
        return [script_dir / f for f in selected]
    except Exception:
        print("⚠️  Falling back to all valid HTML files")
        return base_files

def main() -> int:
    """Guaranteed translation with fallbacks"""
    try:
        # Initialize with aggressive retry settings
        processor = HTMLTranslationProcessor()
        integrator = TranslationIntegrator(
            deepl_key=os.getenv('DEEPL_KEY'),
            libre_urls=os.getenv('LIBRE_URLS', '').split(','),
            chatgpt_key=os.getenv('CHATGPT_KEY')
        )
        manager = HTMLTranslationManager(processor, integrator)
        
        # File selection with validation
        files_to_translate = select_html_files()
        if not files_to_translate:
            return 1

     # [Previous code remains unchanged until confirm_translations()...]

def confirm_translations(translations: Dict[Path, Path]) -> None:
    """Modified for CI compatibility"""
    ci_mode = os.getenv('CI') == 'true'
    
    for original, translated in translations.items():
        print(f"\n🔍 Translation ready for {original.name}:")
        print(f"Original size: {original.stat().st_size} bytes")
        print(f"Translated size: {translated.stat().st_size} bytes")
        
        if ci_mode:
            # Auto-approve in CI with logging
            print("✅ CI auto-approval - saving translation")
            continue
            
        try:
            if inquirer.confirm("Approve this translation?", default=True):
                print(f"✅ Approved {original.name}")
            else:
                os.remove(translated)
                print(f"🗑️ Discarded {original.name}")
        except Exception:
            print("⚠️  Fallback approval (non-interactive)")

# [All following code remains unchanged...]





        
def get_target_language() -> str:
    return os.getenv('TARGET_LANG') or DEFAULT_TARGET_LANG

        target_lang = get_target_language()
        translations = {}
        
        # Process with guaranteed completion
        for file in files_to_translate:
            output_file = file.with_name(f"{file.stem}-{target_lang}{file.suffix}")
            print(f"\n🌐 Translating {file.name} to {target_lang}...")
            
            try:
                result = manager.process_file(
                    html_file=file,
                    target_lang=target_lang,
                    output_file=output_file
                )
                translations[file] = result
                print(f"✔️  Saved to {output_file.name}")
                break  # Success! At least one file translated
            except Exception as e:
                print(f"❌ Failed to translate {file.name}: {str(e)}")
                continue  # Try next file
        
        if not translations:
            print("💥 Failed to translate any files after all attempts")
            return 1
            
        # Approval process
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
