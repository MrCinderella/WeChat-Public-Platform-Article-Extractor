"""
公众号文章爬取与解析模块

负责从微信公众号文章URL抓取页面、解析元数据和正文内容、下载图片资源。
支持三种URL类型：
  1. 单篇文章链接（mp.weixin.qq.com/s/xxx 或 mp.weixin.qq.com/s?__biz=...）
  2. 公众号主页链接（mp.weixin.qq.com/mp/profile_ext?action=home&__biz=...）
  3. 合集/专栏链接（mp.weixin.qq.com/mp/appmsgalbum?__biz=...&album_id=...）
处理反爬机制（User-Agent伪装、Referer头、请求间隔、Cookie持久化）。
"""

import re
import os
import time
import json
import random
from dataclasses import dataclass, field
from typing import Optional, List

import requests
from bs4 import BeautifulSoup


@dataclass
class ArticleData:
    """单篇文章的完整数据结构"""
    url: str = ""                        # 文章原始链接
    title: str = "未命名文章"             # 文章标题
    author: str = ""                     # 作者
    account_name: str = ""              # 公众号名称
    publish_time: str = ""              # 发布时间
    content_html: str = ""              # 正文HTML（图片已下载、URL已更新）
    raw_html: str = ""                  # 原始页面HTML
    image_map: dict = field(default_factory=dict)  # {原始URL: 本地相对路径}
    success: bool = False               # 是否抓取成功
    error: str = ""                     # 失败原因

    @property
    def safe_title(self) -> str:
        """生成文件系统安全的标题"""
        # 去除文件名非法字符，限制长度
        safe = re.sub(r'[\\/:*?"<>|\n\r\t]', '', self.title).strip()
        if len(safe) > 80:
            safe = safe[:80]
        return safe if safe else "未命名文章"


