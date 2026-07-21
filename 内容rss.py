import pandas as pd
import requests
from bs4 import BeautifulSoup
from rfeed import Item, Feed, Guid
import datetime
import urllib3
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed # 引入高并发线程池

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

# 4. 初始化带有连接池和特殊 SSL 绕过能力的请求会话
session = requests.Session()
adapter = CustomSSLAdapter()
session.mount('https://', adapter)
session.mount('http://', adapter)

# 自动配置本地代理端口
proxies = {
    'http': 'http://127.0.0.1:6917',
    'https': 'http://127.0.0.1:6917'
}

# 核心：定义单个网站的抓取任务函数，供多线程同时调用
def fetch_single_site(row):
    site_name = row['网站名称']
    site_url = row['网站链接']
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://google.com',
        'Connection': 'keep-alive',
    }
    
    try:
        # 高频并发下，超时时间缩短到 8 秒，防止个别死网站卡住整个队列
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
            
            # 返回抓取成功的 RSS Item 对象
            return Item(
                title = f"[{site_name}] {title_text}",
                link = site_url,
                description = full_html_content,
                guid = Guid(site_url),
                pubDate = datetime.datetime.now()
            )
    except Exception as e:
        # 抓取失败时打印，但不会让程序中断
        print(f"   ❌ 抓取网站 {site_name} 失败: {e}")
    return None

# 5. 【多线程核心执行区域】
rss_items = []
print(f"🚀 开始多线程并发抓取，总计 {len(df)} 个智库网站...")

# max_workers=30 代表同时开启 30 个线程（30个网站一起爬）
with ThreadPoolExecutor(max_workers=30) as executor:
    # 建立多线程任务映射
    futures = {executor.submit(fetch_single_site, row): row for _, row in df.iterrows()}
    
    # 谁先爬完谁就先返回结果
    for future in as_completed(futures):
        result = future.result()
        if result:
            rss_items.append(result)
            print(f"   ✅ 成功抓取: {result.title[:25]}...")

# 6. 打包生成符合标准全文规范的 RSS XML 文件
feed = Feed(
    title = "我的智库聚合全文订阅",
    link = "https://github.io", # 已修正为你自己的项目主页
    description = "自动高速抓取的智库文章全文",
    language = "zh-cn",
    lastBuildDate = datetime.datetime.now(),
    items = rss_items
)

# 7. 保存为本地的 rss.xml 文件
with open("rss.xml", "w", encoding="utf-8") as f:
    f.write(feed.rss())

print(f"\n==== 🚀 运行结束！本次并发高效抓取了 {len(rss_items)} 个网站，rss.xml 已更新 ====")
