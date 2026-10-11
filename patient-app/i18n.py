"""Patient screen language: chosen by staff in the accessibility menu, remembered on this device.

Strings come from the Bello server (translated there and cached on this device). Without the server,
a committed table is used if there is one (strings_yue.json), and any missing string is English.
Speech recognition is unaffected: the server always detects the spoken language itself.

BELLO_SETTINGS_DIR  where the chosen language and downloaded strings are kept (default ~/.bello-patient)
"""
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SETTINGS_DIR=Path(os.path.expanduser(os.environ.get('BELLO_SETTINGS_DIR','~/.bello-patient')))
ENGLISH=json.loads((ROOT/'strings_en.json').read_text(encoding='utf-8'))
LANGUAGES=[(code,name) for code,name,_ in json.loads((ROOT/'languages.json').read_text(encoding='utf-8'))]
NAMES=dict(LANGUAGES)
_strings={}
language='en'

def tr(key):
    return _strings.get(key) or ENGLISH.get(key,key)

def _read(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return None

def saved_language():
    settings=_read(SETTINGS_DIR/'settings.json') or {}
    code=settings.get('ui_language','en')
    return code if code in NAMES else 'en'

def local_strings(code):
    """Strings available without the server: last download first, then a committed table."""
    return _read(SETTINGS_DIR/f'strings_{code}.json') or _read(ROOT/f'strings_{code}.json')

def apply(code,strings):
    global language,_strings
    language=code
    _strings={} if code=='en' else dict(strings or {})

def save(code,strings=None):
    try:
        SETTINGS_DIR.mkdir(parents=True,exist_ok=True)
        (SETTINGS_DIR/'settings.json').write_text(json.dumps({'ui_language':code}),encoding='utf-8')
        if strings and code!='en':(SETTINGS_DIR/f'strings_{code}.json').write_text(json.dumps(strings,ensure_ascii=False,indent=1),encoding='utf-8')
    except OSError as error:
        print(f'Could not save the screen language: {error}',flush=True)
