from setuptools import setup

APP = ['scripts/claude-ears-menubar.py']

OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'LSUIElement': True,
        'CFBundleName': 'ClaudeEars',
        'CFBundleDisplayName': 'Claude Ears',
        'CFBundleIdentifier': 'com.dmpgh.claude-ears',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
    },
    'packages': ['rumps', 'whisper', 'numpy', 'tiktoken', 'tqdm'],
    'excludes': ['matplotlib', 'tkinter', 'test'],
}

setup(
    name='Claude Ears',
    app=APP,
    data_files=[],
    options={'py2app': OPTIONS},
    setup_requires=[],
)
