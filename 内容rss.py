import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib3
import ssl
import re

# 1. 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 2. 强制低版本安全协议连接器
class CustomSSLAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1') 
        ctx.check_hostname = False
        kwargs['ssl_context'] = ctx
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

# 3. 初始化网络连接
session = requests.Session()
adapter = CustomSSLAdapter()
session.mount('https://', adapter)
session.mount('http://', adapter)

# 🚨 【核心修改 1】延长代理测试时间，确保代理能被正确识别
PROXIES_CONFIG = None
test_proxies = {'http': 'http://127.0.0.1:6917', 'https': 'http://127.0.0.1:6917'}
try:
    print("🔄 正在深度检测本地代理...")
    # 将 timeout 从 1.5s 放大到 5s，防止偶发性超时误判
    test_res = session.get('https://google.com', proxies=test_proxies, timeout=5, verify=False)
    if test_res.status_code == 200:
        PROXIES_CONFIG = test_proxies
        print("   💡 [网络状态] 检测到本地代理有效，已成功启用 6917 转发加速机制。")
except Exception as e:
    print(f"   💡 [网络状态] 代理测试失败（原因: {e}），已切换为原生直连模式。")

# 4. 读取 Excel 文件
try:
    df = pd.read_excel("penn_library_deduplicated_think_tanks.xlsx") 
    print(f"📊 成功读取 Excel，共发现 {len(df)} 个智库。")
except Exception as e:
    print(f"❌ 读取 Excel 失败: {e}")
    exit()

print("\n🚀 [开始诊断] 正在抽取前 3 个网站进行深度抓取测试...")

# 🚨 【核心修改 2】关闭多线程，只抽样前3个网站，把所有的隐藏错误（Error）全部打印出来
for index, row in df.head(3).iterrows():
    site_name = row.get('网站名称', '未知')
    site_url = row.get('网站链接', '')
    
    print(f"\n--------------------------------------------------")
    print(f"🌐 正在测试第 {index+1} 个网站: [{site_name}]")
    print(f"🔗 目标链接: {site_url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    try:
        # 发起请求
        response = session.get(site_url, headers=headers, timeout=10, verify=False, proxies=PROXIES_CONFIG)
        print(f"   🟢 请求成功！网络状态码 (Status Code): {response.status_code}")
        
        # 解析网页
        response.encoding = response.apparent_encoding 
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 检查是否能提取到核心元素
        title_element = soup.find('h1') or soup.find('h2') or soup.find('title')
        content_element = soup.find('article') or soup.find('div', class_='content') or soup.find('body')
        
        if title_element:
            print(f"   📝 成功提取到标题: {title_element.get_text(strip=True)[:30]}...")
        else:
            print(f"   ⚠️ 警告：无法在网页中定位到任何标题元素 (h1/h2/title)")
            
        if content_element:
            raw_text = content_element.get_text(strip=True)
            print(f"   📄 成功提取到正文文本，字数: {len(raw_text)}")
            if len(raw_text) < 100:
                print(f"   ⚠️ 警告：正文字数过少（仅 {len(raw_text)} 字），可能拿到了空壳网页或错误页。")
        else:
            print(f"   ⚠️ 警告：无法在网页中定位到任何正文内容元素")
            
    except requests.exceptions.Timeout:
        print(f"   ❌ 错误：请求超时 (Timeout)！网站响应太慢或网络不通。")
    except requests.exceptions.ProxyError:
        print(f"   ❌ 错误：代理服务器连接失败 (ProxyError)！请检查 v2ray/clash 等软件是否开启了本地 6917 端口。")
    except requests.exceptions.ConnectionError as ce:
        print(f"   ❌ 错误：连接被拒绝或重置 (ConnectionError)！详细原因: {ce}")
    except Exception as e:
        print(f"   ❌ 💥 发生其他未知错误: {e}")

print(f"\n--------------------------------------------------")
print("🏁 诊断抽样结束。请查看上方打印的红色/黄色警告和错误信息。")
