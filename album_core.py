"""
公众号合集文章提取核心模块。

核心能力：
- 解析合集链接，提取 __biz 与 album_id
- 游标分页抓取全部文章（begin_msgid/begin_itemidx，非 begin 偏移）
- 按出现顺序分配唯一文件名，自动处理同名文章
- 抓取正文并导出 Markdown / HTML / Excel
"""

import json
import os
import re
import time
import collections

from scraper import ArticleScraper, ArticleData
from exporters import MarkdownExporter, HTMLExporter, ExcelExporter


ALBUM_API = "https://mp.weixin.qq.com/mp/appmsgalbum"
ARTICLE_HOST = "mp.weixin.qq.com"


# ==================== 链接解析 ====================

def parse_album_url(url):
    """从合集链接中解析 __biz 与 album_id。

    Returns:
        dict: {'biz': str, 'album_id': str, 'error': str}
    """
    res = {'biz': '', 'album_id': '', 'error': ''}
    if not url or not url.strip():
        res['error'] = '请输入合集链接'
        return res

    url = url.strip()
    if '//mp.weixin.qq.com' not in url:
        res['error'] = '不是有效的微信文章链接'
        return res

    def grab(key):
        m = re.search(key + r'=([^&#\s]+)', url)
        return m.group(1) if m else ''

    res['biz'] = grab('__biz')
    res['album_id'] = grab('album_id')

    if not res['album_id']:
        res['error'] = '链接中缺少 album_id，请确认是合集/专辑链接'
    return res


# ==================== 列表抓取 ====================

def fetch_all_articles(scraper, biz, album_id, log=None, max_pages=20):
    """游标分页抓取合集内全部文章。

    微信合集接口仅支持游标分页：begin_msgid + begin_itemidx。
    普通的 begin= 偏移参数会被服务端忽略。
    continue_flag 返回字符串 '1' 而非整数 1。
    """
    def emit(msg):
        if log:
            log(msg)

    base = (f"{ALBUM_API}?action=getalbum&__biz={biz}"
            f"&album_id={album_id}&f=json")

    articles, seen = [], set()
    url = f"{base}&count=20"
    page, empty_streak = 0, 0

    while url and page < max_pages:
        page += 1
        data = _get_json(scraper, url, log)
        if data is None:
            emit(f"第 {page} 页请求失败，终止抓取")
            break

        resp = data.get('getalbum_resp', {})
        al = resp.get('article_list', [])

        if not al:
            empty_streak += 1
            emit(f"第 {page} 页返回空（第 {empty_streak} 次），稍后重试")
            if empty_streak >= 3:
                break
            time.sleep(10)
            continue
        empty_streak = 0

        added = 0
        for a in al:
            link = (a.get('url') or '').replace('&amp;', '&').split('#')[0]
            if link and link not in seen:
                seen.add(link)
                articles.append({
                    'title': (a.get('title') or '').strip(),
                    'url': link,
                    'msgid': a.get('msgid'),
                    'itemidx': a.get('itemidx'),
                    'create_time': a.get('create_time'),
                })
                added += 1

        cf = resp.get('continue_flag')
        emit(f"第 {page} 页：{len(al)} 篇，新增 {added} 篇，累计 {len(articles)} 篇")

        # continue_flag 可能是 int 1，也可能是 str '1'
        if str(cf) != '1':
            emit("已到最后一页")
            break

        last = al[-1]
        url = (f"{base}&count=20&begin_msgid={last.get('msgid')}"
               f"&begin_itemidx={last.get('itemidx')}")
        time.sleep(4)

    return articles


def _get_json(scraper, url, log=None, tries=6):
    """请求并解析 JSON，遇限流自动退避重试。"""
    for k in range(tries):
        try:
            r = scraper.session.get(url, timeout=30)
            r.encoding = 'utf-8'
            return json.loads(r.text)
        except Exception:
            if k < tries - 1:
                wait = 5 * (k + 1)
                if log:
                    log(f"请求异常，{wait} 秒后重试（{k + 1}/{tries}）")
                time.sleep(wait)
    return None


# ==================== 唯一命名 ====================

def safe_filename(title):
    """清洗为合法文件名（与既有导出器规则一致）。"""
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', '', title or '').strip()
    return s[:80] if s else '未命名文章'


