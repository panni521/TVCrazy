import re
import csv
import requests
import asyncio
import aiohttp
import os
import argparse
import time
import random
from urllib.parse import urlparse
import logging
from concurrent.futures import ThreadPoolExecutor

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("iptv_probe.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 扩展的频道分类映射
CHANNEL_CATEGORIES = {
    'CCTV': {'name': '央视频道', 'region': '中国大陆', 'language': '中文'},
    '卫视': {'name': '卫视频道', 'region': '中国大陆', 'language': '中文'},
    '影视': {'name': '影视娱乐', 'region': '', 'language': ''},
    '综艺': {'name': '综艺娱乐', 'region': '', 'language': ''},
    '体育': {'name': '体育赛事', 'region': '', 'language': ''},
    '新闻': {'name': '新闻资讯', 'region': '', 'language': ''},
    '少儿': {'name': '少儿动画', 'region': '', 'language': ''},
    '纪录': {'name': '纪录片', 'region': '', 'language': ''},
    '音乐': {'name': '音乐频道', 'region': '', 'language': ''},
    '财经': {'name': '财经频道', 'region': '', 'language': ''},
    '教育': {'name': '教育频道', 'region': '', 'language': ''},
    '戏曲': {'name': '戏曲频道', 'region': '中国大陆', 'language': '中文'},
    '国际': {'name': '国际频道', 'region': '', 'language': ''},
    '测试': {'name': '测试频道', 'region': '', 'language': ''}
}

# 地区映射表
REGION_MAPPING = {
    'CN': '中国大陆', 'TW': '中国台湾', 'HK': '中国香港', 'MO': '中国澳门',
    'US': '美国', 'UK': '英国', 'JP': '日本', 'KR': '韩国',
    'SG': '新加坡', 'DE': '德国', 'FR': '法国', 'IT': '意大利',
    'ES': '西班牙', 'RU': '俄罗斯', 'IN': '印度', 'TH': '泰国',
    'AU': '澳大利亚', 'CA': '加拿大'
}

# 语言映射表
LANGUAGE_MAPPING = {
    'CN': '中文', 'EN': '英语', 'JP': '日语', 'KR': '韩语',
    'FR': '法语', 'DE': '德语', 'ES': '西班牙语', 'IT': '意大利语',
    'RU': '俄语', 'AR': '阿拉伯语', 'TH': '泰语'
}

# 归一化频道名称
def channel_name_normalize(name):
    for rep in ["高清", "超高", "HD", "标清", "频道", "-", " ", "PLUS", "＋", "(", ")"]:
        name = name.replace(rep, "" if rep not in ["PLUS", "＋"] else "+")
    name = re.sub(r"CCTV(\d+)台", r"CCTV\1", name)
    name_map = {
        "CCTV1综合": "CCTV1", "CCTV2财经": "CCTV2", "CCTV3综艺": "CCTV3",
        "CCTV4国际": "CCTV4", "CCTV4中文国际": "CCTV4", "CCTV4欧洲": "CCTV4",
        "CCTV5体育": "CCTV5", "CCTV6电影": "CCTV6", "CCTV7军事": "CCTV7",
        "CCTV7军农": "CCTV7", "CCTV7农业": "CCTV7", "CCTV7国防军事": "CCTV7",
        "CCTV8电视剧": "CCTV8", "CCTV9记录": "CCTV9", "CCTV9纪录": "CCTV9",
        "CCTV10科教": "CCTV10", "CCTV11戏曲": "CCTV11", "CCTV12社会与法": "CCTV12",
        "CCTV13新闻": "CCTV13", "CCTV新闻": "CCTV13", "CCTV14少儿": "CCTV14",
        "CCTV15音乐": "CCTV15", "CCTV16奥林匹克": "CCTV16",
        "CCTV17农业农村": "CCTV17", "CCTV17农业": "CCTV17",
        "CCTV5+体育赛视": "CCTV5+", "CCTV5+体育赛事": "CCTV5+", "CCTV5+体育": "CCTV5+"
    }
    name = name_map.get(name, name)
    return name

# 从频道名称提取元数据
def extract_channel_metadata(channel_name):
    metadata = {
        'region': '',
        'language': '',
        'category': '其他频道',
        'keywords': []
    }
    
    # 提取地区信息
    for code, region in REGION_MAPPING.items():
        if code in channel_name or region in channel_name:
            metadata['region'] = region
            metadata['keywords'].append(region)
            break
    
    # 提取语言信息
    for code, language in LANGUAGE_MAPPING.items():
        if code in channel_name or language in channel_name:
            metadata['language'] = language
            metadata['keywords'].append(language)
            break
    
    # 提取分类信息
    for keyword, category_info in CHANNEL_CATEGORIES.items():
        if keyword in channel_name:
            metadata['category'] = category_info['name']
            if not metadata['region'] and category_info.get('region'):
                metadata['region'] = category_info['region']
            if not metadata['language'] and category_info.get('language'):
                metadata['language'] = category_info['language']
            break
    
    return metadata

# 确定频道类别
def get_channel_category(channel_name):
    metadata = extract_channel_metadata(channel_name)
    return metadata['category']

# 获取频道地区
def get_channel_region(channel_name):
    metadata = extract_channel_metadata(channel_name)
    return metadata['region'] or '未知地区'

# 获取频道语言
def get_channel_language(channel_name):
    metadata = extract_channel_metadata(channel_name)
    return metadata['language'] or '未知语言'

# 生成同一C段的所有IP的URL
def generate_ip_range_urls(base_url, ip_address, port, suffix=None):
    try:
        ip_parts = ip_address.split('.')
        if len(ip_parts) < 3:
            return []
        c_prefix = '.'.join(ip_parts[:3])
        return [f"{base_url}{c_prefix}.{i}{port}{suffix if suffix else ''}" for i in range(1, 256)]
    except Exception as e:
        logger.error(f"生成IP范围失败: {e}")
        return []

# 获取代理列表
def get_proxy_list(proxy_file=None):
    if not proxy_file or not os.path.exists(proxy_file):
        return []
    
    try:
        with open(proxy_file, 'r') as f:
            proxies = [line.strip() for line in f if line.strip()]
        
        return [{"http": proxy, "https": proxy} for proxy in proxies]
    except Exception as e:
        logger.error(f"加载代理列表失败: {e}")
        return []

# 深度检查URL是否为可用的IPTV源
async def is_valid_iptv_source(url, retries=3, proxies=None, session=None):
    """
    深度检查URL是否为可用的IPTV源
    1. 检查HTTP状态码
    2. 验证M3U8文件格式
    3. 检查TS片段可用性
    """
    proxy_list = proxies or []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    for attempt in range(retries):
        try:
            proxy = random.choice(proxy_list + [None]) if proxy_list else None
            
            # 使用传入的session或创建临时session
            if session:
                async with session.get(url, timeout=8, headers=headers, proxy=proxy) as response:
                    if response.status != 200:
                        continue
                    
                    content = await response.text()
                    if not content.startswith('#EXTM3U'):
                        logger.debug(f"{url} 不是有效的M3U8文件")
                        continue
                    
                    lines = content.strip().split('\n')
                    ts_files = [line for line in lines if not line.startswith('#') and line.endswith(('.ts', '.m3u8'))]
                    if not ts_files:
                        logger.debug(f"{url} 不包含有效的媒体片段")
                        continue
                    
                    # 测试TS文件
                    base_url = url.rsplit('/', 1)[0] + '/'
                    test_ts_url = ts_files[0]
                    if not test_ts_url.startswith(('http://', 'https://')):
                        test_ts_url = base_url + test_ts_url
                    
                    async with session.get(test_ts_url, timeout=10, headers=headers, proxy=proxy) as ts_response:
                        if ts_response.status == 200 and await ts_response.content.read(1024):
                            return url
            else:
                # 同步模式，仅在没有session时使用
                with requests.get(url, timeout=8, headers=headers, proxies=proxy) as response:
                    if response.status_code != 200:
                        continue
                    
                    if not response.text.startswith('#EXTM3U'):
                        logger.debug(f"{url} 不是有效的M3U8文件")
                        continue
                    
                    lines = response.text.strip().split('\n')
                    ts_files = [line for line in lines if not line.startswith('#') and line.endswith(('.ts', '.m3u8'))]
                    if not ts_files:
                        logger.debug(f"{url} 不包含有效的媒体片段")
                        continue
                    
                    # 测试TS文件
                    base_url = url.rsplit('/', 1)[0] + '/'
                    test_ts_url = ts_files[0]
                    if not test_ts_url.startswith(('http://', 'https://')):
                        test_ts_url = base_url + test_ts_url
                    
                    with requests.get(test_ts_url, timeout=10, headers=headers, proxies=proxy) as ts_response:
                        if ts_response.status_code == 200 and len(ts_response.content) > 1024:
                            return url
        except Exception as e:
            logger.debug(f"尝试访问 {url} 失败 (尝试 {attempt+1}/{retries}): {e}")
            continue
    
    return None

# 异步检测URL可用性
async def check_urls_async(urls, proxies=None, concurrency=100):
    valid_urls = []
    semaphore = asyncio.Semaphore(concurrency)
    
    async def check_single_url(url):
        async with semaphore:
            async with aiohttp.ClientSession() as session:
                result = await is_valid_iptv_source(url, retries=3, proxies=proxies, session=session)
                if result:
                    logger.info(f"有效URL: {result}")
                    return result
                return None
    
    tasks = [check_single_url(url) for url in urls]
    results = await asyncio.gather(*tasks)
    return [result for result in results if result]

# jsmpeg模式获取频道
async def get_channels_alltv(csv_file, proxies=None):
    urls = set()
    try:
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            if 'host' not in reader.fieldnames:
                raise ValueError('CSV文件缺少host列')
            for row in reader:
                host = row['host'].strip()
                if host:
                    urls.add(host if host.startswith(('http://', 'https://')) else f"http://{host}")
        
        ip_range_urls = []
        for url in urls:
            try:
                parsed = urlparse(url)
                ip_address = parsed.hostname
                port = f":{parsed.port}" if parsed.port else ""
                base_url = f"{parsed.scheme}://"
                ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port))
            except Exception as e:
                logger.error(f"处理URL {url} 时出错: {e}")
                continue
        
        valid_urls = await check_urls_async(set(ip_range_urls), proxies=proxies)
        channels = []
        
        async def fetch_channel_data(url):
            try:
                async with aiohttp.ClientSession() as session:
                    json_url = f"{url.rstrip('/')}/streamer/list"
                    async with session.get(json_url, timeout=5) as response:
                        json_data = await response.json()
                        host = url.rstrip('/')
                        return [(channel_name_normalize(item.get('name', '').strip()), 
                                f"{host}/hls/{item.get('key', '').strip()}/index.m3u8") 
                                for item in json_data if item.get('name') and item.get('key')]
            except Exception as e:
                logger.error(f"获取频道列表 from {url} 失败: {e}")
                return []
        
        # 并发获取所有有效URL的频道数据
        async with asyncio.Semaphore(50):
            tasks = [fetch_channel_data(url) for url in valid_urls]
            results = await asyncio.gather(*tasks)
        
        for sublist in results:
            channels.extend(sublist)
        
        return channels
    except Exception as e:
        logger.error(f"处理jsmpeg模式CSV文件失败: {e}")
        return []

