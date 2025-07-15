import argparse
import time
import datetime
import concurrent.futures
import requests
import re
import os
import threading
from queue import Queue
import csv
import asyncio
import aiohttp

# 三个csv文件（与代码文件存放在相同目录中）
JSMPEG_CSV = "jsmpeg.csv"
TXIPTV_CSV = "txiptv_new.csv"
ZHGXTX_CSV = "zhgxtv_new.csv"

# 频道分类关键词
CHANNEL_CATEGORIES = {
    "香港频道": ["TVB翡翠台", "翡翠台", "明珠台", "凤凰香港", "凤凰卫视", "凤凰资讯"],
    "广东频道": ["广东新闻", "广东珠江", "广州综合", "广州新闻"],
    "央视频道": ["CCTV"],
    "其他频道": []  # 作为默认分类
}

# ===================== 通用工具 =====================
def channel_name_normalize(name):
    """标准化频道名称，移除冗余词汇并统一格式"""
    name = name.replace("cctv", "CCTV")
    name = name.replace("中央", "CCTV")
    name = name.replace("央视", "CCTV")
    for rep in ["高清", "超高", "HD", "标清", "频道", "-", " ", "PLUS", "＋", "(", ")"]:
        name = name.replace(rep, "" if rep not in ["PLUS", "＋"] else "+")
    name = re.sub(r"CCTV(\d+)台", r"CCTV\1", name)
    name_map = {
        "CCTV1综合": "CCTV1", "CCTV2财经": "CCTV2", "CCTV3综艺": "CCTV3", "CCTV4国际": "CCTV4",
        "CCTV4中文国际": "CCTV4", "CCTV4欧洲": "CCTV4", "CCTV5体育": "CCTV5", "CCTV6电影": "CCTV6",
        "CCTV7军事": "CCTV7", "CCTV7军农": "CCTV7", "CCTV7农业": "CCTV7", "CCTV7国防军事": "CCTV7",
        "CCTV8电视剧": "CCTV8", "CCTV9记录": "CCTV9", "CCTV9纪录": "CCTV9", "CCTV10科教": "CCTV10",
        "CCTV11戏曲": "CCTV11", "CCTV12社会与法": "CCTV12", "CCTV13新闻": "CCTV13", "CCTV新闻": "CCTV13",
        "CCTV14少儿": "CCTV14", "CCTV15音乐": "CCTV15", "CCTV16奥林匹克": "CCTV16",
        "CCTV17农业农村": "CCTV17", "CCTV17农业": "CCTV17", "CCTV5+体育赛视": "CCTV5+",
        "CCTV5+体育赛事": "CCTV5+", "CCTV5+体育": "CCTV5+"
    }
    return name_map.get(name, name)

def classify_channel(name):
    """根据频道名称将其归类到预定义的分类中"""
    for category, keywords in CHANNEL_CATEGORIES.items():
        if category == "其他频道":
            continue  # 最后处理默认分类
        if any(keyword in name for keyword in keywords):
            return category
        if category == "央视频道" and name.startswith("CCTV"):
            return category
    return "其他频道"  # 默认分类

def channel_key(channel_name):
    """用于排序的键函数，提取频道名中的数字部分"""
    match = re.search(r'\d+', channel_name)
    if match:
        return int(match.group())
    else:
        return float('inf')

def generate_ip_range_urls(base_url, ip_address, port, suffix=None):
    """生成同一C段的所有IP的URL，确保IP合法"""
    ip_parts = ip_address.split('.')
    if len(ip_parts) < 3:
        return []
    c_prefix = '.'.join(ip_parts[:3])
    urls = []
    for i in range(1, 256):
        full_ip = f"{c_prefix}.{i}"
        url = f"{base_url}{full_ip}{port}"
        if suffix:
            url += suffix
        urls.append(url)
    return urls

def check_urls_concurrent(urls, timeout=1, max_workers=100, print_valid=True):
    """并发检测URL可用性，返回可用URL列表"""
    valid_urls = []
    def is_url_accessible(url):
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200:
                return url
        except requests.RequestException:
            pass
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(is_url_accessible, url) for url in urls]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                valid_urls.append(result)
                if print_valid:
                    print(f"有效URL: {result}")
    return valid_urls

