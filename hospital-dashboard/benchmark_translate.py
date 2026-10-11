"""Compare the two ways of getting English text for the routing model, on the server itself.

  A  nllb:    large-v3-turbo transcribes, NLLB-200 translates the text      (BELLO_EN_ENGINE=nllb)
  B  whisper: full large-v3 transcribes and translates the audio itself     (BELLO_EN_ENGINE=whisper)

  python benchmark_translate.py clip1.wav clip2.wav ...
  python benchmark_translate.py --say            # macOS: speaks the built-in phrases with system voices

Needs both Whisper models (prepare_model.py --model large-v3-turbo and --model large-v3) and the NLLB
model (prepare_translation_model.py). Judge the English by meaning, especially for the clinical phrases:
a fluent but wrong translation is worse than an awkward correct one.
"""
import argparse
import io
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
import local_stt
import translation

MODELS=Path(__file__).parent/'models'
# (macOS voice, language, phrase, what it means). Install voices in System Settings > Accessibility > Spoken Content.
PHRASES=[
    ('Sinji','yue','我心口好翳，有啲頭暈。','My chest feels tight and I am a little dizzy.'),
    ('Sinji','yue','我想要多一張被，好凍呀。','I would like another blanket, it is very cold.'),
    ('Sinji','yue','我想去廁所，可唔可以扶我？','I want to go to the toilet, can you help me?'),
    ('Sinji','yue','部電視壞咗，搖控唔郁。','The TV is broken, the remote does not work.'),
    ('Tingting','zh','我的伤口在流血，请快点来。','My wound is bleeding, please come quickly.'),
    ('Paulina','es','Me duele mucho la cabeza desde esta mañana.','I have had a bad headache since this morning.'),
    ('Samantha','en','Could someone bring me some water please?','(English: no translation needed)'),
]

def say_clips(folder):
    clips=[]
    for i,(voice,language,text,meaning) in enumerate(PHRASES):
        path=Path(folder)/f'{i}-{language}.wav'
        subprocess.run(['say','-v',voice,'-o',str(path),'--file-format=WAVE','--data-format=LEI16@16000',text],check=True)
        clips.append((path,meaning))
    return clips

def whisper(name):
    from faster_whisper import WhisperModel
    folder=MODELS/name
    if not (folder/'model.bin').is_file():raise SystemExit(f'Missing {folder}. Run prepare_model.py --model {name}.')
    return WhisperModel(str(folder),device='cpu',compute_type='int8',cpu_threads=local_stt.default_threads(),num_workers=1,local_files_only=True)

def run(model,audio,task,language=None):
    segments,info=model.transcribe(io.BytesIO(audio),beam_size=1,task=task,language=language,vad_filter=True,condition_on_previous_text=False)
    return ' '.join(s.text.strip() for s in segments).strip(),info.language

def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('clips',nargs='*',type=Path);p.add_argument('--say',action='store_true',help='generate clips with macOS say')
    a=p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        clips=say_clips(tmp) if a.say else [(c,'') for c in a.clips]
        if not clips:p.error('give WAV clips or --say')
        print('Loading models…',flush=True)
        turbo,large=whisper('large-v3-turbo'),whisper('large-v3');translation.load_model()
        totals={'A':0.0,'B':0.0}
        for path,meaning in clips:
            audio=path.read_bytes()
            start=time.monotonic();text,language=run(turbo,audio,'transcribe')
            english_a=translation.to_english(text,language) if language!='en' else text
            a_seconds=time.monotonic()-start
            start=time.monotonic();original_b,language_b=run(large,audio,'transcribe')
            english_b=run(large,audio,'translate',language_b)[0] if language_b!='en' else original_b
            b_seconds=time.monotonic()-start
            totals['A']+=a_seconds;totals['B']+=b_seconds
            print(f'\n{path.name}' + (f'   meaning: {meaning}' if meaning else ''))
            print(f'  A turbo+NLLB  {a_seconds:5.1f}s  [{language}] {text}\n                       -> {english_a}')
            print(f'  B large-v3    {b_seconds:5.1f}s  [{language_b}] {original_b}\n                       -> {english_b}')
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024)
        print(f"\nTotal: A {totals['A']:.1f}s, B {totals['B']:.1f}s over {len(clips)} clips. Peak memory {peak:.0f} MiB (all three models loaded).")
        print('Set BELLO_EN_ENGINE=nllb (A) or BELLO_EN_ENGINE=whisper (B, with BELLO_WHISPER_MODEL=large-v3).')

if __name__=='__main__':main()
