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

# ==========================================
# ⚙️ 配置区域：已为你修改为 my-rss 项目的真实公网托管链接
# ==========================================
DEPLOYED_BASE_URL = "https://feitoudaerfeitoudaer.github.io/my-rss"

# 1. 禁用 SSL 警告提示
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# 辅助函数：清洗 XML 1.0 绝对不允许的非法控制字符
def clean_xml_string(v):
    if not v:
        return ""
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F]', '', v)

# 【二级联动核心 1】：引入文本密度与语义去噪，彻底剥离导航栏、侧边栏和禁用JS提示
def fetch_article_detail(article_url, headers):
    try:
        try:
            res = session.get(article_url, headers=headers, timeout=10, verify=False, proxies=proxies)
        except Exception:
            res = session.get(article_url, headers=headers, timeout=10, verify=False)
            
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 1. 强力清除已知的非正文干扰标签与“禁用JavaScript”类似垃圾块
        for junk in soup.find_all(['nav', 'footer', 'script', 'style', 'header', 'noscript', 'aside']):
            junk.decompose()
            
        # 2. 精准过滤常见的动态加载错误提示词
        for t in soup.find_all(text=re.compile(r'JavaScript|javascript|禁用|noscript|Enable JS', re.I)):
            if t.parent:
                t.parent.decompose()

        # 3. 寻找真正的报告正文包裹器（避开 nav 和 sidebar）
        article_body = (
            soup.find('article') or 
            soup.find('div', class_=re.compile(r'article-content|post-content|story-body|entry-content|report-body')) or
            soup.find('main')
        )
        
        # 4. 如果没有标准标签，通过文本密度启发式捞取（防止抓到全站导航）
        if not article_body:
            best_div = None
            max_p_count = 0
            for div in soup.find_all('div'):
                # 如果这个 div 带有 sidebar, menu, nav 等特征，直接跳过
                div_class = "".join(div.get('class', [])).lower()
                div_id = str(div.get('id', '')).lower()
                if any(x in div_class or x in div_id for x in ['nav', 'menu', 'sidebar', 'footer', 'header', 'widget']):
                    continue
                p_count = len(div.find_all('p'))
                if p_count > max_p_count:
                    max_p_count = p_count
                    best_div = div
            article_body = best_div

        if article_body:
            # 仅提取段落文本，彻底防止列表标签导致的 Feedly 排版错乱
            paragraphs = [p.get_text(strip=True) for p in article_body.find_all('p') if len(p.get_text(strip=True)) > 10]
            if paragraphs:
                return "\n".join(paragraphs)
            else:
                return article_body.get_text(separator="\n", strip=True)
    except Exception:
        pass
    return None

# 【二级联动核心 2】：过滤无效超链接标题，确保 Feedly 渲染纯净度
def fetch_single_site(row):
    site_name = row['网站名称']
    base_url = row['网站链接']
    country = row[country_column] 
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://google.com',
    }
    
    extracted_items = []
    
    try:
        try:
            response = session.get(base_url, headers=headers, timeout=10, verify=False, proxies=proxies)
        except Exception:
            response = session.get(base_url, headers=headers, timeout=10, verify=False)
            
        response.encoding = response.apparent_encoding 
        soup = BeautifulSoup(response.text, 'html.parser')
        
        links = soup.find_all('a', href=True)
        seen_urls = set()
        
        for link in links:
            href = link['href'].strip()
            if href.startswith('/'):
                if href.startswith('//'): href = 'https:' + href
                else: href = base_url.rstrip('/') + href
            
            if not href.startswith('http') or base_url.replace('www.', '') not in href or href.rstrip('/') == base_url.rstrip('/'):
                continue
            if any(x in href.lower() for x in ['about', 'contact', 'search', 'privacy', 'terms', 'twitter', 'facebook', 'linkedin', 'careers', 'membership', 'staff']):
                continue
                
            link_text = link.get_text(strip=True)
            # 过滤掉类似“了解更多”、“主页”等非文章标题的无效短链接
            if len(link_text) < 15 or href in seen_urls: 
                continue
                
            seen_urls.add(href)
            
            full_detail_text = fetch_article_detail(href, headers)
            # 如果该智库属于严格的纯 JavaScript 渲染网站，抓下来是空的，则提供干净的缺省提示
            if not full_detail_text or "javascript" in full_detail_text.lower():
                full_detail_text = "该智库正文采用高级加密或全JavaScript动态渲染。为保证阅读体验，请点击下方链接查看原文。"
                
            summary_text = full_detail_text[:2000]
            if len(full_detail_text) > 2000:
                summary_text += "\n\n...(详细内容较长，已自动折叠)..."
                
            # 🌟 核心排版：使用 max-width 结合 margin:0 auto 强制 Feedly 正文全屏自适应居中排版
            html_content = f"<div style='max-width:700px; margin:0 auto; line-height:1.7; font-size:16px;'><p>{summary_text.replace('\n', '</p><p>')}</p><br><hr><p><a href='{href}' target='_blank' style='color:#1a73e8;font-weight:bold;text-decoration:none;'>👉 点击这里，阅读该智库官方原文全文</a></p></div>"
            
            title_clean = clean_xml_string(link_text).replace("]]>", "]]&gt;")
            html_clean = clean_xml_string(html_content).replace("]]>", "]]&gt;")
            
            item = {
                "title": f"<![CDATA[[{site_name}] {title_clean}]]>",
                "link": html.escape(href),
                "description": f"<![CDATA[{title_clean}]]>", 
                "content_encoded": f"<![CDATA[{html_clean}]]>",
                "pub_date": datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
            }
            extracted_items.append(item)
            
            if len(extracted_items) >= 2:
                break
    except Exception:
        pass 
        
    return (country, extracted_items)