# txiptv模式获取频道
async def get_channels_newnew(csv_file, proxies=None):
    try:
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            urls = list(set(row.get('link', '').strip() for row in reader if row.get('link')))
        
        async def modify_urls(url):
            try:
                parsed = urlparse(url)
                ip_address = parsed.hostname
                port = f":{parsed.port}" if parsed.port else ""
                base_url = f"{parsed.scheme}://"
                ip_parts = ip_address.split('.')
                if len(ip_parts) < 3:
                    return []
                c_prefix = '.'.join(ip_parts[:3])
                return [f"{base_url}{c_prefix}.{i}{port}/iptv/live/1000.json?key=txiptv" for i in range(1, 256)]
            except:
                return []
        
        async def check_url(session, url, semaphore):
            async with semaphore:
                try:
                    proxy = random.choice(proxies + [None]) if proxies else None
                    async with session.get(url, timeout=5, proxy=proxy) as response:
                        return url if response.status == 200 else None
                except Exception as e:
                    logger.debug(f"尝试访问 {url} 失败: {e}")
                    return None
        
        async def fetch_channels(session, url, semaphore):
            async with semaphore:
                try:
                    parsed = urlparse(url)
                    base_url = f"{parsed.scheme}://{parsed.hostname}"
                    async with session.get(url, timeout=5) as response:
                        json_data = await response.json()
                        channels = []
                        for item in json_data.get('data', []):
                            if isinstance(item, dict):
                                name = item.get('name')
                                urlx = item.get('url')
                                if not urlx or ',' in urlx:
                                    continue
                                urld = urlx if 'http' in urlx else f"{base_url}{urlx}"
                                channels.append((channel_name_normalize(name), urld))
                        return channels
                except Exception as e:
                    logger.error(f"获取JSON数据 from {url} 失败: {e}")
                    return []
        
        # 生成所有可能的URL
        all_urls = []
        for url in urls:
            modified = await modify_urls(url)
            all_urls.extend(modified)
        
        # 检查URL有效性
        async with aiohttp.ClientSession() as session:
            semaphore = asyncio.Semaphore(500)
            tasks = [check_url(session, url, semaphore) for url in all_urls]
            valid_urls = [url for url in await asyncio.gather(*tasks) if url]
        
        # 获取频道数据
        tasks = [fetch_channels(aiohttp.ClientSession(), url, semaphore) for url in valid_urls]
        results = await asyncio.gather(*tasks)
        
        return [channel for sublist in results for channel in sublist]
    except Exception as e:
        logger.error(f"处理txiptv模式CSV文件失败: {e}")
        return []