def build_plan(articles):
    """为文章列表分配唯一文件名。

    合集内存在同名文章（如同一主题的不同版本），按出现顺序追加
    （2）（3）（4）后缀，避免互相覆盖导致丢篇。
    """
    cnt = collections.Counter()
    plan = []
    for i, a in enumerate(articles, 1):
        base = safe_filename(a['title'])
        cnt[base] += 1
        name = base if cnt[base] == 1 else f"{base}（{cnt[base]}）"
        plan.append({
            'no': i,
            'title': a['title'],
            'url': a['url'],
            'filename': name,
        })
    return plan


def duplicate_groups(plan):
    """返回存在同名冲突的分组信息，便于在界面提示用户。"""
    cnt = collections.Counter(p['filename'] for p in plan)
    return {k: v for k, v in cnt.items() if v > 1}


# ==================== 正文抓取与导出 ====================

def is_valid_article(art):
    """判断抓取结果是否真正可用。

    仅看 success 不够：微信有时返回 HTTP 200 但正文为空，
    或标题解析失败。此处要求标题非空且正文有实质内容。
    """
    if not art or not getattr(art, 'success', False):
        return False
    if not (getattr(art, 'title', '') or '').strip():
        return False
    body = getattr(art, 'content_html', '') or ''
    if len(body) < 500:
        return False
    if '（正文内容为空）' in body:
        return False
    return True


def article_problem(art):
    """返回失败原因，用于日志展示。"""
    if not art:
        return '未获取到数据'
    if not getattr(art, 'success', False):
        return getattr(art, 'error', '') or '抓取失败'
    if not (getattr(art, 'title', '') or '').strip():
        return '标题解析失败'
    return f"正文过短（{len(getattr(art, 'content_html', '') or '')} 字节）"


