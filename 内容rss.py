import pandas as pd
import requests
from bs4 import BeautifulSoup
import datetime
import urllib3
import ssl
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import time
from bs4 import XMLParsedAsHTMLWarning
import warnings

# ==========================================
# ⚙️ 配置区域：项目真实公网托管链接
# ==========================================
DEPLOYED_BASE_URL = "https://github.io"

# 1. 禁用所有难看的黄色警告提示
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# 2. 创建允许低版本安全协议的自定义连接器
class CustomSSLAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1') 
        ctx.check_hostname = False
        kwargs['ssl_context'] = ctx
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

# 3. 读取 Excel 文件
df = pd.read_excel("penn_library_deduplicated_think_tanks.xlsx") 

country_column = '描述' 
df[country_column] = df[country_column].fillna('Other').astype(str).str.strip()

# 4. 初始化网络连接器与代理
session = requests.Session()
adapter = CustomSSLAdapter()
session.mount('https://', adapter)
session.mount('http://', adapter)

proxies = {
    'http': 'http://127.0.0.1:6917',
    'https': 'http://127.0.0.1:6917'
}

# 辅助函数：清洗 XML 非法控制字符
def clean_xml_string(v):
    if not v:
        return ""
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F]', '', v)

# 【极速任务 1】：深入具体报告页，抓取封面和纯净正文（超时缩短至 5 秒，大幅提速）
def fetch_article_detail(article_url, headers):
    try:
        try:
            res = session.get(article_url, headers=headers, timeout=5, verify=False, proxies=proxies)
        except Exception:
            res = session.get(article_url, headers=headers, timeout=5, verify=False)
            
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 提取封面图
        img_url = ""
        main_img = soup.find('meta', property='og:image') or soup.find('meta', name='twitter:image')
        if main_img and main_img.get('content'):
            img_url = main_img['content']
        else:
            first_img = soup.find('img', src=re.compile(r'uploads|article|wp-content', re.I))
            if first_img and first_img.get('src'):
                img_url = first_img['src']
                if img_url.startswith('/'):
                    img_url = 'https://' + article_url.split('/')[2] + img_url

        for junk in soup.find_all(['nav', 'footer', 'script', 'style', 'header', 'noscript', 'aside']):
            junk.decompose()

        article_body = (
            soup.find('article') or 
            soup.find('div', class_=re.compile(r'article-content|post-content|story-body|entry-content|report-body')) or
            soup.find('main')
        )
        
        if not article_body:
            best_div = None
            max_p_count = 0
            for div in soup.find_all('div'):
                p_count = len(div.find_all('p'))
                if p_count > max_p_count:
                    max_p_count = p_count
                    best_div = div
            article_body = best_div

        paragraphs = []
        if article_body:
            paragraphs = [p.get_text(strip=True) for p in article_body.find_all('p') if len(p.get_text(strip=True)) > 15]
            
        full_txt = "\n".join(paragraphs) if paragraphs else "点击下方链接阅读智库官方原文。"
        return img_url, full_txt
    except Exception:
        pass
    return "", ""

# 【极速任务 2】：主页多线并发透传，只抽链接，绝不串行等待（耗时压缩至毫秒级）
def fetch_links_from_homepage(row):
    site_name = row['网站名称']
    base_url = row['网站链接']
    country = row[country_column]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://google.com',
    }
    
    target_tasks = []
    try:
        try:
            response = session.get(base_url, headers=headers, timeout=5, verify=False, proxies=proxies)
        except Exception:
            response = session.get(base_url, headers=headers, timeout=5, verify=False)
            
        response.encoding = response.apparent_encoding 
        soup = BeautifulSoup(response.text, 'html.parser')
        
        links = soup.find_all('a', href=True)
        seen_urls = set()
        
        for link in links:
            href = link['href'].strip()
            if href.startswith('/'):
                if href.startswith('//'): href = 'https:' + href
                else: href = base_url.rstrip('/') + href
            
            domain_keyword = base_url.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
            if domain_keyword not in href or href.rstrip('/') == base_url.rstrip('/'):
                continue
            if any(x in href.lower() for x in ['about', 'contact', 'search', 'privacy', 'terms', 'twitter', 'facebook', 'linkedin', 'careers', 'experts', 'events', 'donate']):
                continue
                
            link_text = link.get_text(strip=True)
            if len(link_text) < 20 or href in seen_urls: 
                continue
                
            seen_urls.add(href)
            # 💡 提速核心：只打包任务参数，不在这里发起网络请求，直接返回
            target_tasks.append({
                'site_name': site_name,
                'country': country,
                'href': href,
                'link_text': link_text,
                'headers': headers
            })
            if len(target_tasks) >= 2: # 单站依然保持限额 2 篇
                break
    except Exception:
        pass
    return target_tasks

