import json
import requests
from bs4 import BeautifulSoup
import hashlib
import os
import concurrent.futures
from difflib import SequenceMatcher
import random

# Predefined list of working LibreTranslate servers
LIBRETRANSLATE_SERVERS = [
    "https://translate.argosopentech.com",
    "https://libretranslate.de",
    "https://libretranslate.terraprint.co",
    "https://lt.vern.cc",
    "https://trans.zillyhuhn.com"
]

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

    # Keep rest of TranslationIntegrator methods same as previous version

class HTMLTranslationManager:
    # Keep all previous methods unchanged

    def _translate_item(self, item, target_lang):
        try:
            # Add retry logic for LibreTranslate
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    libre = self.integrator.translate_with_libre(item['content'], target_lang)
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(2 ** attempt)
            
            # Rest of the method remains same
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
                'analysis': {}
            }

def _merge_translations(self, processed_html, translations):
        soup = BeautifulSoup(processed_html, 'html.parser')
        translation_map = {t['id']: t['final_translation'] for t in translations}
        
        # Replace text nodes
        for tag in soup.find_all(text=True):
            if 'TRANSLATION_ID_' in tag:
                trans_id = int(tag.split('_')[-1].strip())
                if trans_id in translation_map:
                    tag.replace_with(translation_map[trans_id])
        
        # Replace attributes
        for element in soup.find_all(attrs=True):
            for attr in element.attrs:
                if isinstance(element[attr], str) and 'TRANSLATION_ID_' in element[attr]:
                    trans_id = int(element[attr].split('_')[-1].strip())
                    if trans_id in translation_map:
                        element[attr] = translation_map[trans_id]
        
        return str(soup)

# Usage Example
if __name__ == "__main__":
    # Initialize components
    processor = HTMLTranslationProcessor()
    integrator = TranslationIntegrator(
        deepl_key=os.getenv('DEEPL_KEY'),
        libre_url=os.getenv('LIBRE_URL'),
        chatgpt_key=os.getenv('CHATGPT_KEY')
    )
    
    manager = HTMLTranslationManager(processor, integrator)
    
    # Process file
    result_file = manager.process_file(
        html_file='input.html',
        target_lang='fr',
        output_file='name-fr.html'
    )
    
    print(f"Final translated file created: {result_file}")

# Rest of the code remains unchanged
