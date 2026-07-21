import pandas as pd
import requests
from bs4 import BeautifulSoup
from rfeed import Item, Feed, Guid
import datetime
import urllib3
import ssl

# 1. 禁用 SSL 警告提示
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 2. 强行创建一个允许低版本安全协议的连接器（解决国外智库断开连接问题）
class CustomSSLAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1') # 降低安全等级以兼容旧版或特殊配置的智库服务器
        ctx.check_hostname = False
        kwargs['ssl_context'] = ctx
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

# 3. 读取 Excel 文件
df = pd.read_excel("penn_library_deduplicated_think_tanks.xlsx") 

rss_items = []

# 4. 创建带特殊 SSL 绕过能力的请求会话
session = requests.Session()
session.mount('https://', CustomSSLAdapter())

# 【核心配置】自动配置你本地的代理端口（10808）
# 这样抓取国外智库（如卡内基）时才不会因为网络卡住而超时
proxies = {
    'http': 'http://127.0.0.1:6917',
    'https': 'http://127.0.0.1:6917'
}

# 5. 循环遍历每一个智库网站
for index, row in df.iterrows():
    site_name = row['网站名称']
    site_url = row['网站链接']
    
    print(f"正在尝试抓取: {site_name}...")
    
    try:
        # 高级浏览器伪装：加入常见的来源页和接收类型，防止被强行断开(EOF)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://google.com', # 伪装成从谷歌搜索跳转而来
            'Connection': 'keep-alive',
        }
        
        # 优先使用代理进行请求，如果代理崩溃则尝试直连
        try:
            response = session.get(site_url, headers=headers, timeout=12, verify=False, proxies=proxies)
        except Exception:
            # 如果代理连接失败（比如你关了翻墙软件），切换为直连重试
            response = session.get(site_url, headers=headers, timeout=12, verify=False)
            
        response.encoding = response.apparent_encoding 
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 寻找文章标题和内容
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
            rss_items.append(item)
            print(f"   ✅ 成功抓取: {title_text[:15]}...")
            
    except Exception as e:
        print(f"   ❌ 抓取网站 {site_name} 失败，错误原因: {e}")

# 6. 打包生成符合标准全文规范的 RSS XML 文件
feed = Feed(
    title = "我的智库聚合全文订阅",
    link = "https://github.io",
    description = "自动抓取的智库文章全文",
    language = "zh-cn",
    lastBuildDate = datetime.datetime.now(),
    items = rss_items
)

# 7. 保存为本地的 rss.xml 文件
with open("rss.xml", "w", encoding="utf-8") as f:
    f.write(feed.rss())

print(f"\n==== 运行结束！本次成功抓取了 {len(rss_items)} 个网站，rss.xml 已更新 ====")