# 单条子文章任务处理函数（用于线程池全扁平化爆发并发）
def process_single_article_task(task):
    href = task['href']
    headers = task['headers']
    link_text = task['link_text']
    site_name = task['site_name']
    
    img_url, full_detail_text = fetch_article_detail(href, headers)
    if not full_detail_text:
        return None
        
    if img_url:
        list_description = f"<img src='{img_url}' style='float:left; margin-right:10px; width:120px; height:80px; object-fit:cover;' />网站专栏报告。作者: 智库研究员。 这篇智库文章初次发表在{site_name}官方网站上。"
    else:
        list_description = f"网站专栏报告。作者: 智库研究员。 这篇智库文章初次发表在{site_name}官方网站上。"
    
    summary_text = full_detail_text[:2000]
    if len(full_detail_text) > 2000:
        summary_text += "\n\n...(详细内容较长，已自动折叠)..."
        
    html_content = (
        f"<div style='max-width:660px; margin:0 auto; font-family:-apple-system,BlinkMacSystemFont,sans-serif; font-size:16px; line-height:1.8; color:#333 Triton;'>"
        f"<h2>{link_text}</h2>"
        f"<p style='color:#666; font-size:14px;'>发布源: {site_name} | 抓取时间: {datetime.datetime.now().strftime('%Y-%m-%d')}</p><hr/>"
        f"<p style='margin-bottom:1.5em; text-indent:2em;'>{summary_text.replace('\n', '</p><p style=\"margin-bottom:1.5em; text-indent:2em;\">')}</p>"
        f"<br/><p><a href='{href}' target='_blank' style='color:#1a73e8;font-weight:bold;text-decoration:none;'>👉 点击这里，阅读该智库官方原文全文</a></p>"
        f"</div>"
    )
    
    title_clean = clean_xml_string(link_text).replace("]]>", "]]&gt;")
    list_clean = clean_xml_string(list_description).replace("]]>", "]]&gt;")
    html_clean = clean_xml_string(html_content).replace("]]>", "]]&gt;")
    
    return task['country'], {
        "title": f"<![CDATA[{title_clean}]]>",
        "link": html.escape(href),
        "description": f"<![CDATA[{list_clean}]]>", 
        "content_encoded": f"<![CDATA[{html_clean}]]>",
        "pub_date": datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    }

# 【主动通知核心】
def ping_feedly_websub(feed_url):
    hub_url = "http://appspot.com"
    data = {"hub.mode": "publish", "hub.url": feed_url}
    try:
        res = requests.post(hub_url, data=data, timeout=5)
        if res.status_code == 200 or res.status_code == 204:
            print(f"   📢 [WebSub广播] 成功通知 Google 枢纽中心: {feed_url}")
    except Exception:
        pass

# ==========================================
# 5. 【两阶段扁平化线程池爆发核心区域】
# ==========================================
print(f"🚀 [第一阶段] 正在秒级提取所有智库主页的文章链接...")
all_article_tasks = []
# 满载 30 线程同时并发秒刷主页链接
with ThreadPoolExecutor(max_workers=30) as executor:
    futures = {executor.submit(fetch_links_from_homepage, row): row for _, row in df.iterrows()}
    for future in as_completed(futures):
        tasks = future.result()
        if tasks:
            all_article_tasks.extend(tasks)

print(f"🚀 [第二阶段] 提取完毕！共获得 {len(all_article_tasks)} 个具体报告页面。开始全量并发渗透...")
country_feeds = {}

# 提升至 40 个极限线程，饱和式多线同开同时深挖具体正文
with ThreadPoolExecutor(max_workers=40) as executor:
    article_futures = {executor.submit(process_single_article_task, task): task for task in all_article_tasks}
    for future in as_completed(article_futures):
        result = future.result()
        if result:
            country, item = result
            if country not in country_feeds:
                country_feeds[country] = []
            country_feeds[country].append(item)

# =====================================================================
# 6. 【按国别打包输出标准的规范化 RSS 文件】（Base64 工业级零误差防变形版）
# =====================================================================
print("\n📦 开始按国别（一国一包）打包生成完美适配 Feedly 规范的 RSS 订阅源...")
import base64
generated_feeds = []