# ===================== 1. jsmpeg模式 =====================
def get_channels_alltv():
    """从jsmpeg.csv文件获取频道信息"""
    urls = set()
    try:
        with open(JSMPEG_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            if not fieldnames or 'host' not in fieldnames:
                print(f"错误: {JSMPEG_CSV}缺少host列")
                return []
            for row in reader:
                host = row['host'].strip()
                if host:
                    if host.startswith('http://') or host.startswith('https://'):
                        urls.add(host)
                    else:
                        urls.add(f"http://{host}")
    except FileNotFoundError:
        print(f"警告: {JSMPEG_CSV}文件不存在，跳过jsmpeg模式")
        return []
    
    ip_range_urls = []
    for url in urls:
        url = url.strip()
        ip_start_index = url.find("//") + 2
        ip_end_index = url.find(":", ip_start_index)
        base_url = url[:ip_start_index]
        ip_address = url[ip_start_index:ip_end_index]
        port = url[ip_end_index:]
        ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port))
    
    valid_urls = check_urls_concurrent(set(ip_range_urls))
    channels = []
    for url in valid_urls:
        json_url = url.rstrip('/') + '/streamer/list'
        try:
            response = requests.get(json_url, timeout=1)
            json_data = response.json()
            host = url.rstrip('/')
            for item in json_data:
                name = item.get('name', '').strip()
                key = item.get('key', '').strip()
                if not name or not key:
                    continue
                channel_url = f"{host}/hls/{key}/index.m3u8"
                name = channel_name_normalize(name)
                channels.append((name, channel_url))
        except Exception as e:
            print(f"获取{url}频道信息失败: {e}")
            continue
    return channels

# ===================== 2. txiptv模式（异步） =====================
async def get_channels_newnew():
    """从txiptv_new.csv文件获取频道信息"""
    urls = []
    try:
        with open(TXIPTV_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            url_set = set()
            for row in reader:
                link = row.get('link', '').strip()
                if link:
                    url_set.add(link)
            urls = list(url_set)
    except FileNotFoundError:
        print(f"警告: {TXIPTV_CSV}文件不存在，跳过txiptv模式")
        return []
    
    async def modify_urls(url):
        modified_urls = []
        ip_start_index = url.find("//") + 2
        ip_end_index = url.find(":", ip_start_index)
        base_url = url[:ip_start_index]
        ip_address = url[ip_start_index:ip_end_index]
        ip_parts = ip_address.split('.')
        if len(ip_parts) < 3:
            return []
        c_prefix = '.'.join(ip_parts[:3])
        port = url[ip_end_index:]
        ip_end = "/iptv/live/1000.json?key=txiptv"
        for i in range(1, 256):
            full_ip = f"{c_prefix}.{i}"
            modified_url = f"{base_url}{full_ip}{port}{ip_end}"
            modified_urls.append(modified_url)
        return modified_urls
    
    async def is_url_accessible(session, url, semaphore):
        async with semaphore:
            try:
                async with session.get(url, timeout=1) as response:
                    if response.status == 200:
                        return url
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
        return None
    
    async def check_urls(session, urls, semaphore):
        tasks = []
        for url in urls:
            url = url.strip()
            modified_urls = await modify_urls(url)
            for modified_url in modified_urls:
                task = asyncio.create_task(is_url_accessible(session, modified_url, semaphore))
                tasks.append(task)
        results = await asyncio.gather(*tasks)
        valid_urls = [result for result in results if result]
        for url in valid_urls:
            print(f"有效URL: {url}")
        return valid_urls
    
    async def fetch_json(session, url, semaphore):
        async with semaphore:
            try:
                ip_start_index = url.find("//") + 2
                ip_dot_start = url.find(".") + 1
                ip_index_second = url.find("/", ip_dot_start)
                base_url = url[:ip_start_index]
                ip_address = url[ip_start_index:ip_index_second]
                url_x = f"{base_url}{ip_address}"
                json_url = f"{url}"
                async with session.get(json_url, timeout=1) as response:
                    json_data = await response.json()
                    channels = []
                    try:
                        for item in json_data['data']:
                            if isinstance(item, dict):
                                name = item.get('name')
                                urlx = item.get('url')
                                if ',' in urlx:
                                    urlx = "aaaaaaaa"
                                if 'http' in urlx:
                                    urld = f"{urlx}"
                                else:
                                    urld = f"{url_x}{urlx}"
                                if name and urlx:
                                    name = channel_name_normalize(name)
                                    channels.append((name, urld))
                    except Exception as e:
                        print(f"解析JSON数据失败: {e}")
                        pass
                    return channels
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                print(f"获取{url}数据失败: {e}")
                return []
    
    x_urls = []
    for url in urls:
        url = url.strip()
        ip_start_index = url.find("//") + 2
        ip_end_index = url.find(":", ip_start_index)
        ip_dot_start = url.find(".") + 1
        ip_dot_second = url.find(".", ip_dot_start) + 1
        ip_dot_three = url.find(".", ip_dot_second) + 1
        base_url = url[:ip_start_index]
        ip_address = url[ip_start_index:ip_dot_three]
        port = url[ip_end_index:]
        ip_end = "1"
        modified_ip = f"{ip_address}{ip_end}"
        x_url = f"{base_url}{modified_ip}{port}"
        x_urls.append(x_url)
    
    unique_urls = set(x_urls)
    semaphore = asyncio.Semaphore(500)
    async with aiohttp.ClientSession() as session:
        valid_urls = await check_urls(session, unique_urls, semaphore)
        all_channels = []
        for url in valid_urls:
            channels = await fetch_json(session, url, semaphore)
            all_channels.extend(channels)
        return all_channels

# ===================== 3. zhgxtv模式 =====================
def get_channels_zhgxtv():
    """从zhgxtv_new.csv文件获取频道信息"""
    urls = set()
    try:
        with open(ZHGXTX_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            if not fieldnames or 'host' not in fieldnames:
                print(f"错误: {ZHGXTX_CSV}缺少host列")
                return []
            for row in reader:
                host = row['host'].strip()
                if host:
                    if host.startswith('http://') or host.startswith('https://'):
                        urls.add(host)
                    else:
                        urls.add(f"http://{host}")
    except FileNotFoundError:
        print(f"警告: {ZHGXTX_CSV}文件不存在，跳过zhgxtv模式")
        return []
    
    ip_range_urls = []
    for url in urls:
        url = url.strip()
        ip_start_index = url.find("//") + 2
        ip_end_index = url.find(":", ip_start_index)
        base_url = url[:ip_start_index]
        ip_address = url[ip_start_index:ip_end_index]
        port = url[ip_end_index:]
        ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port, suffix="/ZHGXTV/Public/json/live_interface.txt"))
    
    valid_urls = check_urls_concurrent(set(ip_range_urls))
    channels = []
    for url in valid_urls:
        try:
            response = requests.get(url, timeout=1)
            text_data = response.text
            lines = text_data.splitlines()
            for line in lines:
                parts = line.split(',')
                if len(parts) == 2:
                    name = parts[0].strip()
                    channel_url = parts[1].strip()
                    name = channel_name_normalize(name)
                    channels.append((name, channel_url))
        except Exception as e:
            print(f"获取{url}频道信息失败: {e}")
            continue
    return channels

