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
# ⚙️ 配置区域：已为你修改为 my-rss 项目的真实公网托管链接
# ==========================================
DEPLOYED_BASE_URL = "https://feitoudaerfeitoudaer.github.io"

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

# 4. 初始化网络连接器与智能自适应代理
session = requests.Session()
adapter = CustomSSLAdapter()
session.mount('https://', adapter)
session.mount('http://', adapter)

# 智能自适应代理测试
PROXIES_CONFIG = None
try:
    test_proxies = {'http': 'http://127.0.0.1:6917', 'https': 'http://127.0.0.1:6917'}
    test_res = requests.get('https://google.com', proxies=test_proxies, timeout=1.5)
    if test_res.status_code == 200:
        PROXIES_CONFIG = test_proxies
        print("   💡 [网络状态] 检测到本地代理有效，已成功启用 6917 转发加速机制。")
except Exception:
    print("   💡 [网络状态] 未检测到本地局域网代理（或正运行于 GitHub 云端），已切换为原生直连模式。")

# 辅助函数：清洗 XML 非法控制字符
def clean_xml_string(v):
    if not v:
        return ""
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F]', '', v)

# 【极速任务 1】：深入具体报告页，抓取封面和纯净正文
def fetch_article_detail(article_url, headers):
    try:
        res = session.get(article_url, headers=headers, timeout=4, verify=False, proxies=PROXIES_CONFIG)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'html.parser')
        
        img_url = ""
        main_img = soup.find('meta', property='og:image') or soup.find('meta', name='twitter:image')
        if main_img and main_img.get('content'):
            img_url = main_img['content']
        else:
            first_img = soup.find('img', src=re.compile(r'uploads|article|wp-content', re.I))
            if first_img and first_img.get('src'):
                img_url = first_img['src']
                if img_url.startswith('/'):
                    img_url = 'https://' + article_url.split('/') + img_url

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

# 【极速任务 2】：主页多线并发透传，只抽链接
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
        response = session.get(base_url, headers=headers, timeout=4, verify=False, proxies=PROXIES_CONFIG)
        response.encoding = response.apparent_encoding 
        soup = BeautifulSoup(response.text, 'html.parser')
        
        links = soup.find_all('a', href=True)
        seen_urls = set()
        
        for link in links:
            href = link['href'].strip()
            if href.startswith('/'):
                if href.startswith('//'): href = 'https:' + href
                else: href = base_url.rstrip('/') + href
            
            domain_keyword = base_url.replace('https://', '').replace('http://', '').replace('www.', '').split('/')
            if domain_keyword not in href or href.rstrip('/') == base_url.rstrip('/'):
                continue
            if any(x in href.lower() for x in ['about', 'contact', 'search', 'privacy', 'terms', 'twitter', 'facebook', 'linkedin', 'careers', 'experts', 'events', 'donate']):
                continue
                
            link_text = link.get_text(strip=True)
            if len(link_text) < 20 or href in seen_urls: 
                continue
                
            seen_urls.add(href)
            target_tasks.append({
                'site_name': site_name,
                'country': country,
                'href': href,
                'link_text': link_text,
                'headers': headers
            })
            if len(target_tasks) >= 3: 
                break
    except Exception:
        pass
    return target_tasks