class ArticleScraper:
    """公众号文章爬取器"""

    # 模拟 Chrome 浏览器的请求头
    DEFAULT_HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
        'Accept': (
            'text/html,application/xhtml+xml,application/xml;'
            'q=0.9,image/avif,image/webp,*/*;q=0.8'
        ),
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': 'https://mp.weixin.qq.com/',
    }

    def __init__(
        self,
        output_dir: str = "output",
        delay_range: tuple = (1.0, 3.0),
        timeout: int = 30,
        max_retries: int = 2,
    ):
        """
        初始化爬取器

        Args:
            output_dir: 输出根目录
            delay_range: 请求间隔随机范围（秒）
            timeout: 请求超时时间（秒）
            max_retries: 失败重试次数
        """
        self.output_dir = output_dir
        self.delay_range = delay_range
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        self.cookies_str = ""  # 用户提供的Cookie字符串

    def set_cookies(self, cookies_str: str):
        """设置Cookie字符串用于需要登录态的请求（公众号主页等）

        Args:
            cookies_str: Cookie字符串，格式如 "name1=val1; name2=val2"
        """
        self.cookies_str = cookies_str.strip()
        if self.cookies_str:
            # 解析Cookie字符串并设置到session
            for item in self.cookies_str.split(';'):
                item = item.strip()
                if '=' in item:
                    name, _, value = item.partition('=')
                    self.session.cookies.set(name.strip(), value.strip())

    # ==================== URL类型检测与列表抓取 ====================

    @staticmethod
    def detect_url_type(url: str) -> str:
        """
        检测URL类型

        Returns:
            'article'  - 单篇文章
            'profile'  - 公众号主页
            'album'    - 合集/专栏页
            'unknown'  - 未知类型
        """
        url_lower = url.lower().strip()

        # 合集页: mp.weixin.qq.com/mp/appmsgalbum?...&album_id=...
        if 'appmsgalbum' in url_lower or 'album_id=' in url_lower:
            return 'album'

        # 公众号主页: mp.weixin.qq.com/mp/profile_ext?action=home...
        if 'profile_ext' in url_lower and 'action=home' in url_lower:
            return 'profile'

        # 单篇文章: mp.weixin.qq.com/s/xxx 或 mp.weixin.qq.com/s?__biz=...
        if '/s/' in url_lower or url_lower.rstrip('/').endswith('/s'):
            return 'article'
        if '/s?' in url_lower and ('__biz=' in url_lower or 'mid=' in url_lower):
            return 'article'

        # 模糊匹配：包含 __biz 但不是合集/主页的，当作单篇文章
        if '__biz=' in url_lower and 'mid=' in url_lower:
            return 'article'

        return 'unknown'

    def fetch_article_list(self, url: str) -> List[str]:
        """
        从公众号主页或合集页抓取所有文章链接列表

        Args:
            url: 公众号主页URL或合集页URL

        Returns:
            文章URL列表（去重保序）
        """
        url_type = self.detect_url_type(url)

        if url_type == 'album':
            return self._extract_from_album(url)
        elif url_type == 'profile':
            return self._extract_from_profile(url)
        else:
            # 未知类型，按单篇文章处理
            return [url]

    def _extract_from_album(self, url: str) -> List[str]:
        """
        从合集/专栏页提取所有文章链接（支持分页加载）

        合集页初始HTML仅包含前10篇文章，后续文章需要通过
        AJAX接口 action=getalbummsg 分页加载。
        分页参数使用 begin_msgid（最后一条文章的msgid），
        且请求必须携带 Referer: 合集页URL，否则接口拒绝。
        """
        try:
            html = self._fetch_with_retry(url)
        except Exception as e:
            raise Exception(f"合集页抓取失败: {e}")

        # 第一阶段：从初始HTML提取第一批文章链接 + 最后一条的msgid
        article_urls, last_msgid = self._parse_album_html_links(html)

        # 第二阶段：通过AJAX接口分页加载剩余文章
        biz = self._extract_url_param(url, '__biz')
        album_id = self._extract_url_param(url, 'album_id')

        if biz and album_id and last_msgid:
            max_pages = 200  # 安全上限，防止无限循环

            while max_pages > 0:
                max_pages -= 1

                ajax_url = (
                    f"https://mp.weixin.qq.com/mp/appmsgalbum?"
                    f"action=getalbummsg&__biz={biz}&album_id={album_id}"
                    f"&count=10&begin_msgid={last_msgid}"
                    f"&continue_flag=1&f=json"
                )

                self._delay()
                try:
                    # AJAX分页请求必须带 Referer，否则返回验证页
                    resp = self.session.get(
                        ajax_url,
                        timeout=self.timeout,
                        headers={'Referer': url, 'X-Requested-With': 'XMLHttpRequest'},
                    )
                    resp.raise_for_status()
                    resp.encoding = 'utf-8'
                    ajax_text = resp.text
                except Exception:
                    break

                # 解析返回JSON：{"getalbum_resp": {"article_list": [...], "continue_flag": "0|1"}}
                batch_urls, new_msgid, has_more = self._parse_album_ajax_response(ajax_text)

                if not batch_urls:
                    break

                for u in batch_urls:
                    if u not in article_urls:
                        article_urls.append(u)

                # 没有更多文章或msgid未更新（死循环保护）
                if not has_more or not new_msgid or new_msgid == last_msgid:
                    break

                last_msgid = new_msgid

        # 清理URL（去重、去片段、反转义）
        cleaned_urls = []
        for u in article_urls:
            u = u.split('#')[0].replace('&amp;', '&')
            if u and u not in cleaned_urls:
                cleaned_urls.append(u)

        self._delay()
        return cleaned_urls

    def _parse_album_html_links(self, html: str):
        """
        从合集页初始HTML提取文章链接和最后一条文章的msgid

        Returns:
            (文章URL列表, 最后一条文章的msgid)
            msgid用于后续AJAX分页的 begin_msgid 参数
        """
        urls = []
        last_msgid = ""

        # 方法1：解析 cgiData / article_list JSON结构
        # 初始页面中文章数据形如: var cgiData = {"item":[{"title":..,"url":..}],...}
        # 或直接在JS数组中，每条含 url 字段（相对路径 /s?__biz=...&mid=MSGID...）
        url_msgid_pairs = re.findall(
            r'"url"\s*:\s*"((?:\\.|[^"\\])*)"', html
        )
        for raw in url_msgid_pairs:
            link = raw.replace('\\/', '/').replace('\\&', '&').replace('&amp;', '&')
            if 'mp.weixin.qq.com/s' in link or link.startswith('/s?') or '/s/' in link:
                if not link.startswith('http'):
                    link = 'https://mp.weixin.qq.com' + link
                if link not in urls:
                    urls.append(link)
                # 从链接中提取mid作为msgid
                mid = self._extract_url_param(link, 'mid')
                if mid:
                    last_msgid = mid

        # 方法2：从HTML <a> 标签提取
        if not urls:
            try:
                soup = BeautifulSoup(html, 'html.parser')
            except Exception:
                soup = BeautifulSoup(html, 'lxml')
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href'].replace('&amp;', '&')
                if 'mp.weixin.qq.com/s' in href and ('__biz=' in href or '/s/' in href):
                    if href not in urls:
                        urls.append(href)
                    mid = self._extract_url_param(href, 'mid')
                    if mid:
                        last_msgid = mid

        # 方法3：兜底正则
        if not urls:
            pattern = r'(https?://mp\.weixin\.qq\.com/s[^"\'<>\s]*?__biz=[^"\'<>\s]*?sn=[^"\'<>\s]*)'
            for m in re.findall(pattern, html):
                m = m.replace('&amp;', '&')
                if m not in urls:
                    urls.append(m)
                mid = self._extract_url_param(m, 'mid')
                if mid:
                    last_msgid = mid

        # 额外提取独立的 msgid（JSON中可能有 "msgid" 字段）
        if urls and not last_msgid:
            msgids = re.findall(r'"msgid"\s*:\s*"?(\d+)"?', html)
            if msgids:
                last_msgid = msgids[-1]

        return urls, last_msgid

    def _parse_album_ajax_response(self, response_text: str):
        """
        解析合集AJAX分页接口返回的JSON

        Returns:
            (文章URL列表, 最后一条的msgid, 是否还有更多)
        """
        urls = []
        last_msgid = ""
        has_more = False

        try:
            data = json.loads(response_text)
            resp = data.get('getalbum_resp', data)
            article_list = resp.get('article_list', []) or resp.get('msg_list', [])

            for item in article_list:
                link = item.get('link', '') or item.get('url', '')
                if link:
                    link = link.replace('\\/', '/').replace('\\&', '&').replace('&amp;', '&')
                    if not link.startswith('http'):
                        link = 'https://mp.weixin.qq.com' + link
                    if link not in urls:
                        urls.append(link)

                # msgid 优先取字段，其次从link的mid参数提取
                msgid = str(item.get('msgid', '') or item.get('msg_id', ''))
                if not msgid and link:
                    msgid = self._extract_url_param(link, 'mid')
                if msgid:
                    last_msgid = msgid

            # continue_flag: "1"表示还有更多，"0"表示已加载完
            cf = str(resp.get('continue_flag', '0'))
            has_more = (cf == '1') and bool(article_list)

        except (json.JSONDecodeError, KeyError, TypeError):
            # 非JSON响应，用正则兜底提取链接
            pattern = r'"(?:link|url)"\s*:\s*"((?:\\.|[^"\\])*)"'
            for raw in re.findall(pattern, response_text):
                link = raw.replace('\\/', '/').replace('\\&', '&').replace('&amp;', '&')
                if '/s' in link:
                    if not link.startswith('http'):
                        link = 'https://mp.weixin.qq.com' + link
                    if link not in urls:
                        urls.append(link)
                    mid = self._extract_url_param(link, 'mid')
                    if mid:
                        last_msgid = mid
            has_more = False  # 无法判断时停止

        return urls, last_msgid, has_more

    def _extract_url_param(self, url: str, param: str) -> str:
        """从URL查询参数中提取指定参数值"""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        values = params.get(param, [])
        return values[0] if values else ""

    def _extract_js_var(self, html: str, var_name: str) -> str:
        """从页面JavaScript中提取变量值（如 var uin = "xxx"）"""
        # 匹配 var name = "value" 或 var name = 'value'
        pattern = rf'var\s+{var_name}\s*=\s*["\']([^"\']*)["\']'
        match = re.search(pattern, html)
        if match:
            return match.group(1)
        # 也尝试匹配 name = "value" (无var)
        pattern = rf'\b{var_name}\s*=\s*["\']([^"\']*)["\']'
        match = re.search(pattern, html)
        if match:
            return match.group(1)
        return ""

    def _extract_from_profile(self, url: str) -> List[str]:
        """
        从公众号主页提取文章链接（需要Cookie认证）

        公众号主页需要登录态(Cookie)才能返回文章列表。
        未设置Cookie时页面返回空列表或验证页面。
        设置有效Cookie后，通过 msgList 解析第一批文章，
        再通过AJAX接口 action=getmsg 分页加载更多。
        """
        try:
            html = self._fetch_with_retry(url)
        except Exception as e:
            raise Exception(f"公众号主页抓取失败: {e}")

        article_urls = []

        # 方法1：从 msgList JSON变量提取第一批文章
        msg_list_match = re.search(
            r'var\s+msgList\s*=\s*["\'](\{.*?\})["\']', html, re.DOTALL
        )
        if msg_list_match:
            try:
                json_str = msg_list_match.group(1).replace('&amp;', '&')
                data = json.loads(json_str)
                article_urls = self._parse_msglist_json(data)
            except (json.JSONDecodeError, KeyError):
                pass

        # 方法2：直接正则提取文章链接
        if not article_urls:
            pattern = r'(https?://mp\.weixin\.qq\.com/s[^"\'<>\s]*?__biz=[^"\'<>\s]*?sn=[^"\'<>\s]*)'
            matches = re.findall(pattern, html)
            for m in matches:
                m = m.replace('&amp;', '&')
                if m not in article_urls:
                    article_urls.append(m)

        # 方法3：从 <a> 标签提取
        if not article_urls:
            soup = BeautifulSoup(html, 'lxml')
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href'].replace('&amp;', '&')
                if 'mp.weixin.qq.com/s' in href and ('__biz=' in href or '/s/' in href):
                    if href not in article_urls:
                        article_urls.append(href)

        # 方法4：AJAX分页加载更多文章（需要Cookie）
        if article_urls and self.cookies_str:
            biz = self._extract_url_param(url, '__biz')
            if biz:
                offset = len(article_urls)
                max_pages = 50
                while max_pages > 0:
                    max_pages -= 1
                    ajax_url = (
                        f"https://mp.weixin.qq.com/mp/profile_ext?"
                        f"action=getmsg&__biz={biz}&f=json"
                        f"&offset={offset}&count=10"
                    )
                    self._delay()
                    try:
                        ajax_resp = self._fetch_with_retry(ajax_url)
                        batch = self._parse_profile_ajax_response(ajax_resp)
                        if not batch:
                            break
                        article_urls.extend(batch)
                        offset += len(batch)
                        # 检查是否有更多
                        has_more = re.search(
                            r'"can_msg_continue"\s*:\s*(\d)', ajax_resp
                        )
                        if has_more and has_more.group(1) == '0':
                            break
                    except Exception:
                        break

        # 如果完全没有提取到文章，给出明确提示
        if not article_urls:
            if not self.cookies_str:
                raise Exception(
                    "公众号主页需要登录态(Cookie)才能获取文章列表。\n"
                    "请在界面中填写Cookie后重试。\n"
                    "获取Cookie方法：在浏览器中登录微信网页版(mp.weixin.qq.com)，"
                    "按F12打开开发者工具 → Network → 刷新页面 → "
                    "点击任意请求 → 复制Cookie头内容"
                )
            else:
                raise Exception(
                    "未能从公众号主页提取文章链接，Cookie可能已失效或页面结构变化。\n"
                    "请重新获取最新Cookie后重试。"
                )

        self._delay()
        return article_urls

    def _parse_msglist_json(self, data: dict) -> List[str]:
        """解析 msgList JSON 数据，提取文章链接"""
        urls = []
        for item in data.get('list', []):
            ext_info = item.get('app_msg_ext_info', {})
            content_url = ext_info.get('content_url', '')
            if content_url:
                content_url = content_url.replace('&amp;', '&')
                if not content_url.startswith('http'):
                    content_url = 'https://mp.weixin.qq.com' + content_url
                if content_url not in urls:
                    urls.append(content_url)
            # 多图文消息
            for multi in ext_info.get('multi_message_info', []):
                multi_url = multi.get('content_url', '')
                if multi_url:
                    multi_url = multi_url.replace('&amp;', '&')
                    if not multi_url.startswith('http'):
                        multi_url = 'https://mp.weixin.qq.com' + multi_url
                    if multi_url not in urls:
                        urls.append(multi_url)
        return urls

    def _parse_profile_ajax_response(self, response_text: str) -> List[str]:
        """解析公众号主页AJAX返回的JSON，提取文章链接"""
        urls = []
        try:
            data = json.loads(response_text)
            general_msg = data.get('general_msg_list', '')
            if general_msg:
                general_data = json.loads(general_msg)
                urls = self._parse_msglist_json(general_data)
        except (json.JSONDecodeError, KeyError, TypeError):
            # JSON解析失败，正则提取
            patterns = [
                r'"content_url"\s*:\s*["\']([^"\']+s[^"\']*)["\']',
                r'"url"\s*:\s*["\']([^"\']+mp\.weixin\.qq\.com/s[^"\']*)["\']',
            ]
            for pattern in patterns:
                matches = re.findall(pattern, response_text)
                for m in matches:
                    m = m.replace('&amp;', '&')
                    if not m.startswith('http'):
                        m = 'https://mp.weixin.qq.com' + m
                    if m not in urls:
                        urls.append(m)
        return urls

    # ==================== 单篇文章抓取 ====================

    def fetch_article(self, url: str) -> ArticleData:
        """
        抓取并解析单篇文章

        Args:
            url: 公众号文章链接

        Returns:
            ArticleData 对象
        """
        article = ArticleData(url=url.strip())

        try:
            # 发起HTTP请求（带重试）
            html = self._fetch_with_retry(url)
            article.raw_html = html

            # 解析文章内容
            self._parse_article(html, article)

            # 下载文章内图片，更新图片URL
            if article.content_html:
                article.content_html, article.image_map = self._download_images(
                    article.content_html,
                    article.safe_title,
                )

            article.success = True

        except requests.exceptions.Timeout:
            article.error = "请求超时，请检查网络连接或稍后重试"
        except requests.exceptions.ConnectionError:
            article.error = "网络连接失败，请检查网络后重试"
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                article.error = "访问被拒绝(403)，可能触发了反爬限制，请稍后重试"
            elif e.response.status_code == 404:
                article.error = "文章不存在或已被删除(404)"
            else:
                article.error = f"HTTP错误: {e.response.status_code}"
        except Exception as e:
            article.error = f"抓取失败: {str(e)}"
        finally:
            # 请求间隔，避免被封
            self._delay()

        return article

    def _fetch_with_retry(self, url: str) -> str:
        """带重试机制的HTTP请求"""
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                # 公众号页面使用UTF-8编码
                response.encoding = 'utf-8'
                return response.text
            except requests.RequestException as e:
                last_error = e
                if attempt < self.max_retries:
                    # 指数退避等待
                    wait = (attempt + 1) * 2
                    time.sleep(wait)
        raise last_error

    def _parse_article(self, html: str, article: ArticleData):
        """解析文章页面，提取元数据和正文"""
        # 尝试多种解析器，html5lib最宽松，能处理格式不规范的HTML
        soup = None
        for parser in ['html5lib', 'lxml', 'html.parser']:
            try:
                soup = BeautifulSoup(html, parser)
                # 验证能否找到关键元素
                if soup.find(id='js_content') or soup.find(class_='rich_media_content') or soup.title:
                    break
            except Exception:
                continue

        if soup is None:
            soup = BeautifulSoup(html, 'html.parser')

        # 提取标题
        article.title = self._extract_title(soup)
        # 提取公众号名称
        article.account_name = self._extract_account_name(soup)
        # 提取作者
        article.author = self._extract_author(soup, html)
        # 提取发布时间
        article.publish_time = self._extract_publish_time(soup, html)
        # 提取正文内容
        article.content_html = self._extract_content(soup)

        # 如果content_html仍然为空，尝试从raw_html中正则提取
        if not article.content_html:
            article.content_html = self._extract_content_fallback(html)

    def _extract_text(self, element) -> str:
        """安全提取元素文本"""
        if element is None:
            return ""
        text = element.get_text(strip=True)
        return text if text else ""

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """提取文章标题"""
        # 优先从 #activity-name 获取
        el = soup.find(id='activity-name')
        title = self._extract_text(el)
        if title:
            return title

        # 备用：从 og:title meta 标签获取
        meta = soup.find('meta', property='og:title')
        if meta and meta.get('content'):
            return meta['content'].strip()

        # 备用：从 <title> 标签获取
        if soup.title:
            t = soup.title.string
            if t:
                return t.strip().split('|')[0].strip()

        return "未命名文章"

    def _extract_account_name(self, soup: BeautifulSoup) -> str:
        """提取公众号名称"""
        # 尝试多种选择器
        el = soup.find(id='js_name')
        name = self._extract_text(el)
        if name:
            return name

        el = soup.find(class_='profile_nickname')
        name = self._extract_text(el)
        if name:
            return name

        # 备用：从JavaScript变量提取
        match = re.search(r'var\s+nickname\s*=\s*["\']([^"\']+)["\']', soup.text)
        if match:
            return match.group(1)

        return ""

    def _extract_author(self, soup: BeautifulSoup, raw_html: str) -> str:
        """提取作者"""
        # 尝试从元数据区域获取
        # 公众号文章中作者可能在 #js_author_name 或 .rich_media_meta_text
        el = soup.find(id='js_author_name')
        author = self._extract_text(el)
        if author:
            return author

        # 备用：从JavaScript变量提取
        match = re.search(r'var\s+author\s*=\s*["\']([^"\']+)["\']', raw_html)
        if match:
            return match.group(1)

        return ""

    def _extract_publish_time(self, soup: BeautifulSoup, raw_html: str) -> str:
        """提取发布时间"""
        # 方法1：从 #publish_time 元素获取
        el = soup.find(id='publish_time')
        t = self._extract_text(el)
        if t and t != '原创':
            return t

        # 方法2：从 ct JavaScript 变量提取（Unix时间戳）
        match = re.search(r'var\s+ct\s*=\s*["\'](\d{10})["\']', raw_html)
        if match:
            ts = int(match.group(1))
            return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))

        # 方法3：从 publish_time JavaScript 变量提取
        match = re.search(r'var\s+publish_time\s*=\s*["\']([^"\']+)["\']', raw_html)
        if match:
            return match.group(1)

        # 方法4：从页面文本中匹配日期格式
        text = soup.get_text()
        match = re.search(r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?(?:\s+\d{1,2}:\d{2})?)', text)
        if match:
            return match.group(1)

        return ""

    def _extract_content(self, soup: BeautifulSoup) -> str:
        """提取正文HTML，并修复图片URL"""
        # 尝试多种选择器找到正文容器
        content_div = None
        selectors = [
            {'id': 'js_content'},
            {'class_': 'rich_media_content'},
            {'id': 'js_android_content'},
            {'class_': 'rich_media_area_primary'},
        ]
        for sel in selectors:
            content_div = soup.find(**sel)
            if content_div and content_div.get_text(strip=True):
                break
            content_div = None

        if not content_div:
            # 最终尝试：找class包含rich_media的div
            for div in soup.find_all('div'):
                cls = div.get('class', [])
                if isinstance(cls, list):
                    cls = ' '.join(cls)
                if 'rich_media' in str(cls) and div.get_text(strip=True):
                    content_div = div
                    break

        if not content_div:
            return ""

        # 修复图片：将 data-src / data-original 转为 src
        self._fix_image_attributes(content_div)

        # 清理无用的脚本和样式标签
        for tag in content_div.find_all(['script', 'style']):
            tag.decompose()

        # 去除所有 style 属性中的 visibility:hidden 等
        for tag in content_div.find_all(style=True):
            style = tag['style']
            # 移除可能使内容不可见的样式
            style = re.sub(r'visibility\s*:\s*hidden', 'visibility:visible', style)
            style = re.sub(r'display\s*:\s*none', '', style, flags=re.IGNORECASE)
            tag['style'] = style

        # 移除容器上的 visibility:hidden 样式
        if content_div.get('style'):
            content_div['style'] = re.sub(
                r'visibility\s*:\s*hidden', 'visibility:visible',
                content_div['style']
            )

        result = str(content_div)
        # 验证提取的内容不为空（去掉HTML标签后）
        if not re.sub(r'<[^>]+>', '', result).strip():
            return ""

        return result

    def _extract_content_fallback(self, raw_html: str) -> str:
        """备用方法：当BeautifulSoup解析失败时，用正则提取正文"""
        # 尝试提取 id="js_content" 到对应 </div> 之间的内容
        match = re.search(
            r'id=["\']js_content["\'][^>]*>(.*?)</div>\s*(?:<div|</div>\s*<script|<!--)',
            raw_html, re.DOTALL
        )
        if match:
            content = match.group(1)
            # 基本清理
            content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
            content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
            # 修复图片 data-src -> src
            content = re.sub(
                r'data-src=["\']([^"\']+)["\']',
                lambda m: f'src="{m.group(1)}" data-src="{m.group(1)}"',
                content
            )
            if content.strip():
                return f'<div class="rich_media_content">{content}</div>'

        # 最后尝试：提取整个body内容
        body_match = re.search(r'<body[^>]*>(.*?)</body>', raw_html, re.DOTALL)
        if body_match:
            body = body_match.group(1)
            # 去除script和style
            body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL)
            body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.DOTALL)
            if len(re.sub(r'<[^>]+>', '', body).strip()) > 100:
                return body.strip()

        return ""

    def _fix_image_attributes(self, content_div):
        """
        修复图片标签属性
        公众号文章使用 data-src 做懒加载，需要转为 src
        同时处理 data-original 等备选懒加载属性
        """
        for img in content_div.find_all('img'):
            # 优先 data-src，其次 data-original，再次 data-croporisrc
            src = (
                img.get('data-src', '')
                or img.get('data-original', '')
                or img.get('data-croporisrc', '')
            )
            if src:
                img['src'] = src
            # 确保至少有 src 属性，避免渲染破损
            if not img.get('src'):
                img['src'] = img.get('data-src', '')

    def _download_images(self, content_html: str, article_title: str) -> tuple:
        """
        下载文章内所有图片到本地

        Args:
            content_html: 正文HTML
            article_title: 文章标题（用于创建图片文件夹）

        Returns:
            (更新后的HTML, {原始URL: 本地相对路径} 映射表)
        """
        soup = BeautifulSoup(content_html, 'lxml')
        img_map = {}

        # 创建图片存储目录
        img_dir_name = f"{article_title}_images"
        img_dir_path = os.path.join(self.output_dir, img_dir_name)
        os.makedirs(img_dir_path, exist_ok=True)

        img_tags = soup.find_all('img')
        for idx, img in enumerate(img_tags):
            src = img.get('src', '')
            if not src or not src.startswith('http'):
                continue

            try:
                # 下载图片（需要Referer头）
                headers = {'Referer': 'https://mp.weixin.qq.com/'}
                resp = self.session.get(src, headers=headers, timeout=20)
                resp.raise_for_status()

                # 根据Content-Type确定扩展名
                content_type = resp.headers.get('Content-Type', 'image/jpeg')
                ext = self._get_extension(content_type)

                filename = f"img_{idx + 1}{ext}"
                filepath = os.path.join(img_dir_path, filename)

                with open(filepath, 'wb') as f:
                    f.write(resp.content)

                # 更新HTML中的图片路径为相对路径
                relative_path = f"{img_dir_name}/{filename}"
                img['src'] = relative_path
                img_map[src] = relative_path

            except Exception:
                # 图片下载失败不中断整体流程，保留原始URL
                continue

        return str(soup), img_map

    def _get_extension(self, content_type: str) -> str:
        """根据Content-Type返回文件扩展名"""
        ct = content_type.lower()
        if 'png' in ct:
            return '.png'
        elif 'gif' in ct:
            return '.gif'
        elif 'webp' in ct:
            return '.webp'
        elif 'bmp' in ct:
            return '.bmp'
        else:
            return '.jpg'

    def _delay(self):
        """随机延迟，避免触发反爬"""
        delay = random.uniform(*self.delay_range)
        time.sleep(delay)
