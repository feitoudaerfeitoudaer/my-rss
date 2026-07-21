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

# 【二级联动核心 1】：深入具体报告页，抠出封面图以及最纯净的正文段落
def fetch_article_detail(article_url, headers):
    try:
        try:
            res = session.get(article_url, headers=headers, timeout=10, verify=False, proxies=proxies)
        except Exception:
            res = session.get(article_url, headers=headers, timeout=10, verify=False)
            
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 提取封面图（完美配合 Feedly 的列表左侧配图机制）
        img_url = ""
        main_img = soup.find('meta', property='og:image') or soup.find('meta', name='twitter:image')
        if main_img and main_img.get('content'):
            img_url = main_img['content']
        else:
            # 备选：从正文区域抓第一张大图
            first_img = soup.find('img', src=re.compile(r'uploads|article|wp-content', re.I))
            if first_img and first_img.get('src'):
                img_url = first_img['src']
                if img_url.startswith('/'):
                    img_url = 'https://' + article_url.split('/')[2] + img_url

        # 清除干扰标签
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
                div_class = "".join(div.get('class', [])).lower()
                div_id = str(div.get('id', '')).lower()
                if any(x in div_class or x in div_id for x in ['nav', 'menu', 'sidebar', 'footer', 'header', 'widget']):
                    continue
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

# 【二级联动核心 2】：严格筛选大西洋理事会等站内具体链接，过滤任何干扰项
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
            
            # 【高能过滤】：严防死守。必须包含当前智库的特定核心域名，排除任何跨站外链
            domain_keyword = base_url.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
            if domain_keyword not in href or href.rstrip('/') == base_url.rstrip('/'):
                continue
                
            # 剔除常见的非报告性质的死角链接
            if any(x in href.lower() for x in ['about', 'contact', 'search', 'privacy', 'terms', 'twitter', 'facebook', 'linkedin', 'careers', 'experts', 'events', 'donate']):
                continue
                
            link_text = link.get_text(strip=True)
            # 过滤短文本，确保抓出来的是货真价实的长报告标题
            if len(link_text) < 20 or href in seen_urls: 
                continue
                
            seen_urls.add(href)
            
            # 深入二级联动抓取
            img_url, full_detail_text = fetch_article_detail(href, headers)
            if not full_detail_text:
                continue
                
            # 💡 还原截图核心：在 description 里面塞入精简图文样式，Feedly 列表视图会完美提取图片到左侧
            if img_url:
                list_description = f"<img src='{img_url}' style='float:left; margin-right:10px; width:120px; height:80px; object-fit:cover;' />网站专栏报告。作者: 智库研究员。 这篇智库文章初次发表在{site_name}官方网站上。"
            else:
                list_description = f"网站专栏报告。作者: 智库研究员。 这篇智库文章初次发表在{site_name}官方网站上。"
            
            summary_text = full_detail_text[:2000]
            if len(full_detail_text) > 2000:
                summary_text += "\n\n...(详细内容较长，已自动折叠)..."
                
            # 右侧沉浸式详情面板排版
            html_content = (
                f"<div style='max-width:660px; margin:0 auto; font-family:-apple-system,BlinkMacSystemFont,sans-serif; font-size:16px; line-height:1.8; color:#333;'>"
                f"<h2>{link_text}</h2>"
                f"<p style='color:#666; font-size:14px;'>发布源: {site_name} | 抓取时间: {datetime.datetime.now().strftime('%Y-%m-%d')}</p><hr/>"
                f"<p style='margin-bottom:1.5em; text-indent:2em;'>"
                f"{summary_text.replace('\n', '</p><p style=\"margin-bottom:1.5em; text-indent:2em;\">')}"
                f"</p>"
                f"<br/><p><a href='{href}' target='_blank' style='color:#1a73e8;font-weight:bold;text-decoration:none;'>👉 点击这里，阅读该智库官方原文全文</a></p>"
                f"</div>"
            )
            
            title_clean = clean_xml_string(link_text).replace("]]>", "]]&gt;")
            list_clean = clean_xml_string(list_description).replace("]]>", "]]&gt;")
            html_clean = clean_xml_string(html_content).replace("]]>", "]]&gt;")
            
            item = {
                "title": f"<![CDATA[{title_clean}]]>",
                "link": html.escape(href),
                "description": f"<![CDATA[{list_clean}]]>", 
                "content_encoded": f"<![CDATA[{html_clean}]]>",
                "pub_date": datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
            }
            extracted_items.append(item)
            
            # 严格控制单站只保留最新 3 篇，严防信息轰炸和列表变形
            if len(extracted_items) >= 3:
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

# =====================================================================
# 6. 【按国别打包输出标准的规范化 RSS 文件 + 附加 WebSub 标签】（终极防吞版）
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
    
    # 🌟 用纯字符串拼接初始化头部，彻底解决标签变空白的问题
    rss_parts = []
    rss_parts.append('?' + 'xml version="1.0" encoding="utf-8"?')
    # 给第一行补上两边的尖括号
    rss_parts[0] = '<' + rss_parts[0] + '>'
    
    rss_parts.append('<' + 'rss version="2.0" xmlns:content="http://purl.org" xmlns:atom="http://w3.org"' + '>')
    rss_parts.append('  <' + 'channel' + '>')
    rss_parts.append('    <' + 'title' + '><!' + f'[CDATA[{safe_country_name}智库最新报告]]' + '><' + '/title' + '>')
    rss_parts.append('    <' + 'link' + '>' + this_feed_url + '<' + '/link' + '>')
    rss_parts.append('    <' + 'atom:link href="http://appspot.com" rel="hub" /' + '>')
    rss_parts.append('    <' + 'atom:link href="' + this_feed_url + '" rel="self" type="application/rss+xml" /' + '>')
    rss_parts.append('    <' + 'description' + '><!' + f'[CDATA[{safe_country_name} 智库具体文章报告详细全文]]' + '><' + '/description' + '>')
    rss_parts.append('    <' + 'language' + '>zh-cn<' + '/language' + '>')
    rss_parts.append('    <' + 'lastBuildDate' + '>' + current_time + '<' + '/lastBuildDate' + '>')
    
    # 遍历补齐每一篇文章
    for item in items:
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
        
    # 🌟 闭合标签也用纯字符串拼接，绝对防吞
    rss_parts.append('  <' + '/channel' + '>')
    rss_parts.append('<' + '/rss' + '>')
    
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
