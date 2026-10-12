"""
多格式导出模块

支持将公众号文章导出为：
- HTML（完整保留样式与排版，图片本地化）
- PDF（自定义页面尺寸、边距、页眉页脚）
- Markdown（结构化转换，支持标题/列表/图片/代码块等）
- Excel（批量元数据表格）
"""

import os
import datetime
from typing import Optional

from scraper import ArticleData


class HTMLExporter:
    """HTML格式导出器"""

    # 公众号文章基础样式
    ARTICLE_CSS = """
    @charset "utf-8";
    * { box-sizing: border-box; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                      "Microsoft YaHei", "微软雅黑", "PingFang SC", sans-serif;
        max-width: 677px;
        margin: 0 auto;
        padding: 20px;
        color: #333;
        line-height: 1.75;
        background: #fff;
    }
    #js_content { font-size: 16px; color: #333; word-wrap: break-word; }
    #js_content p { margin: 0 0 15px; }
    #js_content h1, #js_content h2, #js_content h3,
    #js_content h4, #js_content h5, #js_content h6 {
        font-weight: bold; margin: 20px 0 10px;
    }
    #js_content img { max-width: 100% !important; height: auto !important; }
    #js_content a { color: #576b95; text-decoration: none; }
    #js_content table { border-collapse: collapse; width: 100%; margin: 15px 0; }
    #js_content td, #js_content th {
        border: 1px solid #ddd; padding: 8px; font-size: 14px;
    }
    #js_content pre {
        background: #f5f5f5; padding: 12px; border-radius: 4px;
        overflow-x: auto; font-size: 14px;
    }
    #js_content code {
        background: #f5f5f5; padding: 2px 6px; border-radius: 3px;
        font-family: Consolas, Monaco, monospace;
    }
    .article-header { margin-bottom: 20px; border-bottom: 1px solid #eee; padding-bottom: 15px; }
    .article-title { font-size: 24px; font-weight: bold; line-height: 1.4; margin: 0 0 10px; }
    .article-meta { font-size: 14px; color: #999; }
    .article-meta span { margin-right: 15px; }
    """

    def export(self, article: ArticleData, output_dir: str) -> str:
        """
        导出为HTML文件

        Returns:
            生成的文件路径
        """
        filename = f"{article.safe_title}.html"
        filepath = os.path.join(output_dir, filename)

        html = self._build_html(article)

        # 使用 utf-8-sig 带 BOM 写入，确保Windows下浏览器正确识别编码
        with open(filepath, 'w', encoding='utf-8-sig') as f:
            f.write(html)

        return filepath

    def _build_html(self, article: ArticleData) -> str:
        """构建完整的HTML文档"""
        meta_parts = []
        if article.account_name:
            meta_parts.append(f'<span>公众号: {article.account_name}</span>')
        if article.author:
            meta_parts.append(f'<span>作者: {article.author}</span>')
        if article.publish_time:
            meta_parts.append(f'<span>发布时间: {article.publish_time}</span>')
        meta_html = '\n'.join(meta_parts)

        # 如果正文为空，给出明确提示
        if not article.content_html or not article.content_html.strip():
            content = (
                '<p style="color:#999;text-align:center;padding:40px;">'
                '（正文内容为空，可能是文章需要登录验证或页面结构变化）</p>'
            )
        else:
            content = article.content_html

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{article.title}</title>
    <style>
{self.ARTICLE_CSS}
    </style>
</head>
<body>
    <div class="article-header">
        <h1 class="article-title">{article.title}</h1>
        <div class="article-meta">
{meta_html}
        </div>
    </div>
    <div id="js_content" class="rich_media_content">
{content}
    </div>