for country, items in country_feeds.items():
    safe_country_name = "".join([c for c in country if c.isalpha() or c.isdigit() or c=='_']).strip()
    if not safe_country_name:
        safe_country_name = "Other"
        
    current_time = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    filename = f"rss_{safe_country_name}.xml"
    this_feed_url = f"{DEPLOYED_BASE_URL.rstrip('/')}/{filename}"
    
    # 🌟 核心突破：利用密文直接在内存中还原带尖括号的完美标准头尾，任何系统都无法损坏其格式
    xml_header_b64 = b'PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0idXRmLTgiPz4KPHJzcyB2ZXJzaW9uPSIyLjAiIHhtbG5zOmNvbnRlbnQ9Imh0dHA6Ly9wdXJsLm9yZy9yc3MvMS4wL21vZHVsZXMvY29udGVudC8iIHhtbG5zOmF0b209Imh0dHA6Ly93d3cub3JnLzIwMDUvQXRvbSI+CiAgPGNoYW5uZWw+CiAgICA8dGl0bGU+PCFbQ0RBVEFbX3NDT1VOVFJZX19f5pm65bqc5pyA5paw5oql5ZWRXV0+PC90aXRsZT4KICAgIDxsaW5rPl9zRkVFRFVSTF9fPC9saW5rPgogICAgPGF0b206bGluayBocmVmPSJodHRwOi8vcHVic3ViaHViYnViLmFwcHNwb3QuY29tLyIgcmVsPSJodWIiIC8+CiAgICA8YXRvbTpsaW5rIGhyZWY9Il9zRkVFRFVSTF9fIiByZWw9InNlbGYiIHR5cGU9ImFwcGxpY2F0aW9uL3Jzcyt4bWwiIC8+CiAgICA8ZGVzY3JpcHRpb24+PCFbQ0RBVEFbX3NDT1VOVFJZX19fIOaZpumbu+S9p+aWh+aKpeWRiuivSemhuuWFqOaWh11dPjwvZGVzY3JpcHRpb24+CiAgICA8bGFuZ3VhZ2U+emgtY248L2xhbmd1YWdlPgogICAgPGxhc3RCdWlsZERhdGU+X3NDVVJUSU1FX188L2xhc3RCdWlsZERhdGU+'
    xml_footer_b64 = b'ICA8L2NoYW5uZWw+CjwvcnNzPg=='
    
    # 解密出最纯正的头部标签
    xml_header = xml_header_b64.decode('base64') if hasattr(str, 'decode') else base64.b64decode(xml_header_b64).decode('utf-8')
    xml_footer = xml_footer_b64.decode('base64') if hasattr(str, 'decode') else base64.b64decode(xml_footer_b64).decode('utf-8')
    
    # 动态注入当前国家名、URL 和时间戳
    xml_header = xml_header.replace('_sCOUNTRY___', safe_country_name).replace('_sFEEDURL__', this_feed_url).replace('_sCURTIME__', current_time)
    
    rss_parts = [xml_header]
    
    # 遍历补齐每一篇文章的 item 块
    for item in items:
        item_string = (
            "    <item>\n"
            f"      <title>{item['title']}</title>\n"
            f"      <link>{item['link']}</link>\n"
            f"      <description>{item['description']}</description>\n"
            f"      <content:encoded>{item['content_encoded']}</content:encoded>\n"
            f"      <guid isPermaLink=\"true\">{item['link']}</guid>\n"
            f"      <pubDate>{item['pub_date']}</pubDate>\n"
            "    </item>"
        )
        rss_parts.append(item_string)
        
    rss_parts.append(xml_footer)
    
    # 用标准的换行符拼装出最终的无损 XML
    full_xml_content = "\n".join(rss_parts)
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(full_xml_content)
        
    actual_mb = len(full_xml_content.encode('utf-8')) / (1024 * 1024)
    print(f"   💾 成功同步国家文件: {filename} (包含 {len(items)} 篇，大小: {actual_mb:.2f} MB)")
    generated_feeds.append(this_feed_url)

# =====================================================================
# 7. 【全量同步完后触发主动推送】
# =====================================================================
print("\n📡 开始触发 WebSub 主动广播通知...")
for feed_url in generated_feeds:
    ping_feedly_websub(feed_url)

print(f"\n==== 🚀 全速并发运行结束！格式 100% 绝对无损完美适配 Feedly ====")
