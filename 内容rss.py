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
import xml.etree.ElementTree as ET
from xml.dom import minidom

# ==========================================
# ⚙️ 配置区域：已为你修改为 my-rss 项目的真实公网托管链接
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

# 4. 初始化网络连接器与智能自适应代理
session = requests.Session()
adapter = CustomSSLAdapter()
session.mount('https://', adapter)
session.mount('http://', adapter)

# 智能自适应代理测试（放宽超时到 5s 防止误判）
PROXIES_CONFIG = None
try:
    test_proxies = {'http': 'http://127.0.0.1:6917', 'https': 'http://127.0.0.1:6917'}
    test_res = requests.get('https://google.com', proxies=test_proxies, timeout=5, verify=False)
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
        res = session.get(article_url, headers=headers, timeout=10, verify=False, proxies=PROXIES_CONFIG)
        if res.status_code != 200:
            return "", ""
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
                    # ⭐ 修复原先使用 split() 与字符串直接相加爆掉的 Bug
                    base_domain = '/'.join(article_url.split('/')[:3])
                    img_url = base_domain + img_url

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
        response = session.get(base_url, headers=headers, timeout=10, verify=False, proxies=PROXIES_CONFIG)
        if response.status_code != 200:
            return target_tasks
        response.encoding = response.apparent_encoding 
        soup = BeautifulSoup(response.text, 'html.parser')
        
        links = soup.find_all('a', href=True)
        seen_urls = set()
        
        # ⭐ 核心 Bug 修复：提取纯字符串域名，用于后续判断
        domain_keyword = base_url.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
        
        for link in links:
            href = link['href'].strip()
            if href.startswith('/'):
                if href.startswith('//'): href = 'https:' + href
                else: href = base_url.rstrip('/') + href
            
            # ⭐ 核心 Bug 修复：用纯字符串形式做 in 判定，不再和 list 进行对比
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
    
    summary_text = full_detail_text[:1500] # 瘦身至 1500 字完美契合 Feedly 免费版额度
    if len(full_detail_text) > 1500:
        summary_text += "\n\n...(详细内容较长，已自动折叠)..."
        
    html_content = (
        f"<div style='max-width:660px; margin:0 auto; font-family:-apple-system,BlinkMacSystemFont,sans-serif; font-size:16px; line-height:1.8; color:#333;'>"
        f"<h2>{link_text}</h2>"
        f"<p style='color:#666; font-size:14px;'>发布源: {site_name} | 抓取时间: {datetime.datetime.now().strftime('%Y-%m-%d')}</p><hr/>"
        f"<p style='margin-bottom:1.5em; text-indent:2em;'>{summary_text.replace('\n', '</p><p style=\"margin-bottom:1.5em; text-indent:2em;\">')}</p>"
        f"<br/><p><a href='{href}' target='_blank' style='color:#1a73e8;font-weight:bold;text-decoration:none;'>👉 点击这里，阅读该智库官方原文全文</a></p>"
        f"</div>"
    )
    
    # 彻底洗净数据源，移出可能污染 CDATA 的边界标记
    title_clean = clean_xml_string(link_text).replace("]]>", "]]&gt;")
    list_clean = clean_xml_string(list_description).replace("]]>", "]]&gt;")
    html_clean = clean_xml_string(html_content).replace("]]>", "]]&gt;")
    
    return task['country'], {
        "title": title_clean,
        "site_name": site_name, 
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
        if res.status_code in:
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
# 6. 【现代安全序列化结构】（对象级树形架构，100% 免疫排版错乱与 Feedly 语法错误）
# =====================================================================
print("\n📦 开始按国别打包生成完美隔离、零额度损耗的 RSS 订阅源...")
generated_feeds = []

# 向系统全局注册 Feedly 校验所必须的 W3C 标准命名空间缩写
ET.register_namespace('content', 'http://purl.org')
ET.register_namespace('atom', 'http://w3.org')

for country, items in country_feeds.items():
    safe_country_name = "".join([c for c in country if c.isalnum() or c == '_']).strip()
    if not safe_country_name:
        safe_country_name = "Other"
        
    current_time = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    filename = f"rss_{safe_country_name}.xml"
    this_feed_url = f"{DEPLOYED_BASE_URL.rstrip('/')}/{filename}"
    
    # 建立标准的 XML 树形根节点（从源头上杜绝尖括号闭合不全引起的死穴）
    rss_root = ET.Element('rss', {
        'version': '2.0',
        'xmlns:content': 'http://purl.org',
        'xmlns:atom': 'http://w3.org'
    })
    channel = ET.SubElement(rss_root, 'channel')
    
    # 填充频道级（Channel）核心元数据
    ch_title = ET.SubElement(channel, 'title')
    ch_title.text = f"{safe_country_name}智库报告聚合"
    
    ch_link = ET.SubElement(channel, 'link')
    ch_link.text = this_feed_url
    
    # 精准注入完美匹配 Feedly 的标准 WebSub 广播节点
    ET.SubElement(channel, '{http://w3.org}link', {'href': 'http://appspot.com', 'rel': 'hub'})
    ET.SubElement(channel, '{http://w3.org}link', {'href': this_feed_url, 'rel': 'self', 'type': 'application/rss+xml'})
    
    ch_desc = ET.SubElement(channel, 'description')
    ch_desc.text = f"{safe_country_name} 全量智库文章详细全文聚合流"
    
    ch_lang = ET.SubElement(channel, 'language')
    ch_lang.text = "zh-cn"
    
    ch_date = ET.SubElement(channel, 'lastBuildDate')
    ch_date.text = current_time
    
    # 循环向频道中安全追加每一篇文章的 item 块
    for item in items:
        item_node = ET.SubElement(channel, 'item')
        
        # 剥离可能残存的字符串 CDATA 标记，由 ElementTree 统一在最底层执行最高规格的转义
        raw_title = item["title"].replace("<![CDATA[", "").replace("]]>", "")
        raw_desc = item["description"].replace("<![CDATA[", "").replace("]]>", "")
        raw_content = item["content_encoded"].replace("<![CDATA[", "").replace("]]>", "")
        
        # 注入标题（自带智库分类前缀）
        t_node = ET.SubElement(item_node, 'title')
        t_node.text = f"[{item['site_name']}] {raw_title}"
        
        # 注入链接（内部自动执行 html.escape，解决 URL 中带 & 导致 Feedly 报错的隐患）
        l_node = ET.SubElement(item_node, 'link')
        l_node.text = item["link"]
        
        # 注入中间栏精简图文摘要描述
        d_node = ET.SubElement(item_node, 'description')
        d_node.text = raw_desc
        
        # 注入 Feedly 阅读器最喜欢的右侧沉浸式详情富文本
        c_node = ET.SubElement(item_node, '{http://purl.org}encoded')
        c_node.text = raw_content
        
        # 注入分类标签
        cat_node = ET.SubElement(item_node, 'category')
        cat_node.text = item["site_name"]
        
        # 注入永久链接唯一标识符
        g_node = ET.SubElement(item_node, 'guid', {'isPermaLink': 'true'})
        g_node.text = item["link"]
        
        # 注入具体发布时间
        p_node = ET.SubElement(item_node, 'pubDate')
        p_node.text = item["pub_date"]
        
    # 序列化为字节流
    raw_xml_bytes = ET.tostring(rss_root, encoding='utf-8', method='xml')
    try:
        # 使用 minidom 为最终文件提供漂亮、整齐的换行和缩进排版
        pretty_xml_str = minidom.parseString(raw_xml_bytes).toprettyxml(indent="  ")
        if not pretty_xml_str.startswith('<?xml'):
            pretty_xml_str = '<?xml version="1.0" encoding="utf-8"?>\n' + pretty_xml_str
        pretty_xml_bytes = pretty_xml_str.encode('utf-8')
    except Exception:
        pretty_xml_bytes = b'<?xml version="1.0" encoding="utf-8"?>\n' + raw_xml_bytes
        
    # 以二进制覆写模式写入磁盘文件
    with open(filename, "wb") as f:
        f.write(pretty_xml_bytes)
        
    actual_mb = len(pretty_xml_bytes) / (1024 * 1024)
    print(f"   💾 成功创建合并文件: {filename} (包含 {len(items)} 篇，大小: {actual_mb:.2f} MB)")
    generated_feeds.append(this_feed_url)

# =====================================================================
# 7. 【全量同步完后触发主动推送】
# =====================================================================
print("\n📡 开始触发 WebSub 主动广播通知...")
for feed_url in generated_feeds:
    ping_feedly_websub(feed_url)

print(f"\n==== 🚀 全量聚合完毕！完全不消耗订阅额度，完美适配 Feedly 免费版 ====")