# zhgxtv模式获取频道
async def get_channels_hgxtv(csv_file, proxies=None):
    urls = set()
    try:
        with open(csv_file, 'r', encoding='utf-8-sig') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                host = row['host'].strip()
                if host:
                    url = host if host.startswith(('http://', 'https://')) else f"http://{host}{':80' if ':' not in host else ''}"
                    urls.add(url)
        
        ip_range_urls = []
        for url in urls:
            try:
                parsed = urlparse(url)
                ip_address = parsed.hostname
                port = f":{parsed.port}" if parsed.port else ""
                base_url = f"{parsed.scheme}://"
                ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port, "/ZHGXTV/Public/json/live_interface.txt"))
            except:
                logger.error(f"处理URL {url} 时出错")
                continue
        
        valid_urls = await check_urls_async(set(ip_range_urls), proxies=proxies)
        channels = []
        
        async def fetch_channels(url):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=5) as response:
                        content = await response.text()
                        channel_list = []
                        for line in content.split('\n'):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                name, channel_url = line.split(',')
                                parsed_url = urlparse(url)
                                url_parts = channel_url.split('/', 3)
                                if len(url_parts) >= 4:
                                    urld = f"{url_parts[0]}//{parsed_url.hostname}/{url_parts[3]}"
                                else:
                                    urld = f"{url_parts[0]}//{parsed_url.hostname}"
                                channel_list.append((channel_name_normalize(name), urld))
                            except:
                                continue
                        return channel_list
            except Exception as e:
                logger.error(f"获取频道列表 from {url} 失败: {e}")
                return []
        
        # 并发获取所有有效URL的频道数据
        async with asyncio.Semaphore(50):
            tasks = [fetch_channels(url) for url in valid_urls]
            results = await asyncio.gather(*tasks)
        
        for sublist in results:
            channels.extend(sublist)
        
        return channels
    except Exception as e:
        logger.error(f"处理zhgxtv模式CSV文件失败: {e}")
        return []

