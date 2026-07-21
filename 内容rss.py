import pandas as pd
import requests
from bs4 import BeautifulSoup
import datetime
import urllib3
import ssl
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import warnings
import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import Dict, List, Optional, Tuple, Set
from urllib3.util.retry import Retry

# ====================== 全局常量配置区 ======================
DEPLOYED_BASE_URL = "https://feitoudaerfeitoudaer.github.io"
COUNTRY_COLUMN = '描述'
MAX_ARTICLE_PER_SITE = 3
SUMMARY_MAX_CHAR = 1500

# ========= 重要：云端GitHub Action专用并发参数 =========
THREAD_POOL_STAGE1 = 8
THREAD_POOL_STAGE2 = 12
REQUEST_TIMEOUT = 8
PROXY_TEST_TIMEOUT = 5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# RSS标准命名空间（Feedly图片支持 media）
NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"
NS_ATOM = "http://www.w3.org/2005/Atom"
NS_MEDIA = "http://search.yahoo.com/mrss/"
WEBSUB_HUB = "https://pubsubhubbub.appspot.com/"

# 关闭警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")


class CustomSSLAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, retries, **kwargs):
        self.retry_strategy = retries
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        ctx.check_hostname = False
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)


def create_http_session() -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=2,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    adapter = CustomSSLAdapter(retries=retry_strategy)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


def detect_proxy() -> Optional[Dict[str, str]]:
    """仅本地电脑检测6917代理，GitHub云端自动跳过"""
    proxies = {'http': 'http://127.0.0.1:6917', 'https': 'http://127.0.0.1:6917'}
    try:
        resp = requests.get(
            'https://www.google.com',
            proxies=proxies,
            timeout=PROXY_TEST_TIMEOUT,
            verify=False
        )
        if resp.status_code == 200:
            print("💡 [网络状态] 检测到本地代理有效，启用6917转发")
            return proxies
    except Exception:
        print("💡 [网络状态] 未检测到代理，使用直连模式")
    return None


def clean_xml_string(v: Optional[str]) -> str:
    if not v:
        return ""
    val = str(v)
    val = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F]', '', val)
    val = val.replace("]]>", "]]&gt;")
    return val


def normalize_url(base: str, href: str) -> str:
    href = href.strip()
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        domain = '/'.join(base.rstrip("/").split('/')[:3])
        return domain + href
    return href


def parse_rfc1123_datetime(dt: datetime.datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0800")


def extract_article_publish_date(soup: BeautifulSoup) -> Optional[datetime.datetime]:
    meta_pub = soup.find("meta", property="article:published_time")
    if meta_pub and meta_pub.get("content"):
        try:
            dt_str = meta_pub["content"]
            dt = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return dt
        except Exception:
            pass
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        try:
            dt_str = time_tag["datetime"]
            dt = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return dt
        except Exception:
            pass
    return None


def fetch_article_detail(article_url: str, headers: dict, session: requests.Session, proxies: Optional[dict]) -> Tuple[str, str, str, Optional[datetime.datetime]]:
    author_text = "智库研究员"
    publish_dt = None
    try:
        res = session.get(
            article_url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            verify=False,
            proxies=proxies
        )
        if res.status_code != 200:
            return "", "", author_text, publish_dt
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'lxml')

        publish_dt = extract_article_publish_date(soup)
        meta_author = soup.find("meta", property="article:author") or soup.find("meta", name="author")
        if meta_author and meta_author.get("content"):
            author_text = meta_author["content"].strip()

        img_url = ""
        meta_img = soup.find('meta', property='og:image') or soup.find('meta', name='twitter:image')
        if meta_img and meta_img.get("content"):
            img_url = meta_img["content"]
        else:
            img_tag = soup.find('img', src=re.compile(r'uploads|article|wp-content|report', re.I))
            if img_tag and img_tag.get("src"):
                img_url = normalize_url(article_url, img_tag["src"])

        for selector in ['nav', 'footer', 'script', 'style', 'header', 'noscript', 'aside']:
            for tag in soup.find_all(selector):
                tag.decompose()

        article_body = (
            soup.find('article')
            or soup.find('main')
            or soup.find('div', class_=re.compile(r'article-content|post-content|story-body|entry-content|report-body'))
        )

        if not article_body:
            max_p = 0
            best_div = None
            for div in soup.find_all("div"):
                cnt = len(div.find_all("p"))
                if cnt > max_p:
                    max_p = cnt
                    best_div = div
            article_body = best_div

        paragraphs = []
        if article_body:
            paragraphs = [
                p.get_text(strip=True)
                for p in article_body.find_all("p")
                if len(p.get_text(strip=True)) > 15
            ]

        full_text = "\n".join(paragraphs) if paragraphs else "点击下方链接阅读智库官方原文。"
        return img_url, full_text, author_text, publish_dt
    except Exception:
        return "", "", author_text, publish_dt


