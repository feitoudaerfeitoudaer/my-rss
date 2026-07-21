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

# 辅助函数：彻底清洗 XML 绝对不允许的非法控制字符
def clean_xml_string(v):
    if not v:
        return ""
    # 移除 XML 绝对不允许的控制字符，防止解析崩溃
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', v)

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
            full_html_content = str(content_element) 
            
            # 清洗非法字符
            title_clean = clean_xml_string(title_text)
            content_clean = clean_xml_string(full_html_content)
            
            # 拼接标准的 XML Item 字典（不经过任何三方库转义）
            # 对 title 和 link 进行常规 HTML 转义处理，防止特殊符号破环 XML 结构
            item = {
                "title": html.escape(f"[{site_name}] {title_clean}"),
                "link": html.escape(site_url),
                "description": f"<![CDATA[{content_clean}]]>", # 纯原生 CDATA 防弹衣，拒绝乱码转义
                "pub_date": datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800") # 标准 RFC 822 时间格式
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
            print(f"   ✅ [属地:{country}] 成功抓取: {item['title'][:20]}...")

# 6. 【原生字符串拼接打包，生成绝对纯正的 XML 文件】
print("\n📦 开始按国别（描述栏）打包分流 RSS 文件...")

for country, items in country_feeds.items():
    # 限制最新 100 条
    safe_items = items[:100]
    
    # 清洗国家名称中可能存在的特殊字符或空格
    safe_country_name = "".join([c for c in country if c.isalpha() or c.isdigit() or c=='_']).strip()
    if not safe_country_name:
        safe_country_name = "Other"
        
    current_time = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    
    # 1. 拼接 RSS 头部
    rss_parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0">',
        '  <channel>',
        f'    <title>智库全文订阅-{safe_country_name}</title>',
        f'    <link>https://github.io_{safe_country_name}.xml</link>',
        f'    <description>自动抓取的 [{safe_country_name}] 智库文章全文</description>',
        '    <language>zh-cn</language>',
        f'    <lastBuildDate>{current_time}</lastBuildDate>'
    ]
    
    # 2. 循环拼接每一个文章条目 (Item)
    for item in safe_items:
        rss_parts.append('    <item>')
        rss_parts.append(f'      <title>{item["title"]}</title>')
        rss_parts.append(f'      <link>{item["link"]}</link>')
        rss_parts.append(f'      <description>{item["description"]}</description>')
        rss_parts.append(f'      <guid>{item["link"]}</guid>')
        rss_parts.append(f'      <pubDate>{item["pub_date"]}</pubDate>')
        rss_parts.append('    </item>')
        
    # 3. 闭合 RSS 尾部
    rss_parts.append('  </channel>')
    rss_parts.append('</rss>')
    
    # 4. 组合成完整的 XML 字符串
    full_xml_content = "\n".join(rss_parts)
    
    filename = f"rss_{safe_country_name}.xml"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(full_xml_content)
    print(f"   💾 成功生成防爆分流文件: {filename} (包含 {len(safe_items)} 条文章全文)")

print(f"\n==== 🚀 运行结束！已成功按国别分流更新全部防爆 XML 文件 ====")