class AlbumExtractor:
    """合集文章提取器：串联列表抓取、正文下载与多格式导出。"""

    def __init__(self, output_dir, formats=('md',), log=None, stop_flag=None,
                 progress=None, skip_existing=False):
        """
        Args:
            output_dir: 输出目录
            formats: 导出格式集合，md / html / excel
            log: 日志回调 log(str)
            stop_flag: 可调用对象，返回 True 时中止
            progress: 进度回调 progress(cur, total, title)
            skip_existing: 跳过已存在的文件，用于断点续传
        """
        self.output_dir = output_dir
        self.formats = set(formats)
        self._log = log
        self._stop = stop_flag
        self._progress = progress
        self.skip_existing = skip_existing

        os.makedirs(output_dir, exist_ok=True)

        self.scraper = ArticleScraper(output_dir=output_dir,
                                      delay_range=(3.0, 5.0))
        self.md_exporter = MarkdownExporter()
        self.html_exporter = HTMLExporter()
        self.excel_exporter = ExcelExporter()

        self.results = []
        self.failed = []
        self.skipped = 0

    def log(self, msg):
        if self._log:
            self._log(msg)

    def report(self, cur, total, title):
        if self._progress:
            self._progress(cur, total, title)

    def stopped(self):
        return bool(self._stop and self._stop())

    # ---------- 阶段一：列表 ----------
    def fetch_list(self, url):
        info = parse_album_url(url)
        if info['error']:
            return None, info['error']

        biz, aid = info['biz'], info['album_id']
        self.scraper.session.headers['Referer'] = (
            f"{ALBUM_API}?__biz={biz}&album_id={aid}&action=getalbum")

        self.log(f"合集 ID：{aid}")
        articles = fetch_all_articles(self.scraper, biz, aid, log=self.log)
        if not articles:
            return None, '未获取到任何文章，请确认合集链接有效且非空'
        return articles, ''

    # ---------- 阶段二：正文 ----------
    def run(self, url=None, plan=None, max_retries=4):
        """执行完整提取流程。

        Args:
            url: 合集链接；plan 已给出时跳过列表抓取
        Returns:
            (results, failed)
        """
        if plan is None:
            articles, err = self.fetch_list(url)
            if not articles:
                return [], [{'no': 0, 'title': err, 'error': err}]
            plan = build_plan(articles)

        total = len(plan)
        dups = duplicate_groups(plan)
        if dups:
            self.log(f"检测到 {len(dups)} 组同名文章，将自动添加序号后缀区分")

        self.log(f"开始下载 {total} 篇文章\n")

        for p in plan:
            if self.stopped():
                self.log("已停止下载")
                break

            # 断点续传：已存在且内容正常则跳过
            if self.skip_existing and 'md' in self.formats:
                target = os.path.join(self.output_dir, f"{p['filename']}.md")
                if os.path.exists(target) and os.path.getsize(target) >= 3000:
                    self.log(f"[{p['no']}/{total}] {p['title'][:40]} —— 已存在，跳过")
                    self.skipped += 1
                    self.report(p['no'], total, p['title'])
                    continue

            self.log(f"[{p['no']}/{total}] {p['title'][:40]}")
            self.report(p['no'], total, p['title'])

            art, reason = None, ''
            for attempt in range(max_retries):
                try:
                    art = self.scraper.fetch_article(p['url'])
                    if is_valid_article(art):
                        break
                    reason = article_problem(art)
                except Exception as e:
                    reason = f"{type(e).__name__}: {e}"

                if attempt < max_retries - 1:
                    wait = 15 * (attempt + 1)
                    self.log(f"    重试 {attempt + 1}/{max_retries}"
                             f"（{reason[:50]}），{wait} 秒后继续")
                    time.sleep(wait)

            if not is_valid_article(art):
                self.failed.append({'no': p['no'], 'title': p['title'],
                                    'url': p['url'], 'error': reason})
                self.log("    失败，跳过\n")
                continue

            try:
                files = self._export(art, p['filename'])
                self.results.append({
                    'no': p['no'], 'title': art.title, 'url': p['url'],
                    'files': files, 'account': art.account_name,
                    'author': art.author, 'publish_time': art.publish_time,
                })
                size = os.path.getsize(files[0]) if files else 0
                self.log(f"    完成（{size:,} 字节）\n")
            except Exception as e:
                self.failed.append({'no': p['no'], 'title': p['title'],
                                    'url': p['url'],
                                    'error': f"导出失败：{e}"})
                self.log(f"    导出失败：{e}\n")

            time.sleep(3)

        if 'excel' in self.formats and self.results:
            try:
                self._export_excel()
                self.log("已生成元数据表格\n")
            except Exception as e:
                self.log(f"元数据表格生成失败：{e}")

        return self.results, self.failed

    def _export(self, art, filename):
        """按所选格式导出单篇文章，返回生成的文件路径列表。"""
        files = []

        if 'md' in self.formats:
            md = self.md_exporter._convert_to_markdown(art)
            path = os.path.join(self.output_dir, f"{filename}.md")
            with open(path, 'w', encoding='utf-8') as f:
                f.write(md)
            files.append(path)

        if 'html' in self.formats:
            art.title = filename      # HTML 导出器以 title 决定文件名
            files.append(self.html_exporter.export(art, self.output_dir))

        return files

    def _export_excel(self):
        """导出元数据表格（ExcelExporter 需要 ArticleData 列表）。"""
        rows = []
        for r in self.results:
            art = ArticleData()
            art.title = r['title']
            art.url = r['url']
            art.account_name = r.get('account', '')
            art.author = r.get('author', '')
            art.publish_time = r.get('publish_time', '')
            art.success = True
            rows.append(art)
        if rows:
            self.excel_exporter.export(rows, self.output_dir)

    # ---------- 校验 ----------
    def verify(self, plan=None):
        """检查输出完整性，返回问题列表。"""
        problems = []
        plan = plan or [p for p in build_plan(self.results)]

        for p in plan:
            path = os.path.join(self.output_dir, f"{p['filename']}.md")
            if 'md' not in self.formats:
                continue
            if not os.path.exists(path):
                problems.append((p['no'], '缺失', p['filename']))
                continue
            size = os.path.getsize(path)
            if size < 3000:
                problems.append((p['no'], f'过小({size})', p['filename']))
                continue
            txt = open(path, encoding='utf-8').read()
            if '**公众号**' not in txt or '\n---\n' not in txt:
                problems.append((p['no'], '元数据异常', p['filename']))
        return problems