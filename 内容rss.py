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
# ⚙️ 配置区域：项目真实公网托管链接
# ==========================================
DEPLOYED_BASE_URL = "https://feitoudaerfeitoudaer.github.io"

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

# 【二级联动核心 1】：智能剥离侧边栏，保留最纯净的文本行
def fetch_article_detail(article_url, headers):
    try:
        try:
            res = session.get(article_url, headers=headers, timeout=10, verify=False, proxies=proxies)
        except Exception:
            res = session.get(article_url, headers=headers, timeout=10, verify=False)
            
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 清除所有无关和垃圾标签
        for junk in soup.find_all(['nav', 'footer', 'script', 'style', 'header', 'noscript', 'aside']):
            junk.decompose()
            
        # 清除动态加载、JS 禁用等无用报错文本
        for t in soup.find_all(string=re.compile(r'JavaScript|javascript|禁用|noscript|Enable JS', re.I)):
            if t.parent:
                t.parent.decompose()

        # 寻找正文包裹器
        article_body = (
            soup.find('article') or 
            soup.find('div', class_=re.compile(r'article-content|post-content|story-body|entry-content|report-body')) or
            soup.find('main')
        )
        
        if not article_body:
            best_div = None
            max_p_count = 0
            for div in soup.find_all('div'):
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
            # 严格提取有价值的段落，过滤掉少于 15 字的短句（通常是侧边栏标签）
            paragraphs = [p.get_text(strip=True) for p in article_body.find_all('p') if len(p.get_text(strip=True)) > 15]
            if paragraphs:
                return "\n".join(paragraphs)
            
            # 兜底清理直接文本
            text_lines = [line.strip() for line in article_body.get_text(separator="\n").split("\n") if len(line.strip()) > 20]
            return "\n".join(text_lines)
    except Exception:
        pass
    return ""

# 【二级联动核心 2】：生成极致清爽的列表与右侧居中详情
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
            if any(x in href.lower() for x in ['about', 'contact', 'search', 'privacy', 'terms', 'twitter', 'facebook', 'linkedin', 'careers']):
                continue
                
            link_text = link.get_text(strip=True)
            if len(link_text) < 15 or href in seen_urls: 
                continue
                
            seen_urls.add(href)
            
            full_detail_text = fetch_article_detail(href, headers)
            
            # 💡 优化 1：如果抓取内容为空，绝不显示“无内容”，而是直接用标题兜底，防止列表变形
            if not full_detail_text or len(full_detail_text.strip()) < 10:
                full_detail_text = f"最新报告: {link_text}。详细报告全文已发布，请点击下方链接直接前往智库官网阅读。"
                
            # 💡 优化 2：生成极其干净的纯文本预览，作为中间栏列表的精简摘要
            list_preview = full_detail_text[:120].replace('\n', ' ') + "..."
            
            summary_text = full_detail_text[:2000]
            if len(full_detail_text) > 2000:
                summary_text += "\n\n...(详细报告正文较长，已自动折叠)..."
                
            # 💡 优化 3：右侧详情窗口排版——强制锁定标准中轴线最大 660px 宽度，段落严格隔离，完美配合 Feedly 的右侧滑出面板
            html_content = (
                f"<div style='max-width:660px; margin:0 auto; padding:10px 0; font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,Helvetica,Arial,sans-serif; font-size:16px; line-height:1.75; color:#222;'>"
                f"<p style='margin-bottom:1.5em; text-indent:2em;'>"
                f"{summary_text.replace('\n', '</p><p style=\"margin-bottom:1.5em; text-indent:2em;\">')}"
                f"</p>"
                f"</div>"
            )
            
            title_clean = clean_xml_string(link_text).replace("]]>", "]]&gt;")
            list_clean = clean_xml_string(list_preview).replace("]]>", "]]&gt;")
            html_clean = clean_xml_string(html_content).replace("]]>", "]]&gt;")
            
            item = {
                # 列表标题去掉不必要的括号，保持极简
                "title": f"<![CDATA[{title_clean}]]>",
                "link": html.escape(href),
                # description 对应中间栏的摘要流，只给纯文本
                "description": f"<![CDATA[{list_clean}]]>", 
                # content_encoded 对应右侧点开后的沉浸正文
                "content_encoded": f"<![CDATA[{html_clean}]]>",
                "pub_date": datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
            }
            extracted_items.append(item)
            
            if len(extracted_items) >= 2:
                break
    except Exception:
        pass 
        
    return (country, extracted_items)

# 【主动通知核心】
def ping_feedly_websub(feed_url):
    hub_url = "http://appspot.com"
    data = {
        "hub.mode": "publish",
        "hub.url": feed_url
    }
    try:
        res = requests.post(hub_url, data=data, timeout=5)
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

# 6. 【按国别打包输出标准的规范化 RSS 文件 + 附加 WebSub 标签】（安全纯文本防吞版）
# =====================================================================
print("\n📦 开始按国别（一国一包）打包生成完美适配 Feedly 规范的 RSS 订阅源...")
generated_feeds = []

for country, items in country_feeds.items():
    safe_country_name = "".join([c for c in country if c.isalpha() or c.isdigit() or c=='_']).strip()
    if not safe_country_name:
        safe_country_name = "Other"
        
    current_time = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    
    filename = f"rss_{safe_country_name}.xml"
    this_feed_url = f"{DEPLOYED_BASE_URL.rstrip('/')}/{filename}"
    
    # 使用安全的字符替换方式，防止含有尖括号的标签被传输机制吞掉
    rss_parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org" xmlns:atom="http://w3.org">',
        '  <channel>',
        f'    <title><![CDATA[{safe_country_name}智库最新报告]]></title>',
        f'    <link>{this_feed_url}</link>',
        '    <atom:link href="http://appspot.com" rel="hub" />',
        f'    <atom:link href="{this_feed_url}" rel="self" type="application/rss+xml" />',
        f'    <description><![CDATA[{safe_country_name} 智库具体文章报告详细全文]]></description>',
        '    <language>zh-cn</language>',
        f'    <lastBuildDate>{current_time}</lastBuildDate>'
    ]
    
    for item in items:
        # 通过显式定义标签名称来确保绝对不会丢失任何符号
        t_item_start = '    ' + '<' + 'item' + '>'
        t_title = '      ' + '<' + 'title' + '>' + item["title"] + '<' + '/title' + '>'
        t_link = '      ' + '<' + 'link' + '>' + item["link"] + '<' + '/link' + '>'
        t_desc = '      ' + '<' + 'description' + '>' + item["description"] + '<' + '/description' + '>'
        t_content = '      ' + '<' + 'content:encoded' + '>' + item["content_encoded"] + '<' + '/content:encoded' + '>'
        t_guid = '      ' + '<' + 'guid isPermaLink="true"' + '>' + item["link"] + '<' + '/guid' + '>'
        t_date = '      ' + '<' + 'pubDate' + '>' + item["pub_date"] + '<' + '/pubDate' + '>'
        t_item_end = '    ' + '<' + '/item' + '>'
        
        rss_parts.append(t_item_start)
        rss_parts.append(t_title)
        rss_parts.append(t_link)
        rss_parts.append(t_desc)
        rss_parts.append(t_content)
        rss_parts.append(t_guid)
        rss_parts.append(t_date)
        rss_parts.append(t_item_end)
        
    rss_parts.append('  </channel>')
    rss_parts.append('</rss>')
    
    full_xml_content = "\n".join(rss_parts)
    
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
