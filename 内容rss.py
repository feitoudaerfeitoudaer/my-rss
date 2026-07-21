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

# 【二级联动核心 1】：深入文章的真实具体 URL，抓取详细报告全文
def fetch_article_detail(article_url, headers):
    try:
        try:
            res = session.get(article_url, headers=headers, timeout=10, verify=False, proxies=proxies)
        except Exception:
            res = session.get(article_url, headers=headers, timeout=10, verify=False)
            
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 智能化匹配绝大多数智库的正文包裹标签
        article_body = soup.find('article') or soup.find('div', class_=re.compile(r'content|post|article|body|entry|main')) or soup.find('main')
        if article_body:
            # 剔除无用的导航栏、页脚、脚本和样式，确保内容纯净
            for ignore in article_body.find_all(['nav', 'footer', 'script', 'style', 'header']):
                ignore.decompose()
            return article_body.get_text(separator="\n", strip=True)
    except Exception:
        pass
    return None

# 【二级联动核心 2】：单站抓取任务，先从主页提取文章链接列表，再遍历抓取全文
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
        
        # 寻找主页所有超链接
        links = soup.find_all('a', href=True)
        seen_urls = set()
        
        for link in links:
            href = link['href'].strip()
            
            # 补全相对路径链接
            if href.startswith('/'):
                # 兼容处理 // 域名的特殊情况
                if href.startswith('//'):
                    href = 'https:' + href
                else:
                    href = base_url.rstrip('/') + href
            
            # 过滤非本站链接、主页本身以及常见的干扰功能性链接
            if not href.startswith('http') or base_url.replace('www.', '') not in href or href.rstrip('/') == base_url.rstrip('/'):
                continue
            if any(x in href.lower() for x in ['about', 'contact', 'search', 'privacy', 'terms', 'twitter', 'facebook', 'linkedin', 'careers']):
                continue
                
            # 提取链接文本作为标题参考
            link_text = link.get_text(strip=True)
            if len(link_text) < 12 or href in seen_urls: 
                continue
                
            seen_urls.add(href)
            
            # 顺着具体链接深入抓取正文详细内容
            full_detail_text = fetch_article_detail(href, headers)
            if not full_detail_text:
                full_detail_text = "未能自动提取到详细正文，请点击下方链接直接访问智库官网阅读。"
                
            # 截取前 2000 字，保留深度阅读价值，同时防止 XML 超过 Feedly 单文件大小限制
            summary_text = full_detail_text[:2000]
            if len(full_detail_text) > 2000:
                summary_text += "\n\n...(详细报告正文较长，已自动截断，请点击下方链接阅读完整版)..."
                
            # 为 Feedly 专门定制的标准 HTML 纯净渲染区块
            html_content = f"<div><p>{summary_text.replace('\n', '<br>')}</p><br><hr><p><a href='{href}' target='_blank' style='color:#1a73e8;font-weight:bold;text-decoration:none;'>👉 点击这里，阅读该智库官方原文全文</a></p></div>"
            
            title_clean = clean_xml_string(link_text).replace("]]>", "]]&gt;")
            html_clean = clean_xml_string(html_content).replace("]]>", "]]&gt;")
            
            # 单篇具体文章的数据字典
            item = {
                "title": f"<![CDATA[[{site_name}] {title_clean}]]>",
                "link": html.escape(href),
                "description": f"<![CDATA[{title_clean}]]>", 
                "content_encoded": f"<![CDATA[{html_clean}]]>",
                "pub_date": datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
            }
            extracted_items.append(item)
            
            # 【频控限额】：每个智库主页最多只抓取最新 2 篇，防止因单站更新过多导致 Feedly 刷屏
            if len(extracted_items) >= 2:
                break

    except Exception:
        pass 
        
    return (country, extracted_items)

# 5. 【多线程并发抓取控制区域】（已适配列表返回格式）
print(f"🚀 开始二级联动多线程并发抓取，总计 {len(df)} 个智库网站...")
country_feeds = {}

with ThreadPoolExecutor(max_workers=20) as executor:
    futures = {executor.submit(fetch_single_site, row): row for _, row in df.iterrows()}
    
    for future in as_completed(futures):
        result = future.result()
        if result:
            country, items_list = result
            if items_list: # 只有抓取到了具体文章，才塞入归档
                if country not in country_feeds:
                    country_feeds[country] = []
                # 注意：使用 extend 将返回的文章列表拼接到国家分类中
                country_feeds[country].extend(items_list)
                print(f"   ✅ [属地:{country}] 成功抓取该智库最新文章 {len(items_list)} 篇")

# 6. 【按国别打包输出标准的规范化 RSS 文件】
print("\n📦 开始按国别（一国一包）打包生成完美适配 Feedly 规范的 RSS 订阅源...")

for country, items in country_feeds.items():
    safe_country_name = "".join([c for c in country if c.isalpha() or c.isdigit() or c=='_']).strip()
    if not safe_country_name:
        safe_country_name = "Other"
        
    current_time = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    
    rss_parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org">',
        '  <channel>',
        f'    <title><![CDATA[智库深度全文订阅-{safe_country_name}]]></title>',
        f'    <link>https://github.io_{safe_country_name}.xml</link>',
        f'    <description><![CDATA[自动透传的 [{safe_country_name}] 智库具体文章报告详细全文（Feedly专用高能版）]]></description>',
        '    <language>zh-cn</language>',
        f'    <lastBuildDate>{current_time}</lastBuildDate>'
    ]
    
    for item in items:
        rss_parts.append('    <item>')
        rss_parts.append(f'      <title>{item["title"]}</title>')
        rss_parts.append(f'      <link>{item["link"]}</link>')
        rss_parts.append(f'      <description>{item["description"]}</description>')
        rss_parts.append(f'      <content:encoded>{item["content_encoded"]}</content:encoded>')
        # 核心优化：将文章各自真实的真实具体 URL 作为永久不重复的唯一 guid 标识
        rss_parts.append(f'      <guid isPermaLink="true">{item["link"]}</guid>')
        rss_parts.append(f'      <pubDate>{item["pub_date"]}</pubDate>')
        rss_parts.append('    </item>')
        
    rss_parts.append('  </channel>')
    rss_parts.append('</rss>')
    
    full_xml_content = "\n".join(rss_parts)
    
    filename = f"rss_{safe_country_name}.xml"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(full_xml_content)
        
    actual_mb = len(full_xml_content.encode('utf-8')) / (1024 * 1024)
    print(f"   💾 成功同步国家文件: {filename} (包含 {len(items)} 篇详细文章，大小: {actual_mb:.2f} MB)")

print(f"\n==== 🚀 运行结束！二级联动无损输出完毕，请在 Feedly 中刷新查看具体内容标题与详细正文 ====")
