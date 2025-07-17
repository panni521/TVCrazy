import re
import csv
import requests
import concurrent.futures
import asyncio
import aiohttp
import os
import threading
from queue import Queue
import argparse
import time
import random
from urllib.parse import urlparse
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
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

# 获取频道名称中的数字
def channel_key(channel_name):
    match = re.search(r'\d+', channel_name)
    if match:
        return int(match.group())
    return float('inf')

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
    ip_parts = ip_address.split('.')
    if len(ip_parts) < 3:
        return []
    c_prefix = '.'.join(ip_parts[:3])
    return [f"{base_url}{c_prefix}.{i}{port}{suffix if suffix else ''}" for i in range(1, 256)]

# 自适应并发数，根据系统性能和网络状况调整
def adjust_concurrency():
    try:
        import psutil
        cpu_count = psutil.cpu_count(logical=False)
        memory_percent = psutil.virtual_memory().percent
        # 根据CPU核心数和内存使用情况动态调整并发数
        if memory_percent < 70:
            return max(50, cpu_count * 10)
        else:
            return max(30, cpu_count * 5)
    except ImportError:
        return 100  # 默认并发数

# 获取代理列表
def get_proxy_list(proxy_file=None):
    if not proxy_file or not os.path.exists(proxy_file):
        return []
    
    with open(proxy_file, 'r') as f:
        proxies = [line.strip() for line in f if line.strip()]
    
    return [{"http": proxy, "https": proxy} for proxy in proxies]

# 深度检查URL是否为可用的IPTV源
def is_valid_iptv_source(url, retries=3, proxies=None):
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
            # 第一阶段：检查M3U8文件
            response = requests.get(url, timeout=5, headers=headers, proxies=proxy)
            if response.status_code != 200:
                continue
                
            content_type = response.headers.get('Content-Type', '')
            if 'mpegurl' not in content_type and 'x-mpegurl' not in content_type:
                # 尝试从内容判断
                if not response.text.startswith('#EXTM3U'):
                    logger.debug(f"{url} 不是有效的M3U8文件")
                    continue
            
            # 第二阶段：解析M3U8文件
            lines = response.text.strip().split('\n')
            if not lines or not lines[0].startswith('#EXTM3U'):
                logger.debug(f"{url} 不包含M3U8头部")
                continue
                
            ts_files = [line for line in lines if not line.startswith('#') and line.endswith(('.ts', '.m3u8'))]
            if not ts_files:
                logger.debug(f"{url} 不包含有效的媒体片段")
                continue
                
            # 第三阶段：随机选择一个TS文件测试
            base_url = url.rsplit('/', 1)[0] + '/'
            test_ts_url = ts_files[0]
            if not test_ts_url.startswith(('http://', 'https://')):
                test_ts_url = base_url + test_ts_url
            
            ts_response = requests.get(test_ts_url, timeout=8, headers=headers, proxies=proxy)
            if ts_response.status_code == 200 and len(ts_response.content) > 1024:  # 确保内容大小合理
                return url
        except Exception as e:
            logger.debug(f"尝试访问 {url} 失败 (尝试 {attempt+1}/{retries}): {e}")
            continue
    
    return None

# 并发检测URL可用性
def check_urls_concurrent(urls, timeout=3, print_valid=True, proxies=None):
    max_workers = adjust_concurrency()
    valid_urls = []
    
    def check_url(url):
        return is_valid_iptv_source(url, retries=3, proxies=proxies)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_url, url): url for url in urls}
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                result = future.result()
                if result:
                    valid_urls.append(result)
                    if print_valid:
                        logger.info(f"有效URL: {result}")
            except Exception as e:
                logger.error(f"检测URL {url} 时出错: {e}")
    
    return valid_urls

# jsmpeg模式获取频道
def get_channels_alltv(csv_file, proxies=None):
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
                ip_start = url.find("//") + 2
                ip_end = url.find(":", ip_start)
                base_url = url[:ip_start]
                ip_address = url[ip_start:ip_end]
                port = url[ip_end:]
                ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port))
            except:
                logger.error(f"处理URL {url} 时出错")
                continue
        
        valid_urls = check_urls_concurrent(set(ip_range_urls), proxies=proxies)
        channels = []
        
        for url in valid_urls:
            try:
                json_url = f"{url.rstrip('/')}/streamer/list"
                json_data = requests.get(json_url, timeout=3).json()
                host = url.rstrip('/')
                for item in json_data:
                    name = item.get('name', '').strip()
                    key = item.get('key', '').strip()
                    if name and key:
                        channel_url = f"{host}/hls/{key}/index.m3u8"
                        channels.append((channel_name_normalize(name), channel_url))
            except Exception as e:
                logger.error(f"获取频道列表 from {url} 失败: {e}")
                continue
        
        return channels
    except Exception as e:
        logger.error(f"处理jsmpeg模式CSV文件失败: {e}")
        return []

