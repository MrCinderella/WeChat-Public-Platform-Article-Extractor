# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：公众号合集文章提取器

用法：
    pyinstaller album_extractor.spec

产物：
    dist/公众号合集文章提取器.exe   （单文件，双击即用，无需装Python）
"""

block_cipher = None


a = Analysis(
    ['album_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # html2text 在 MarkdownExporter 内延迟导入
        'html2text',
        # openpyxl 用于元数据表格导出
        'openpyxl',
        'openpyxl.cell',
        'openpyxl.styles',
        'openpyxl.utils',
        # bs4 解析器
        'bs4',
        'lxml',
        'html5lib',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 排除用不到的大型库，显著减小体积
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtMultimedia',
        'PySide6.Qt3DCore',
        'PySide6.QtBluetooth',
        'weasyprint',
        'xhtml2pdf',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='公众号合集文章提取器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)