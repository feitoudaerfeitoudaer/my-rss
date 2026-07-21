# 【升级版二级核心 1】：引入文本密度与语义去噪，彻底剥离导航栏、侧边栏和禁用JS提示
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

# 【升级版二级核心 2】：过滤无效超链接标题，确保 Feedly 渲染纯净度
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
            # 💡 核心改动：如果该智库属于严格的纯 JavaScript 渲染网站，抓下来是空的，则提供干净的缺省提示
            if not full_detail_text or "javascript" in full_detail_text.lower():
                full_detail_text = "该智库正文采用高级加密或全JavaScript动态渲染。为保证阅读体验，请点击下方链接查看原文。"
                
            summary_text = full_detail_text[:2000]
            if len(full_detail_text) > 2000:
                summary_text += "\n\n...(详细内容较长，已自动折叠)..."
                
            # 使用最纯净的流式段落标签，强制 Feedly 全屏居中自适应弹性排版
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