# 多维度排序函数
def sort_channels(channels, sort_rules=None):
    """
    基于多种规则对频道进行排序
    sort_rules: 排序规则列表，例如 [('region', 'asc'), ('quality', 'desc')]
    """
    if not sort_rules:
        # 默认排序规则
        sort_rules = [
            ('category', 'asc'),  # 按分类升序
            ('region', 'asc'),   # 按地区升序
            ('language', 'asc'), # 按语言升序
            ('quality', 'desc')  # 按质量降序
        ]
    
    # 定义排序键函数
    def sort_key(channel):
        key_values = []
        for field, direction in sort_rules:
            value = channel[field]
            
            # 处理特殊字段
            if field == 'quality':
                value = float(value)
            elif field == 'category':
                # 确保分类按预定义顺序排列
                category_order = list(CHANNEL_CATEGORIES.values())
                value = next((i for i, cat in enumerate(category_order) if cat['name'] == value), len(category_order))
            
            # 处理排序方向
            key_values.append(value if direction == 'asc' else -value)
        
        return tuple(key_values)
    
    return sorted(channels, key=sort_key)

# 按用户定义规则排序
def apply_custom_sort(channels, sort_config_file):
    """
    从配置文件加载自定义排序规则并应用
    """
    if not sort_config_file or not os.path.exists(sort_config_file):
        return channels
    
    try:
        with open(sort_config_file, 'r') as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
        sort_rules = []
        for line in lines:
            parts = line.split(',')
            if len(parts) == 2:
                field, direction = parts
                if direction.lower() in ('asc', 'desc'):
                    sort_rules.append((field.strip(), direction.strip().lower()))
        
        if sort_rules:
            return sort_channels(channels, sort_rules)
        
    except Exception as e:
        logger.error(f"应用自定义排序规则失败: {e}")
    
    return channels