def fetch_links_from_homepage(row: pd.Series, headers: dict, session: requests.Session, proxies: Optional[dict]) -> List[dict]:
    site_name = row['网站名称']
    base_url = row['网站链接']
    country = row[COUNTRY_COLUMN]
    target_tasks: List[dict] = []
    seen_urls: Set[str] = set()

    try:
        resp = session.get(
            base_url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            verify=False,
            proxies=proxies
        )
        if resp.status_code != 200:
            return target_tasks
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, 'lxml')

        domain_keyword = base_url.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
        junk_keywords = {'about', 'contact', 'search', 'privacy', 'terms', 'twitter',
                         'facebook', 'linkedin', 'careers', 'experts', 'events', 'donate'}

        for link in soup.find_all('a', href=True):
            raw_href = link['href']
            full_href = normalize_url(base_url, raw_href)
            href_stripped = full_href.rstrip("/")
            base_stripped = base_url.rstrip("/")

            if domain_keyword not in full_href or href_stripped == base_stripped:
                continue
            if any(k in full_href.lower() for k in junk_keywords):
                continue

            link_text = link.get_text(strip=True)
            if len(link_text) < 20 or full_href in seen_urls:
                continue

            seen_urls.add(full_href)
            target_tasks.append({
                "site_name": site_name,
                "country": country,
                "href": full_href,
                "link_text": link_text,
                "headers": headers
            })
            if len(target_tasks) >= MAX_ARTICLE_PER_SITE:
                break
    except Exception:
        pass
    return target_tasks


def process_single_article_task(task: dict, global_proxies) -> Optional[Tuple[str, dict]]:
    session = create_http_session()
    href = task["href"]
    headers = task["headers"]
    link_text = task["link_text"]
    site_name = task["site_name"]

    img_url, full_detail_text, author_name, article_datetime = fetch_article_detail(href, headers, session, global_proxies)
    session.close()

    if not full_detail_text:
        return None

    # 文案严格对齐Feedly截图样式
    list_description = f"作者：{author_name}。这篇文章最初发表在{site_name}官方网站上。"
    summary_text = full_detail_text[:SUMMARY_MAX_CHAR]
    if len(full_detail_text) > SUMMARY_MAX_CHAR:
        summary_text += "\n\n...(详细内容较长，已自动折叠)..."

    html_content = (
        f"<div style='max-width:660px; margin:0 auto; font-family:-apple-system,BlinkMacSystemFont,sans-serif; font-size:16px; line-height:1.8; color:#333;'>"
        f"<h2>{html.escape(link_text)}</h2>"
        f"<p style='color:#666; font-size:14px;'>发布源: {html.escape(site_name)} | 作者：{html.escape(author_name)}</p><hr/>"
        f"<p style='margin-bottom:1.5em; text-indent:2em;'>"
        f"{summary_text.replace('\n', '</p><p style=\"margin-bottom:1.5em; text-indent:2em;\">')}"
        f"</p>"
        f"<br/><p><a href='{html.escape(href)}' target='_blank' style='color:#1a73e8;font-weight:bold;text-decoration:none;'>👉 点击这里，阅读该智库官方原文全文</a></p>"
        f"</div>"
    )

    title_clean = clean_xml_string(link_text)
    desc_clean = clean_xml_string(list_description)
    content_clean = clean_xml_string(html_content)

    if article_datetime:
        pub_date_str = parse_rfc1123_datetime(article_datetime)
    else:
        pub_date_str = parse_rfc1123_datetime(datetime.datetime.now())

    return task["country"], {
        "title": title_clean,
        "site_name": site_name,
        "link": html.escape(href),
        "description": desc_clean,
        "content_encoded": content_clean,
        "pub_date": pub_date_str,
        "image_url": img_url,
        "author": author_name
    }


def ping_feedly_websub(feed_url: str, session: requests.Session):
    data = {
        "hub.mode": "publish",
        "hub.url": feed_url
    }
    try:
        resp = session.post(WEBSUB_HUB, data=data, timeout=5)
        if resp.status_code in (200, 204):
            print(f"📢 [WebSub广播成功] {feed_url}")
        else:
            print(f"⚠️ [WebSub广播失败] {feed_url} status={resp.status_code}")
    except Exception as e:
        print(f"⚠️ [WebSub异常] {feed_url} {str(e)[:60]}")