# ===================== 输出处理 =====================
def generate_output_files(channels, output_prefix="itvlist"):
    """生成多种格式的输出文件，并按分类组织频道"""
    # 创建输出目录（如果不存在）
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    
    # 按分类组织频道
    categorized_channels = {}
    for name, url in channels:
        category = classify_channel(name)
        if category not in categorized_channels:
            categorized_channels[category] = []
        categorized_channels[category].append((name, url))
    
    # 生成M3U文件（按分类排序）
    m3u_file = os.path.join(output_dir, f"{output_prefix}.m3u")
    with open(m3u_file, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        # 按分类排序
        for category in sorted(categorized_channels.keys()):
            # 每个分类的频道按数字排序
            sorted_channels = sorted(
                categorized_channels[category], 
                key=lambda x: channel_key(x[0])
            )
            for name, url in sorted_channels:
                f.write(f"#EXTINF:-1 group-title=\"{category}\",{name}\n")
                f.write(f"{url}\n")
    
    # 生成文本文件（按分类分组）
    txt_file = os.path.join(output_dir, f"{output_prefix}.txt")
    with open(txt_file, 'w', encoding='utf-8') as f:
        for category in sorted(categorized_channels.keys()):
            f.write(f"{category}\n")
            # 每个分类的频道按数字排序
            sorted_channels = sorted(
                categorized_channels[category], 
                key=lambda x: channel_key(x[0])
            )
            for name, url in sorted_channels:
                f.write(f"{name},{url}\n")
            f.write("\n")  # 分类之间空行分隔
    
    print(f"已生成M3U文件: {m3u_file}")
    print(f"已生成文本文件: {txt_file}")

def main():
    print("开始IPTV频道探测...")
    start_time = time.time()
    
    all_channels = []
    
    # 获取jsmpeg模式的频道
    print(f"正在处理 {JSMPEG_CSV}...")
    jsmpeg_channels = get_channels_alltv()
    all_channels.extend(jsmpeg_channels)
    print(f"从jsmpeg模式获取了 {len(jsmpeg_channels)} 个频道")
    
    # 获取txiptv模式的频道
    print(f"正在处理 {TXIPTV_CSV}...")
    loop = asyncio.get_event_loop()
    txiptv_channels = loop.run_until_complete(get_channels_newnew())
    all_channels.extend(txiptv_channels)
    print(f"从txiptv模式获取了 {len(txiptv_channels)} 个频道")
    
    # 获取zhgxtv模式的频道
    print(f"正在处理 {ZHGXTX_CSV}...")
    zhgxtv_channels = get_channels_zhgxtv()
    all_channels.extend(zhgxtv_channels)
    print(f"从zhgxtv模式获取了 {len(zhgxtv_channels)} 个频道")
    
    # 去重处理（基于频道名称）
    unique_channels = []
    channel_names = set()
    for name, url in all_channels:
        if name not in channel_names:
            channel_names.add(name)
            unique_channels.append((name, url))
    
    print(f"总共获取了 {len(unique_channels)} 个唯一频道")
    
    # 生成输出文件
    generate_output_files(unique_channels)
    
    end_time = time.time()
    print(f"处理完成，耗时: {end_time - start_time:.2f} 秒")

if __name__ == "__main__":
    main()