# 测试频道速度并输出结果
async def test_speed_and_output(channels, output_prefix="itvlist", proxy_file=None, sort_config=None):
    proxy_list = get_proxy_list(proxy_file) if proxy_file else []
    channel_results = []
    error_channels = []
    
    async def test_channel(channel_name, channel_url):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        proxy = random.choice(proxy_list + [None]) if proxy_list else None
        
        try:
            # 测试M3U8文件获取
            async with aiohttp.ClientSession() as session:
                start_time = time.time()
                async with session.get(channel_url, timeout=8, headers=headers, proxy=proxy) as response:
                    if response.status != 200:
                        raise Exception(f"获取M3U8失败，状态码: {response.status}")
                    
                    m3u8_time = time.time() - start_time
                    content = await response.text()
                    lines = content.strip().split('\n')
                    ts_lists = [line for line in lines if not line.startswith('#') and line.endswith('.ts')]
                    
                    if not ts_lists:
                        raise Exception("未找到有效的TS文件")
                    
                    # 测试TS文件获取
                    base_url = channel_url.rsplit('/', 1)[0] + '/'
                    ts_url = base_url + ts_lists[0].split('/')[-1]
                    
                    start_time = time.time()
                    async with session.get(ts_url, timeout=10, headers=headers, proxy=proxy) as ts_response:
                        if ts_response.status != 200:
                            raise Exception(f"获取TS文件失败，状态码: {ts_response.status}")
                        
                        ts_time = time.time() - start_time
                        file_size = len(await ts_response.read())
                        download_speed = file_size / ts_time / 1024 / 1024  # MB/s
                        
                        # 计算频道质量评分
                        quality_score = min(max(download_speed * 10, 0.1), 100)  # 归一化到0.1-100
                        
                        channel_results.append({
                            'name': channel_name,
                            'url': channel_url,
                            'speed': f"{download_speed:.3f} MB/s",
                            'm3u8_time': f"{m3u8_time:.3f} s",
                            'ts_time': f"{ts_time:.3f} s",
                            'quality': f"{quality_score:.2f}",
                            'category': get_channel_category(channel_name),
                            'region': get_channel_region(channel_name),
                            'language': get_channel_language(channel_name)
                        })
                        
                        logger.info(f"测试成功: {channel_name} - {download_speed:.3f} MB/s")
        except Exception as e:
            error_channels.append((channel_name, channel_url, str(e)))
            logger.debug(f"测试失败: {channel_name} - {str(e)}")
    
    # 并发测试所有频道
    concurrency = min(200, len(channels))
    semaphore = asyncio.Semaphore(concurrency)
    
    async def bounded_test(channel):
        async with semaphore:
            await test_channel(*channel)
    
    tasks = [bounded_test(channel) for channel in channels]
    await asyncio.gather(*tasks)
    
    # 按频道分组并按质量排序
    from collections import defaultdict
    channel_groups = defaultdict(list)
    
    for result in channel_results:
        channel_groups[result['name']].append(result)
    
    # 为每个频道选择最佳的N个源
    optimized_results = []
    for channel_name, sources in channel_groups.items():
        sorted_sources = sorted(sources, key=lambda x: float(x['quality']), reverse=True)[:8]
        optimized_results.extend(sorted_sources)
    
    # 应用自定义排序规则
    if sort_config:
        optimized_results = apply_custom_sort(optimized_results, sort_config)
    else:
        # 默认排序
        optimized_results = sort_channels(optimized_results)
    
    # 输出到文件
    def write_to_file(file, results, genre):
        channel_counters = {}
        for result in results:
            if result['category'] == genre:
                channel_name = result['name']
                if channel_name in channel_counters:
                    if channel_counters[channel_name] < 8:
                        file.write(f"{channel_name},{result['url']}\n")
                        channel_counters[channel_name] += 1
                else:
                    file.write(f"{channel_name},{result['url']}\n")
                    channel_counters[channel_name] = 1
    
    # 写入TXT文件
    with open(f"{output_prefix}.txt", 'w', encoding='utf-8') as txt_file:
        # 按地区分组
        regions = sorted(list(set(result['region'] for result in optimized_results)))
        for region in regions:
            txt_file.write(f"{region},#region#\n")
            
            # 按语言分组
            region_channels = [r for r in optimized_results if r['region'] == region]
            languages = sorted(list(set(r['language'] for r in region_channels)))
            for language in languages:
                txt_file.write(f"{language},#language#\n")
                
                # 按分类分组
                lang_channels = [r for r in region_channels if r['language'] == language]
                categories = sorted(list(set(r['category'] for r in lang_channels)))
                for category in categories:
                    txt_file.write(f"{category},#genre#\n")
                    write_to_file(txt_file, lang_channels, category)
        
        # 写入未分类的频道
        txt_file.write("其他频道,#genre#\n")
        write_to_file(txt_file, optimized_results, "其他频道")
    
    # 写入M3U文件
    with open(f"{output_prefix}.m3u", 'w', encoding='utf-8') as m3u_file:
        m3u_file.write('#EXTM3U\n')
        for result in optimized_results:
            m3u_file.write(f"#EXTINF:-1 group-title=\"{result['category']} | {result['region']} | {result['language']}\" tvg-name=\"{result['name']}\",{result['name']}\n")
            m3u_file.write(f"{result['url']}\n")
    
    # 写入详细速度和质量报告
    with open(f"{output_prefix}_report.txt", 'w', encoding='utf-8') as report_file:
        report_file.write("频道名称,URL,下载速度,响应时间,质量评分,类别,地区,语言\n")
        for result in optimized_results:
            report_file.write(f"{result['name']},{result['url']},{result['speed']},{result['ts_time']},{result['quality']},{result['category']},{result['region']},{result['language']}\n")
    
    # 写入不可用频道列表
    if error_channels:
        with open(f"{output_prefix}_errors.txt", 'w', encoding='utf-8') as error_file:
            error_file.write("频道名称,URL,错误信息\n")
            for name, url, error in error_channels:
                error_file.write(f"{name},{url},{error}\n")
    
    logger.info(f"处理完成! 共测试 {len(channels)} 个频道, 成功 {len(channel_results)}, 失败 {len(error_channels)}")
    logger.info(f"结果已保存到: {output_prefix}.txt, {output_prefix}.m3u, {output_prefix}_report.txt")

