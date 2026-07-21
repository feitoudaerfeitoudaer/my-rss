import pandas as pd
import requests
from bs4 import BeautifulSoup
from rfeed import Item, Feed, Guid
import datetime
import urllib3
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# 🌟【精准对齐】：使用你最新修改的 Excel 列名“描述”来进行国家区分
country_column = '描述' 

# 如果表格里对应的国家是空值，默认归类到“其他”
df[country_column] = df[country_column].fillna('其他').astype(str).str.strip()

# 4. 初始化特殊的网络连接器
session = requests.Session()
adapter = CustomSSLAdapter()
session.mount('https://', adapter)
session.mount('http://', adapter)

# 自动配置本地代理端口（在 GitHub 云端会自动切换直连）
proxies = {
    'http': 'http://127.0.0.1:6917',
    'https': 'http://127.0.0.1:6917'
}

# 定义单个网站的抓取任务函数
def fetch_single_site(row):
    site_name = row['网站名称']
    site_url = row['网站链接']
    country = row[country_column] # 获取该网站在“描述”栏里填写的国家
    
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
            
            item = Item(
                title = f"[{site_name}] {title_text}",
                link = site_url,
                description = full_html_content,
                guid = Guid(site_url),
                pubDate = datetime.datetime.now()
            )
            # 返回国家标签和抓取到的文章内容
            return (country, item)
    except Exception as e:
        pass # 抓取失败时直接跳过，不干扰多线程大部队
    return None

# 5. 【多线程高速抓取区域】
print(f"🚀 开始多线程并发抓取，总计 {len(df)} 个智库网站...")

# 创建一个字典，用来对抓取到的文章按国家进行分类整理
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
            print(f"   ✅ [属地:{country}] 成功抓取: {item.title[:20]}...")

# 6. 【按国别分流打包生成多个 XML 文件】
print("\n📦 开始按国别（描述栏）打包分流 RSS 文件...")

for country, items in country_feeds.items():
    # 限制每个国家最多只放最新的 100 条全文（双重保险，将单个 XML 文件体积死死卡在 1MB 以内以兼容 Feedly）
    safe_items = items[:100]
    
    feed = Feed(
        title = f"智库全文订阅-{country}",
        link = f"https://github.io_{country}.xml",
        description = f"自动抓取的 [{country}] 智库文章全文",
        language = "zh-cn",
        lastBuildDate = datetime.datetime.now(),
        items = safe_items
    )
    
    # 清洗国家名称中可能存在的特殊字符，防止文件名非法
    safe_country_name = "".join([c for c in country if c.isalpha() or c.isdigit() or c=='_']).strip()
    if not safe_country_name:
        safe_country_name = "Other"
        
    filename = f"rss_{safe_country_name}.xml"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(feed.rss())
    print(f"   💾 成功生成分流文件: {filename} (包含 {len(safe_items)} 条文章全文)")

print(f"\n==== 🚀 运行结束！已成功按国别分流更新全部 XML 文件 ====")