def build_rss_xml(country: str, items: List[dict], feed_base: str) -> Tuple[str, bytes]:
    safe_country = "".join([c for c in country if c.isalnum() or c == '_']).strip()
    if not safe_country:
        safe_country = "Other"
    filename = f"rss_{safe_country}.xml"
    feed_url = f"{feed_base.rstrip('/')}/{filename}"
    now_str = parse_rfc1123_datetime(datetime.datetime.now())

    ET.register_namespace("content", NS_CONTENT)
    ET.register_namespace("atom", NS_ATOM)
    ET.register_namespace("media", NS_MEDIA)

    rss = ET.Element("rss", {
        "version": "2.0",
        "xmlns:content": NS_CONTENT,
        "xmlns:atom": NS_ATOM,
        "xmlns:media": NS_MEDIA
    })
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = f"{safe_country}智库报告聚合"
    ET.SubElement(channel, "link").text = feed_url
    ET.SubElement(channel, "description").text = f"{safe_country} 全量智库文章详细全文聚合流"
    ET.SubElement(channel, "language").text = "zh-cn"
    ET.SubElement(channel, "lastBuildDate").text = now_str

    ET.SubElement(channel, f"{{{NS_ATOM}}}link", {"rel": "hub", "href": WEBSUB_HUB})
    ET.SubElement(channel, f"{{{NS_ATOM}}}link", {"rel": "self", "href": feed_url, "type": "application/rss+xml"})

    for item in items:
        item_node = ET.SubElement(channel, "item")
        ET.SubElement(item_node, "title").text = item['title']
        ET.SubElement(item_node, "link").text = item["link"]
        ET.SubElement(item_node, "description").text = item["description"]
        ET.SubElement(item_node, f"{{{NS_CONTENT}}}encoded").text = item["content_encoded"]
        ET.SubElement(item_node, "category").text = item["site_name"]
        ET.SubElement(item_node, "guid", {"isPermaLink": "true"}).text = item["link"]
        ET.SubElement(item_node, "pubDate").text = item["pub_date"]
        if item.get("image_url") and item["image_url"]:
            ET.SubElement(item_node, f"{{{NS_MEDIA}}}content", {"url": item["image_url"], "medium": "image"})
        ET.SubElement(item_node, "author").text = f"{item['author']}"

    raw_bytes = ET.tostring(rss, encoding="utf-8", method="xml")
    try:
        dom = minidom.parseString(raw_bytes)
        pretty_str = dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
        if not pretty_str.startswith("<?xml"):
            pretty_str = '<?xml version="1.0" encoding="utf-8"?>\n' + pretty_str
        output_bytes = pretty_str.encode("utf-8")
    except Exception:
        output_bytes = b'<?xml version="1.0" encoding="utf-8"?>\n' + raw_bytes

    return filename, output_bytes


def main():
    global_proxies = detect_proxy()
    base_headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://google.com',
    }

    print("\n📂 正在读取智库清单Excel...")
    df = pd.read_excel("penn_library_deduplicated_think_tanks.xlsx")
    df[COUNTRY_COLUMN] = df[COUNTRY_COLUMN].fillna("Other").astype(str).str.strip()
    print(f"✅ 读取完成，共 {len(df)} 家智库")

    print("\n🚀 [第一阶段] 提取智库主页文章链接...")
    all_tasks: List[dict] = []
    stage1_session = create_http_session()
    with ThreadPoolExecutor(max_workers=THREAD_POOL_STAGE1) as executor:
        futures_map = {
            executor.submit(fetch_links_from_homepage, row, base_headers, stage1_session, global_proxies): row
            for _, row in df.iterrows()
        }
        for fut in as_completed(futures_map):
            res = fut.result()
            if res:
                all_tasks.extend(res)
    stage1_session.close()
    print(f"✅ 链接收集完成，待抓取详情页面总数：{len(all_tasks)}")

    if not all_tasks:
        print("⚠️ 未获取到任何文章链接，程序终止")
        return

    print("\n🚀 [第二阶段] 并发抓取文章正文与封面...")
    country_feed_map: Dict[str, List[dict]] = {}
    success_count = 0
    fail_count = 0
    with ThreadPoolExecutor(max_workers=THREAD_POOL_STAGE2) as executor:
        futures_map = {
            executor.submit(process_single_article_task, task, global_proxies): task
            for task in all_tasks
        }
        for fut in as_completed(futures_map):
            result = fut.result()
            if result:
                success_count += 1
                cty, item_data = result
                if cty not in country_feed_map:
                    country_feed_map[cty] = []
                country_feed_map[cty].append(item_data)
            else:
                fail_count += 1
    print(f"✅ 详情抓取结束：成功 {success_count} 篇，失败 {fail_count} 篇")

    print("\n📦 开始生成国别RSS订阅文件...")
    feed_url_list = []
    for country, item_list in country_feed_map.items():
        fname, xml_bytes = build_rss_xml(country, item_list, DEPLOYED_BASE_URL)
        try:
            with open(fname, "wb") as fp:
                fp.write(xml_bytes)
            size_mb = len(xml_bytes) / (1024 * 1024)
            feed_link = f"{DEPLOYED_BASE_URL.rstrip('/')}/{fname}"
            feed_url_list.append(feed_link)
            print(f"💾 {fname} | 条目:{len(item_list)} | {size_mb:.2f} MB")
        except Exception as e:
            print(f"❌ 文件写入失败 {fname}: {str(e)}")

    push_session = create_http_session()
    print("\n📡 执行WebSub主动推送通知...")
    for fu in feed_url_list:
        ping_feedly_websub(fu, push_session)
    push_session.close()

    print("\n==== 🎉 全流程任务执行完毕 ====")


if __name__ == "__main__":
    main()