# txiptv模式获取频道（异步）
async def get_channels_newnew(csv_file, proxies=None):
    try:
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            urls = list(set(row.get('link', '').strip() for row in reader if row.get('link')))
        
        async def modify_urls(url):
            try:
                ip_start = url.find("//") + 2
                ip_end = url.find(":", ip_start)
                base_url = url[:ip_start]
                ip_address = url[ip_start:ip_end]
                ip_parts = ip_address.split('.')
                if len(ip_parts) < 3:
                    return []
                c_prefix = '.'.join(ip_parts[:3])
                port = url[ip_end:]
                ip_end = "/iptv/live/1000.json?key=txiptv"
                return [f"{base_url}{c_prefix}.{i}{port}{ip_end}" for i in range(1, 256)]
            except:
                return []
        
        async def is_url_accessible(session, url, semaphore):
            async with semaphore:
                try:
                    # 随机选择代理
                    proxy = random.choice(proxies + [None]) if proxies else None
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    }
                    async with session.get(url, timeout=3, headers=headers, proxy=proxy) as response:
                        return url if response.status == 200 else None
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.debug(f"尝试访问 {url} 失败: {e}")
                    return None
        
        async def check_urls(session, urls, semaphore):
            tasks = []
            for url in urls:
                modified_urls = await modify_urls(url)
                tasks.extend(asyncio.create_task(is_url_accessible(session, modified_url, semaphore)) for modified_url in modified_urls)
            results = await asyncio.gather(*tasks)
            valid_urls = [result for result in results if result]
            for url in valid_urls:
                logger.info(f"有效URL: {url}")
            return valid_urls
        
        async def fetch_json(session, url, semaphore):
            async with semaphore:
                try:
                    ip_start = url.find("//") + 2
                    ip_index = url.find("/", url.find(".") + 1)
                    base_url = url[:ip_start]
                    ip_address = url[ip_start:ip_index]
                    url_x = f"{base_url}{ip_address}"
                    json_data = await session.get(url, timeout=3).json()
                    channels = []
                    for item in json_data.get('data', []):
                        if isinstance(item, dict):
                            name = item.get('name')
                            urlx = item.get('url')
                            if ',' in urlx:
                                urlx = "aaaaaaaa"
                            urld = urlx if 'http' in urlx else f"{url_x}{urlx}"
                            if name and urlx:
                                channels.append((channel_name_normalize(name), urld))
                    return channels
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                    logger.error(f"获取JSON数据 from {url} 失败: {e}")
                    return []
        
        x_urls = []
        for url in urls:
            try:
                ip_start = url.find("//") + 2
                ip_end = url.find(":", ip_start)
                ip_dot = url.find(".") + 1
                ip_address = url[ip_start:url.find(".", ip_dot, url.find(".", ip_dot + 1)) + 1]
                port = url[ip_end:]
                x_urls.append(f"{url[:ip_start]}{ip_address}1{port}")
            except:
                logger.error(f"处理URL {url} 时出错")
                continue
        
        unique_urls = set(x_urls)
        semaphore = asyncio.Semaphore(500)
        
        # 创建代理会话
        conn = aiohttp.TCPConnector(limit_per_host=100)
        async with aiohttp.ClientSession(connector=conn) as session:
            valid_urls = await check_urls(session, unique_urls, semaphore)
            tasks = [asyncio.create_task(fetch_json(session, url, semaphore)) for url in valid_urls]
            results = await asyncio.gather(*tasks)
            return [channel for sublist in results for channel in sublist]
    except Exception as e:
        logger.error(f"处理txiptv模式CSV文件失败: {e}")
        return []

