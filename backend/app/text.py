import json
import re
import unicodedata
from functools import lru_cache
from threading import Lock

import jieba

DEFAULT_RULES = {'stopwords': ['的','了','是','我','你','他','她','它','我们','一个','和','与','在','就','都','也','很','有','更','就是'], 'phrases': [], 'synonyms': {}}
token_lock = Lock()

def normalize(text):
    return unicodedata.normalize('NFKC', text).strip().lower()

@lru_cache(maxsize=2)
def tokenizer(config_json):
    config = json.loads(config_json)
    obj = jieba.Tokenizer()
    obj.initialize()
    for word in config['phrases']:
        obj.add_word(normalize(word), freq=1000000)
    return obj

def analyze(raw, config_json):
    config = json.loads(config_json)
    stop = {normalize(w) for w in config['stopwords']}
    synonyms = {normalize(k): normalize(v) for k, v in config['synonyms'].items()}
    with token_lock:
        words = list(tokenizer(config_json).cut(normalize(raw)))
    result = set()
    for word in words:
        word = word.strip()
        if not re.search(r'[a-z0-9\u3400-\u9fff]', word) or word in stop:
            continue
        word = synonyms.get(word, word)
        if word and word not in stop:
            result.add(word)
    return sorted(result)