# 主入口函数
async def main():
    parser = argparse.ArgumentParser(description='多模式IPTV频道批量探测与测速')
    parser.add_argument('--jsmpeg', help='jsmpeg-streamer模式csv文件')
    parser.add_argument('--txiptv', help='txiptv模式csv文件')
    parser.add_argument('--zhgxtv', help='zhgxtv模式csv文件')
    parser.add_argument('--output', default='itvlist', help='输出文件前缀')
    parser.add_argument('--proxy', help='代理服务器列表文件')
    parser.add_argument('--sort-config', help='自定义排序配置文件')
    args = parser.parse_args()
    
    # 检查是否提供了至少一个CSV文件
    if not args.jsmpeg and not args.txiptv and not args.zhgxtv:
        logger.error('请至少指定一个CSV文件')
        return
    
    # 加载代理列表
    proxy_list = get_proxy_list(args.proxy) if args.proxy else []
    if proxy_list:
        logger.info(f"已加载 {len(proxy_list)} 个代理服务器")
    
    # 获取所有频道
    channels = []
    start_time = time.time()
    
    # 并发处理不同模式
    tasks = []
    if args.jsmpeg:
        logger.info(f"正在处理jsmpeg模式CSV文件: {args.jsmpeg}")
        tasks.append(get_channels_alltv(args.jsmpeg, proxy_list))
    if args.zhgxtv:
        logger.info(f"正在处理zhgxtv模式CSV文件: {args.zhgxtv}")
        tasks.append(get_channels_hgxtv(args.zhgxtv, proxy_list))
    if args.txiptv:
        logger.info(f"正在处理txiptv模式CSV文件: {args.txiptv}")
        tasks.append(get_channels_newnew(args.txiptv, proxy_list))
    
    # 执行所有任务
    results = await asyncio.gather(*tasks)
    for result in results:
        channels.extend(result)
    
    if not channels:
        logger.error('没有找到可用的频道')
        return
    
    duration = time.time() - start_time
    logger.info(f"找到 {len(channels)} 个频道，耗时 {duration:.2f} 秒，开始测试速度...")
    
    # 测试速度并输出结果
    await test_speed_and_output(channels, args.output, args.proxy, args.sort_config)

if __name__ == "__main__":
    asyncio.run(main())