# zhgxtv模式获取频道
def get_channels_hgxtv(csv_file, proxies=None):
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
                ip_start = url.find("//") + 2
                ip_end = url.find(":", ip_start)
                base_url = url[:ip_start]
                ip_address = url[ip_start:ip_end]
                port = url[ip_end:]
                ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port, "/ZHGXTV/Public/json/live_interface.txt"))
            except:
                logger.error(f"处理URL {url} 时出错")
                continue
        
        valid_urls = check_urls_concurrent(set(ip_range_urls), proxies=proxies)
        channels = []
        
        for url in valid_urls:
            try:
                json_data = requests.get(url, timeout=3).content.decode('utf-8')
                for line in json_data.split('\n'):
                    line = line.strip()
                    if line:
                        try:
                            name, channel_url = line.split(',')
                            urls_parts = channel_url.split('/', 3)
                            url_data_parts = url.split('/', 3)
                            urld = f"{urls_parts[0]}//{url_data_parts[2]}/{urls_parts[3]}" if len(urls_parts) >= 4 else f"{urls_parts[0]}//{url_data_parts[2]}"
                            channels.append((channel_name_normalize(name), urld))
                        except:
                            continue
            except Exception as e:
                logger.error(f"获取频道列表 from {url} 失败: {e}")
                continue
        
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
                category_order = [info['name'] for info in CHANNEL_CATEGORIES.values()]
                value = category_order.index(value) if value in category_order else len(category_order)
            
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

# 多维度测试频道速度并输出结果
def test_speed_and_output(channels, output_prefix="itvlist", proxy_file=None, sort_config=None):
    task_queue = Queue()
    channel_results = []
    error_channels = []
    proxy_list = get_proxy_list(proxy_file) if proxy_file else []
    
    def worker():
        while True:
            channel_name, channel_url = task_queue.get()
            try:
                # 测试连接可用性
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                
                # 随机选择代理
                proxy = random.choice(proxy_list + [None]) if proxy_list else None
                
                # 测试M3U8文件获取
                start_time = time.time()
                response = requests.get(channel_url, timeout=5, headers=headers, proxies=proxy)
                m3u8_time = time.time() - start_time
                
                if response.status_code != 200:
                    raise Exception(f"获取M3U8失败，状态码: {response.status_code}")
                
                lines = response.text.strip().split('\n')
                ts_lists = [line for line in lines if not line.startswith('#') and line.endswith('.ts')]
                
                if not ts_lists:
                    raise Exception("No valid TS files found.")
                
                # 测试TS文件获取
                channel_url_t = channel_url.rstrip(channel_url.split('/')[-1])
                ts_url = channel_url_t + ts_lists[0].split('/')[-1]
                
                start_time = time.time()
                ts_response = requests.get(ts_url, timeout=5, headers=headers, proxies=proxy)
                ts_time = time.time() - start_time
                
                if ts_response.status_code != 200:
                    raise Exception(f"获取TS文件失败，状态码: {ts_response.status_code}")
                
                file_size = len(ts_response.content)
                download_speed = file_size / ts_time / 1024 / 1024  # MB/s
                
                # 计算频道质量评分 (综合考虑速度和响应时间)
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
            finally:
                progress = (len(channel_results) + len(error_channels)) / len(channels) * 100
                logger.info(f"进度: {len(channel_results)}/{len(channels)} 可用, {len(error_channels)} 失败, 总进度: {progress:.2f}%")
                task_queue.task_done()
    
    # 启动工作线程
    num_threads = adjust_concurrency()
    for _ in range(num_threads):
        threading.Thread(target=worker, daemon=True).start()
    
    # 添加所有频道到任务队列
    for channel in channels:
        task_queue.put(channel)
    
    # 等待所有任务完成
    task_queue.join()
    
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
def main():
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
    if args.jsmpeg:
        logger.info(f"正在处理jsmpeg模式CSV文件: {args.jsmpeg}")
        channels.extend(get_channels_alltv(args.jsmpeg, proxy_list))
    if args.zhgxtv:
        logger.info(f"正在处理zhgxtv模式CSV文件: {args.zhgxtv}")
        channels.extend(get_channels_hgxtv(args.zhgxtv, proxy_list))
    if args.txiptv:
        logger.info(f"正在处理txiptv模式CSV文件: {args.txiptv}")
        channels.extend(asyncio.run(get_channels_newnew(args.txiptv, proxy_list)))
    
    if not channels:
        logger.error('没有找到可用的频道')
        return
    
    logger.info(f"找到 {len(channels)} 个频道，开始测试速度...")
    
    # 测试速度并输出结果
    test_speed_and_output(channels, args.output, args.proxy, args.sort_config)

if __name__ == "__main__":
    main()
