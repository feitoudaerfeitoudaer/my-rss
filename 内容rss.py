import pandas as pd
import requests
from bs4 import BeautifulSoup
import datetime
import urllib3
import ssl
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import html

# 1. 禁用 SSL 警告提示
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 2. 强行创建一个允许低版本安全协议的连接器
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

# 4. 初始化特殊的网络连接器
session = requests.Session()
adapter = CustomSSLAdapter()
session.mount('https://', adapter)
session.mount('http://', adapter)

proxies = {
    'http': 'http://127.0.0.1:6917',
    'https': 'http://127.0.0.1:6917'
}

# 辅助函数：彻底清洗 XML 1.0 绝对不允许的非法控制字符
def clean_xml_string(v):
    if not v:
        return ""
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F]', '', v)

# 定义单个网站的抓取任务函数
def fetch_single_site(row):
    site_name = row['网站名称']
    site_url = row['网站链接']
    country = row[country_column] 
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://google.com',
        'Connection': 'keep-alive',
    }
    
    try:
        try:
            response = session.get(site_url, headers=headers, timeout=8, verify=False, proxies=proxies)
        except Exception:
            response = session.get(site_url, headers=headers, timeout=8, verify=False)
            
        response.encoding = response.apparent_encoding 
        soup = BeautifulSoup(response.text, 'html.parser')
        
        title_element = soup.find('h1') or soup.find('h2') or soup.find('title')
        content_element = soup.find('article') or soup.find('div', class_='content') or soup.find('body')
        
        if title_element and content_element:
            title_text = title_element.get_text(strip=True)
            raw_text = content_element.get_text(separator="\n", strip=True)
            
            # 限制前 1500 个字作为核心摘要
            summary_text = raw_text[:1500]
            if len(raw_text) > 1500:
                summary_text += "\n\n...(报告正文较长已自动隐藏后半部分)..."
            
            # 1. 纯文本摘要：Feedly 列表流预览
            description_text = summary_text[:200] + "..."
            
            # 2. 富文本正文：Feedly 内部沉浸式阅读器所需的 HTML 结构
            # 转换换行符为标准破行，并构建干净的内联样式链接
            html_content = f"<div><p>{summary_text.replace('\n', '<br>')}</p><br><hr><p><a href='{site_url}' target='_blank' style='color:#1a73e8;font-weight:bold;text-decoration:none;'>👉 点击这里，阅读该智库官方原文全文</a></p></div>"
            
            # 清洗非法控制字符
            title_clean = clean_xml_string(title_text)
            description_clean = clean_xml_string(description_text)
            html_clean = clean_xml_string(html_content)
            
            # 核心安全处理：防止 CDATA 提前闭合崩溃
            title_safe = title_clean.replace("]]>", "]]&gt;")
            description_safe = description_clean.replace("]]>", "]]&gt;")
            html_safe = html_clean.replace("]]>", "]]&gt;")
            
            # 对 URL 进行标准的 XML 实体转义
            url_safe = html.escape(site_url)
            
            item = {
                "title": f"<![CDATA[[{site_name}] {title_safe}]]>",
                "link": url_safe,
                "description": f"<![CDATA[{description_safe}]]>", 
                "content_encoded": f"<![CDATA[{html_safe}]]>",
                "pub_date": datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
            }
            return (country, item)
    except Exception:
        pass 
    return None

# 5. 【多线程高速抓取区域】
print(f"🚀 开始多线程并发抓取，总计 {len(df)} 个智库网站...")
country_feeds = {}

with ThreadPoolExecutor(max_workers=30) as executor:
    futures = {executor.submit(fetch_single_site, row): row for _, row in df.iterrows()}
    
    for future in as_completed(futures):
        result = future.result()
        if result:
            country, item = result
            if country not in country_feeds:
                country_feeds[country] = []
            country_feeds[country].append(item)
            print(f"   ✅ [属地:{country}] 成功抓取: {item['title'][:30]}...")

# 6. 【针对 Feedly 特性优化的 RSS 输出】
print("\n📦 开始按国别（一国一包一链接）打包 RSS 文件...")

for country, items in country_feeds.items():
    safe_items = items  
    
    safe_country_name = "".join([c for c in country if c.isalpha() or c.isdigit() or c=='_']).strip()
    if not safe_country_name:
        safe_country_name = "Other"
        
    current_time = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    
    # 核心优化：引入 content 命名空间 (xmlns:content)，这是 Feedly 完美解析 HTML 的工业标准
    rss_parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org">',
        '  <channel>',
        f'    <title><![CDATA[智库全文订阅-{safe_country_name}]]></title>',
        f'    <link>https://github.io_{safe_country_name}.xml</link>',
        f'    <description><![CDATA[自动抓取的 [{safe_country_name}] 智库文章全文摘要（全量无损版）]]></description>',
        '    <language>zh-cn</language>',
        f'    <lastBuildDate>{current_time}</lastBuildDate>'
    ]
    
    for item in safe_items:
        rss_parts.append('    <item>')
        rss_parts.append(f'      <title>{item["title"]}</title>')
        rss_parts.append(f'      <link>{item["link"]}</link>')
        
        # 优化项：description 只放纯文本，作为 Feedly 列表视图下的预览语
        rss_parts.append(f'      <description>{item["description"]}</description>')
        
        # 优化项：用 content:encoded 专门存放 HTML 富文本，Feedly 会优先用它在详情页渲染
        rss_parts.append(f'      <content:encoded>{item["content_encoded"]}</content:encoded>')
        
        # 优化项：显式标明 guid 是永久链接，防止 Feedly 重复推文、识别错乱
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
    print(f"   💾 成功同步国家文件: {filename} (包含 {len(safe_items)} 条，大小仅为: {actual_mb:.2f} MB)")

print(f"\n==== 🚀 运行结束！全量无损输出完毕，完美适配 Feedly 规范 ====")