# 单条子文章任务处理函数
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
        list_description = f"网站专栏报告. 作者: 智库研究员. 这篇智库文章初次发表在{site_name}官方网站上。"
    
    summary_text = full_detail_text[:2000]
    if len(full_detail_text) > 2000:
        summary_text += "\n\n...(详细内容较长，已自动折叠)..."
        
    html_content = (
        f"<div style='max-width:660px; margin:0 auto; font-family:-apple-system,BlinkMacSystemFont,sans-serif; font-size:16px; line-height:1.8; color:#333;'>"
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
        "title": title_clean,
        "site_name": site_name,  # 🌟 传递智库网站名字作为分类
        "link": html.escape(href),
        "description": list_clean, 
        "content_encoded": html_clean,
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
with ThreadPoolExecutor(max_workers=30) as executor:
    futures = {executor.submit(fetch_links_from_homepage, row): row for _, row in df.iterrows()}
    for future in as_completed(futures):
        tasks = future.result()
        if tasks:
            all_article_tasks.extend(tasks)

print(f"🚀 [第二阶段] 提取完毕！共获得 {len(all_article_tasks)} 个具体报告页面。开始全量高并发渗透...")
# 回归按国家分类打包，极大节省 Feedly 链接额度
country_feeds = {}

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
# 6. 【安全零额度聚合模式】（工业级字典扁平化架构：100% 免疫任何排版错乱）
# =====================================================================
print("\n📦 开始按国别打包生成完美隔离、零额度损耗的 RSS 订阅源...")
generated_feeds = []

# 用底层的字符编码构建尖括号，防止任何聊天框或传输系统拦截吞字
LT = chr(60)  # 左尖括号 <
RT = chr(62)  # 右尖括号 >

for country, items in country_feeds.items():
    safe_country_name = "".join([c for c in country if c.isalpha() or c.isdigit() or c=='_']).strip()
    if not safe_country_name:
        safe_country_name = "Other"
        
    current_time = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    filename = f"rss_{safe_country_name}.xml"
    this_feed_url = f"{DEPLOYED_BASE_URL.rstrip('/')}/{filename}"
    
    # 🌟 将 XML 骨架拆散成最基础的纯文本单元，用逗号严格隔开，彻底杜绝排版压缩
    meta_elements = [
        ["?xml version=\"1.0\" encoding=\"utf-8\"?", ""],
        ["rss version=\"2.0\" xmlns:content=\"http://purl.org\" xmlns:atom=\"http://w3.org\"", ""],
        ["channel", "  "],
        ["title", "    ", f"![CDATA[{safe_country_name}智库报告聚合]]"],
        ["link", "    ", this_feed_url],
        ["atom:link href=\"http://appspot.com\" rel=\"hub\" /", "    "],
        ["atom:link href=\"" + this_feed_url + "\" rel=\"self\" type=\"application/rss+xml\" /", "    "],
        ["description", "    ", f"![CDATA[{safe_country_name} 全量智库文章详细全文聚合流]]"],
        ["language", "    ", "zh-cn"],
        ["lastBuildDate", "    ", current_time]
    ]
    
    rss_lines = []
    # 1. 自动生成标准且带有漂亮缩进的头部标签
    for elem in meta_elements:
        tag_name = elem[0]
        indent = elem[1]
        if len(elem) == 3:
            content_val = elem[2]
            rss_lines.append(f"{indent}{LT}{tag_name}{RT}{LT}{content_val}{RT}{LT}/{tag_name.split()[0]}{RT}")
        else:
            rss_lines.append(f"{indent}{LT}{tag_name}{RT}")
            
    # 2. 循环遍历并精准注入每一篇文章的 item 块
    for item in items:
        rss_lines.append("    " + LT + "item" + RT)
        
        # 注入标题 (带有专属智库前缀)
        rss_lines.append("      " + LT + "title" + RT + LT + "![CDATA[[" + item["site_name"] + "] " + item["title"] + "]]" + RT + LT + "/title" + RT)
        # 注入链接
        rss_lines.append("      " + LT + "link" + RT + item["link"] + LT + "/link" + RT)
        # 注入中间栏精简图文摘要描述
        rss_lines.append("      " + LT + "description" + RT + LT + "![CDATA[" + item["description"] + "]]" + RT + LT + "/description" + RT)
        # 注入右侧沉浸式详情富文本
        rss_lines.append("      " + LT + "content:encoded" + RT + LT + "![CDATA[" + item["content_encoded"] + "]]" + RT + LT + "/content:encoded" + RT)
        # 🌟 障眼法核心：注入 category 标签，在不消耗任何额度的情况下，让 Feedly 自动激活网站单点过滤过滤机制
        rss_lines.append("      " + LT + "category" + RT + LT + "![CDATA[" + item["site_name"] + "]]" + RT + LT + "/category" + RT)
        # 注入永久链接唯一标识符
        rss_lines.append("      " + LT + "guid isPermaLink=\"true\"" + RT + item["link"] + LT + "/guid" + RT)
        # 注入具体发布时间
        rss_lines.append("      " + LT + "pubDate" + RT + item["pub_date"] + LT + "/pubDate" + RT)
        
        rss_lines.append("    " + LT + "/item" + RT)
        
    # 3. 严格闭合频道根节点
    rss_lines.append("  " + LT + "/channel" + RT)
    rss_lines.append(LT + "/rss" + RT)
    
    # 拼接并保存为最终完美的静态无损 XML 文件
    full_xml_content = "\n".join(rss_lines)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(full_xml_content)
        
    actual_mb = len(full_xml_content.encode('utf-8')) / (1024 * 1024)
    print(f"   💾 成功创建合并文件: {filename} (包含 {len(items)} 篇，大小: {actual_mb:.2f} MB)")
    generated_feeds.append(this_feed_url)

# =====================================================================
# 7. 【全量同步完后触发主动推送】
# =====================================================================
print("\n📡 开始触发 WebSub 主动广播通知...")
for feed_url in generated_feeds:
    ping_feedly_websub(feed_url)

print(f"\n==== 🚀 全量聚合完毕！完全不消耗订阅额度，完美适配 Feedly 免费版 ====")