</body>
</html>"""


class PDFExporter:
    """PDF格式导出器（基于weasyprint）"""

    # PDF默认配置
    DEFAULT_OPTIONS = {
        'page_size': 'A4',
        'margin_top': '20mm',
        'margin_bottom': '20mm',
        'margin_left': '15mm',
        'margin_right': '15mm',
        'header': '',
        'footer': '',
    }

    # PDF专用CSS（加入中文字体支持）
    PDF_CSS_TEMPLATE = """
    @page {{
        size: {page_size};
        margin: {margin_top} {margin_right} {margin_bottom} {margin_left};
        {header_css}
        {footer_css}
    }}
    body {{
        font-family: "WxCJK", "CJK", "Microsoft YaHei", "微软雅黑", "SimSun", "宋体", "SimHei", "黑体", sans-serif;
        font-size: 14px;
        line-height: 1.75;
        color: #333;
    }}
    h1, h2, h3, h4, h5, h6 {{
        font-family: "WxCJK", "CJK", "Microsoft YaHei", "微软雅黑", "SimHei", "黑体", sans-serif;
        font-weight: bold;
    }}
    p {{ font-family: "WxCJK", "CJK", "Microsoft YaHei", "微软雅黑", "SimSun", "宋体", sans-serif; }}
    img {{ max-width: 100% !important; height: auto !important; }}
    table {{ border-collapse: collapse; width: 100%; }}
    td, th {{ border: 1px solid #ddd; padding: 6px; }}
    """

    HEADER_CSS_TEMPLATE = """
    @top-center {{
        content: "{header_text}";
        font-size: 9px;
        color: #999;
    }}
    """

    FOOTER_CSS_TEMPLATE = """
    @bottom-center {{
        content: "{footer_text}";
        font-size: 9px;
        color: #999;
    }}
    """

    PAGE_NUMBER_CSS = """
    @bottom-right {
        content: counter(page) "/" counter(pages);
        font-size: 9px;
        color: #999;
    }
    """

    def __init__(self):
        self._engine = None  # 'weasyprint' 或 'xhtml2pdf'
        self._check_engines()

    def _check_engines(self):
        """检测可用的PDF引擎（优先weasyprint，回退xhtml2pdf）"""
        import io, contextlib, sys
        self._available = False

        # 尝试 weasyprint（渲染质量最高，但Windows需GTK库）
        # 临时重定向stderr以抑制weasyprint的警告输出
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                from weasyprint import HTML, CSS
            self._engine = 'weasyprint'
            self._available = True
        except Exception:
            pass

        # 回退到 xhtml2pdf（纯Python，无需外部库）
        if not self._available:
            try:
                from xhtml2pdf import pisa
                self._engine = 'xhtml2pdf'
                self._available = True
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._available

    @property
    def engine_name(self) -> str:
        return self._engine or 'none'

    def export(
        self, article: ArticleData, output_dir: str, options: Optional[dict] = None
    ) -> str:
        """
        导出为PDF文件

        Args:
            article: 文章数据
            output_dir: 输出目录
            options: PDF选项（page_size, margin, header, footer等）

        Returns:
            生成的文件路径
        """
        if not self._available:
            raise ImportError(
                "PDF导出需要安装 weasyprint 或 xhtml2pdf 库。"
                "执行: pip install weasyprint  或  pip install xhtml2pdf"
            )

        opts = {**self.DEFAULT_OPTIONS, **(options or {})}

        filename = f"{article.safe_title}.pdf"
        filepath = os.path.join(output_dir, filename)

        # 构建HTML内容
        html_exporter = HTMLExporter()
        html_content = html_exporter._build_html(article)

        if self._engine == 'weasyprint':
            self._export_weasyprint(html_content, filepath, opts, output_dir)
        elif self._engine == 'xhtml2pdf':
            self._export_xhtml2pdf(html_content, filepath, opts, output_dir)

        return filepath

    def _export_weasyprint(self, html_content, filepath, opts, output_dir):
        """使用weasyprint生成PDF（渲染质量最高）"""
        from weasyprint import HTML, CSS

        html_content = self._fix_image_paths(html_content, output_dir)
        css_content = self._build_css(opts)

        # 为 weasyprint 注册中文字体（通过 @font-face）
        cjk_font_path = self._find_cjk_font()
        if cjk_font_path:
            font_url = 'file:///' + cjk_font_path.replace('\\', '/')
            cjk_font_css = (
                f"@font-face {{\n"
                f"  font-family: 'WxCJK';\n"
                f"  src: url('{font_url}') format('truetype');\n"
                f"}}\n"
            )
            css_content = cjk_font_css + css_content

        html_obj = HTML(string=html_content, base_url=output_dir)
        css_obj = CSS(string=css_content)
        html_obj.write_pdf(filepath, stylesheets=[css_obj])

    def _export_xhtml2pdf(self, html_content, filepath, opts, output_dir):
        """使用xhtml2pdf生成PDF（纯Python回退方案）

        关键修复：xhtml2pdf对复杂CSS和内联样式支持差，
        需要先简化HTML（去除内联样式和复杂属性），只保留基本HTML结构。
        """
        from xhtml2pdf import pisa
        from xhtml2pdf.config.resources import PERMISSIVE_POLICY
        from bs4 import BeautifulSoup

        # 构建带CSS的完整HTML
        page_size = opts.get('page_size', 'A4')
        margins = (
            f"{opts.get('margin_top', '20mm')} "
            f"{opts.get('margin_right', '15mm')} "
            f"{opts.get('margin_bottom', '20mm')} "
            f"{opts.get('margin_left', '15mm')}"
        )

        # 简化HTML：去除所有内联样式和复杂属性，只保留基本HTML结构
        # 这是修复PDF乱码/空白的关键步骤
        soup = BeautifulSoup(html_content, 'html5lib')

        # 允许保留的标签
        allowed_tags = {
            'html', 'head', 'meta', 'title', 'style', 'body',
            'div', 'p', 'span', 'br', 'hr',
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'img', 'a',
            'ul', 'ol', 'li',
            'table', 'thead', 'tbody', 'tr', 'td', 'th',
            'strong', 'em', 'b', 'i',
            'blockquote', 'pre', 'code',
        }

        # 移除不允许的标签（保留其内容）
        for tag in soup.find_all(True):
            if tag.name not in allowed_tags:
                tag.unwrap()

        # 移除所有标签的 style 和 class 属性（xhtml2pdf对复杂CSS支持差）
        for tag in soup.find_all(True):
            for attr in list(tag.attrs.keys()):
                if attr in ('style', 'class', 'id', 'onclick', 'onload',
                            'data-src', 'data-original', 'data-ratio',
                            'data-w', 'data-type', 'data-cropselax1',
                            'data-cropselax2', 'data-croporisrc',
                            'data-content', 'data-lazy-load', 'data-s'):
                    del tag[attr]

        # 将相对图片路径转为绝对路径
        html_simplified = str(soup)
        html_simplified = self._fix_image_paths(html_simplified, output_dir)

        # 查找系统中文字体
        cjk_font_path = self._find_cjk_font()
        font_face_css = ""
        body_font = "sans-serif"

        if cjk_font_path:
            font_url = 'file:///' + cjk_font_path.replace('\\', '/')
            font_face_css = (
                f"@font-face {{\n"
                f"  font-family: CJK;\n"
                f"  src: url('{font_url}');\n"
                f"}}\n"
            )
            body_font = "CJK, sans-serif"

        xhtml2pdf_css = f"""
        {font_face_css}
        @page {{ size: {page_size}; margin: {margins}; }}
        body {{ font-family: {body_font}; font-size: 14px; line-height: 1.75; color: #333; }}
        h1, h2, h3, h4, h5, h6 {{ font-family: {body_font}; font-weight: bold; }}
        p {{ font-family: {body_font}; }}
        img {{ max-width: 100%; }}
        table {{ border-collapse: collapse; width: 100%; }}
        td, th {{ border: 1px solid #ddd; padding: 6px; font-family: {body_font}; }}
        """

        # 确保HTML有charset声明
        if 'charset' not in html_simplified.lower():
            html_simplified = html_simplified.replace(
                '<head>', '<head>\n<meta charset="UTF-8">', 1
            )

        # 将CSS插入HTML
        html_with_css = html_simplified.replace(
            '</head>', f'<style>{xhtml2pdf_css}</style></head>'
        )

        # 清除 <title> 标签内容，避免 xhtml2pdf 对 title 文本产生字体警告
        import re as _re
        html_with_css = _re.sub(
            r'(<title>)[^<]*(</title>)', r'\1\2', html_with_css
        )

        # 生成PDF
        # 使用 PERMISSIVE_POLICY 允许读取系统字体文件
        with open(filepath, 'wb') as f:
            pisa_status = pisa.CreatePDF(
                src=html_with_css,
                dest=f,
                path=os.path.abspath(output_dir),
                encoding='utf-8',
                resource_policy=PERMISSIVE_POLICY,
            )

        # 检查PDF生成结果
        if not pisa_status.err:
            file_size = os.path.getsize(filepath)
            if file_size < 100:
                os.remove(filepath)
                raise Exception("PDF生成失败：文件过小，可能为空白PDF")
        else:
            raise Exception("PDF生成出错，请检查HTML内容是否正确")

    def _fix_image_paths(self, html: str, base_dir: str) -> str:
        """将相对图片路径转为绝对路径（weasyprint/xhtml2pdf需要绝对路径定位本地资源）"""
        import re

        abs_dir = os.path.abspath(base_dir)

        def replace_img_src(match):
            quote = match.group(1)  # 引号字符（" 或 '）
            relative = match.group(2)
            # 跳过已经是绝对URL或data URI的路径
            if relative.startswith(('http', 'file://', 'data:')):
                return match.group(0)
            abs_path = os.path.join(abs_dir, relative).replace('\\', '/')
            return f'src={quote}file:///{abs_path}{quote}'

        # 同时处理双引号和单引号的 src 属性
        return re.sub(r'src=(["\'])([^"\']+)\1', replace_img_src, html)

    def _find_cjk_font(self) -> str:
        """查找系统中可用的中文字体文件路径

        优先返回 .ttf 文件（xhtml2pdf 对 .ttc 集合文件支持不稳定），
        .ttc 仅作为最后回退。
        """
        # Windows 常见中文字体（按优先级排序：先.ttf，后.ttc）
        win_font_dir = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')
        candidates = [
            'simhei.ttf',   # 黑体（.ttf，xhtml2pdf兼容性好）
            'simfang.ttf',  # 仿宋
            'simkai.ttf',   # 楷体
            'SIMLI.ttf',    # 隶书
            'SIMYOU.ttf',   # 幼圆
            'msyh.ttf',     # 微软雅黑（部分系统为.ttf）
            'simsun.ttc',   # 宋体（.ttc，回退）
            'msyh.ttc',     # 微软雅黑（.ttc，回退）
        ]
        for name in candidates:
            path = os.path.join(win_font_dir, name)
            if os.path.isfile(path):
                return path.replace('\\', '/')

        # macOS 中文字体
        mac_candidates = [
            '/System/Library/Fonts/STHeiti Light.ttc',
            '/System/Library/Fonts/PingFang.ttc',
            '/Library/Fonts/Songti.ttc',
        ]
        for path in mac_candidates:
            if os.path.isfile(path):
                return path

        # Linux 中文字体
        linux_candidates = [
            '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
            '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/arphic/uming.ttc',
        ]
        for path in linux_candidates:
            if os.path.isfile(path):
                return path

        return ""

    def _build_css(self, opts: dict) -> str:
        """构建PDF样式表"""
        header_css = ""
        if opts.get('header'):
            header_css = self.HEADER_CSS_TEMPLATE.format(
                header_text=opts['header'].replace('"', '\\"')
            )

        footer_css = ""
        if opts.get('footer'):
            footer_text = opts['footer'].replace('"', '\\"')
            footer_css = self.FOOTER_CSS_TEMPLATE.format(footer_text=footer_text)
        else:
            footer_css = self.PAGE_NUMBER_CSS

        return self.PDF_CSS_TEMPLATE.format(
            page_size=opts['page_size'],
            margin_top=opts['margin_top'],
            margin_right=opts['margin_right'],
            margin_bottom=opts['margin_bottom'],
            margin_left=opts['margin_left'],
            header_css=header_css,
            footer_css=footer_css,
        )


class MarkdownExporter:
    """Markdown格式导出器（基于html2text）"""

    def __init__(self):
        import html2text
        self._h2t = html2text

    def export(self, article: ArticleData, output_dir: str) -> str:
        """
        导出为Markdown文件

        Returns:
            生成的文件路径
        """
        filename = f"{article.safe_title}.md"
        filepath = os.path.join(output_dir, filename)

        markdown_content = self._convert_to_markdown(article)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        return filepath

    def _convert_to_markdown(self, article: ArticleData) -> str:
        """将文章内容转换为Markdown"""
        h = self._h2t.HTML2Text()
        # 配置转换选项
        h.body_width = 0            # 不自动换行
        h.ignore_images = False     # 保留图片
        h.ignore_links = False      # 保留链接
        h.ignore_emphasis = False   # 保留粗体斜体
        h.protect_links = True      # 保护链接完整URL
        h.unicode_snob = True       # 使用Unicode字符
        h.images_as_html = False    # 使用Markdown图片语法

        # 转换正文内容
        content_md = h.handle(article.content_html or '<p>（正文内容为空）</p>')
        content_md = content_md.strip()

        # 构建Markdown文档（包含元数据头部）
        parts = []

        # 标题
        parts.append(f"# {article.title}\n")

        # 元数据
        meta_lines = []
        if article.account_name:
            meta_lines.append(f"- **公众号**: {article.account_name}")
        if article.author:
            meta_lines.append(f"- **作者**: {article.author}")
        if article.publish_time:
            meta_lines.append(f"- **发布时间**: {article.publish_time}")
        meta_lines.append(f"- **原文链接**: [{article.url}]({article.url})")

        if meta_lines:
            parts.append('\n'.join(meta_lines))
            parts.append('')  # 空行分隔

        parts.append('---\n')

        # 正文
        parts.append(content_md)

        return '\n'.join(parts)


class ExcelExporter:
    """Excel元数据导出器（基于openpyxl）"""

    # 列定义: (列标题, 取值函数)
    COLUMNS = [
        ('序号',        lambda i, a: i + 1),
        ('标题',        lambda i, a: a.title),
        ('公众号',      lambda i, a: a.account_name),
        ('作者',        lambda i, a: a.author),
        ('发布时间',    lambda i, a: a.publish_time),
        ('原文链接',    lambda i, a: a.url),
        ('下载状态',    lambda i, a: '成功' if a.success else f'失败: {a.error}'),
        ('下载时间',    lambda i, a: datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
    ]

    def export(self, articles: list, output_dir: str) -> str:
        """
        批量导出元数据到Excel

        Args:
            articles: ArticleData 列表
            output_dir: 输出目录

        Returns:
            生成的文件路径
        """
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

        filepath = os.path.join(output_dir, '公众号文章元数据.xlsx')
        wb = Workbook()
        ws = wb.active
        ws.title = '文章元数据'

        # 样式定义
        header_font = Font(name='微软雅黑', size=11, bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
        header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
        body_font = Font(name='微软雅黑', size=10)
        body_align = Alignment(vertical='center', wrap_text=True)
        thin_border = Border(
            left=Side(style='thin', color='D9D9D9'),
            right=Side(style='thin', color='D9D9D9'),
            top=Side(style='thin', color='D9D9D9'),
            bottom=Side(style='thin', color='D9D9D9'),
        )

        # 写入表头
        for col_idx, (col_name, _) in enumerate(self.COLUMNS, 1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

        # 写入数据行
        for row_idx, article in enumerate(articles, start=2):
            for col_idx, (_, getter) in enumerate(self.COLUMNS, 1):
                value = getter(row_idx - 2, article)
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font = body_font
                cell.alignment = body_align
                cell.border = thin_border

        # 自动列宽（根据内容估算）
        col_widths = [6, 50, 20, 15, 22, 60, 30, 22]
        for idx, width in enumerate(col_widths, 1):
            ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width

        # 冻结首行
        ws.freeze_panes = 'A2'

        # 自动筛选
        ws.auto_filter.ref = ws.dimensions

        wb.save(filepath)
        return filepath