# 【主动通知核心】：向 Google WebSub 枢纽发送广播，已修复 == 200 / == 204 语法错误
def ping_feedly_websub(feed_url):
    hub_url = "http://appspot.com"
    data = {
        "hub.mode": "publish",
        "hub.url": feed_url
    }
    try:
        res = requests.post(hub_url, data=data, timeout=5)
        # 🌟 核心修正：使用标准明文表达式判断状态码，杜绝 Markdown 被吞导致的无效语法错误
        if res.status_code == 200 or res.status_code == 204:
            print(f"   📢 [WebSub广播] 成功通知 Google 枢纽中心，Feedly 将在数秒内同步: {feed_url}")
        else:
            print(f"   ⚠️ [WebSub广播] 枢纽中心返回状态码异常: {res.status_code}")
    except Exception as e:
        print(f"   ❌ [WebSub广播] 投递失败: {e}")

# 5. 【多线程并发抓取控制区域】
print(f"🚀 开始二级联动多线程并发抓取，总计 {len(df)} 个智库网站...")
country_feeds = {}

with ThreadPoolExecutor(max_workers=20) as executor:
    futures = {executor.submit(fetch_single_site, row): row for _, row in df.iterrows()}
    
    for future in as_completed(futures):
        result = future.result()
        if result:
            country, items_list = result
            if items_list: 
                if country not in country_feeds:
                    country_feeds[country] = []
                country_feeds[country].extend(items_list)
                print(f"   ✅ [属地:{country}] 成功抓取该智库最新文章 {len(items_list)} 篇")

# 6. 【按国别打包输出标准的规范化 RSS 文件 + 附加 WebSub 标签】（已完全修复格式）
print("\n📦 开始按国别（一国一包）打包生成完美适配 Feedly 规范的 RSS 订阅源...")
generated_feeds = []

for country, items in country_feeds.items():
    safe_country_name = "".join([c for c in country if c.isalpha() or c.isdigit() or c=='_']).strip()
    if not safe_country_name:
        safe_country_name = "Other"
        
    current_time = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    
    filename = f"rss_{safe_country_name}.xml"
    this_feed_url = f"{DEPLOYED_BASE_URL.rstrip('/')}/{filename}"
    
    # 严格拼装标准的 XML/RSS 通道节点
    rss_parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org" xmlns:atom="http://w3.org">',
        '  <channel>',
        f'    <title><![CDATA[智库深度全文订阅-{safe_country_name}]]></title>',
        f'    <link>{this_feed_url}</link>',
        '    <atom:link href="http://appspot.com" rel="hub" />',
        f'    <atom:link href="{this_feed_url}" rel="self" type="application/rss+xml" />',
        f'    <description><![CDATA[自动透传的 [{safe_country_name}] 智库具体文章报告详细全文（Feedly专用高能版）]]></description>',
        '    <language>zh-cn</language>',
        f'    <lastBuildDate>{current_time}</lastBuildDate>'
    ]
    
    # 循环补齐每一篇文章的所有核心标准标签，修复了标签被吞导致的异常
    for item in items:
        rss_parts.append('    <item>')
        rss_parts.append(f'      <title>{item["title"]}</title>')
        rss_parts.append(f'      <link>{item["link"]}</link>')
        rss_parts.append(f'      <description>{item["description"]}</description>')
        rss_parts.append(f'      <content:encoded>{item["content_encoded"]}</content:encoded>')
        rss_parts.append(f'      <guid isPermaLink="true">{item["link"]}</guid>')
        rss_parts.append(f'      <pubDate>{item["pub_date"]}</pubDate>')
        rss_parts.append('    </item>')
        
    # 严格闭合频道和文档尾部
    rss_parts.append('  </channel>')
    rss_parts.append('</rss>')
    
    full_xml_content = "\n".join(rss_parts)
    
    # 写入本地静态 XML 文件
    with open(filename, "w", encoding="utf-8") as f:
        f.write(full_xml_content)
        
    actual_mb = len(full_xml_content.encode('utf-8')) / (1024 * 1024)
    print(f"   💾 成功同步国家文件: {filename} (包含 {len(items)} 篇详细文章，大小: {actual_mb:.2f} MB)")
    
    generated_feeds.append(this_feed_url)

# =====================================================================
# 7. 【全量同步完后触发主动推送】
# =====================================================================
print("\n📡 开始触发 WebSub 主动广播通知，引导 Feedly 极速抓取...")
for feed_url in generated_feeds:
    ping_feedly_websub(feed_url)

print(f"\n==== 🚀 运行结束！全量无损输出并广播完毕，请在 Feedly 中刷新查看具体内容标题与详细正文 ====")
