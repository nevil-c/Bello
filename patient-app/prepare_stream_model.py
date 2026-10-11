"""Download the English streaming model once; recognition then works offline."""
from pathlib import Path
import tempfile
from urllib.request import urlretrieve
from zipfile import ZipFile

root=Path(__file__).parent/'models'
name='vosk-model-small-en-us-0.15'
if (root/name/'am/final.mdl').exists():
    print('Streaming model already installed.')
else:
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive=Path(tmp)/'model.zip'
        urlretrieve('https://alphacephei.com/vosk/models/'+name+'.zip',archive)
        with ZipFile(archive) as z:
            for member in z.infolist():
                target=(root/member.filename).resolve()
                if not target.is_relative_to(root.resolve()):raise RuntimeError('Unsafe model archive path')
            z.extractall(root)
    print('English streaming model installed.')
