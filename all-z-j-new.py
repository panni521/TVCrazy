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
from collections import defaultdict
import time

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
    return name_map.get(name, name)

# 获取频道名称中的数字（用于排序）
def channel_key(channel_name):
    match = re.search(r'\d+', channel_name)
    return int(match.group()) if match else float('inf')

# 生成同一C段的所有IP的URL
def generate_ip_range_urls(base_url, ip_address, port, suffix=None):
    ip_parts = ip_address.split('.')
    if len(ip_parts) < 3:
        return []
    c_prefix = '.'.join(ip_parts[:3])
    return [f"{base_url}{c_prefix}.{i}{port}{suffix if suffix else ''}" for i in range(1, 256)]

# 调整并发数
def adjust_concurrency():
    return 100  # 固定并发数，避免系统过载

# 基础URL可用性检测（带重试机制）
def is_url_accessible(url, retries=2, timeout=2):
    for _ in range(retries):
        try:
            response = requests.head(url, timeout=timeout, allow_redirects=True)
            if response.status_code == 200:
                return True
            # 部分服务器不支持HEAD请求，尝试GET请求
            response = requests.get(url, timeout=timeout, stream=True)
            return response.status_code == 200
        except (requests.RequestException, Exception):
            continue
    return False

# HLS流专用验证（检查M3U8结构和TS片段）
def validate_hls_stream(url, timeout=5):
    try:
        # 1. 验证M3U8文件是否存在且有效
        response = requests.get(url, timeout=timeout)
        if response.status_code != 200:
            return False
        
        m3u8_content = response.text
        if not m3u8_content.startswith('#EXTM3U'):
            return False

        # 2. 提取TS片段
        ts_urls = []
        base_url = url.rsplit('/', 1)[0] + '/' if '/' in url else ''
        
        for line in m3u8_content.splitlines():
            line = line.strip()
            if not line.startswith('#') and line.endswith('.ts'):
                ts_urls.append(base_url + line if not line.startswith('http') else line)
        
        if not ts_urls:
            return False

        # 3. 验证至少一个TS片段可播放
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(is_url_accessible, ts_url, retries=1, timeout=3) 
                      for ts_url in ts_urls[:3]]  # 验证前3个片段
            for future in concurrent.futures.as_completed(futures):
                if future.result():
                    return True
        return False
    except Exception:
        return False

# FLV流专用验证
def validate_flv_stream(url, timeout=5):
    try:
        response = requests.get(url, timeout=timeout, stream=True)
        if response.status_code != 200:
            return False
        
        # 检查FLV文件头
        flv_header = response.raw.read(3)
        return flv_header == b'FLV'
    except Exception:
        return False

# 综合流验证（根据URL后缀自动选择验证方式）
def validate_stream(url):
    url_lower = url.lower()
    if '.m3u8' in url_lower:
        return validate_hls_stream(url)
    elif '.flv' in url_lower:
        return validate_flv_stream(url)
    else:
        # 通用验证
        return is_url_accessible(url)

# 并发检测URL可用性
def check_urls_concurrent(urls, print_valid=True):
    max_workers = adjust_concurrency()
    
    def check_url(url):
        return url if validate_stream(url) else None
    
    valid_urls = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(check_url, url) for url in urls]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                valid_urls.append(result)
                if print_valid:
                    print(f"有效链接: {result}")
    return valid_urls

# jsmpeg模式获取频道
def get_channels_alltv(csv_file):
    urls = set()
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
        ip_start = url.find("//") + 2
        ip_end = url.find(":", ip_start)
        base_url = url[:ip_start]
        ip_address = url[ip_start:ip_end]
        port = url[ip_end:]
        ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port))

    valid_urls = check_urls_concurrent(set(ip_range_urls))
    channels = []
    for url in valid_urls:
        json_url = f"{url.rstrip('/')}/streamer/list"
        try:
            json_data = requests.get(json_url, timeout=2).json()
            host = url.rstrip('/')
            for item in json_data:
                name = item.get('name', '').strip()
                key = item.get('key', '').strip()
                if name and key:
                    channel_url = f"{host}/hls/{key}/index.m3u8"
                    # 二次验证频道链接
                    if validate_stream(channel_url):
                        channels.append((channel_name_normalize(name), channel_url))
        except Exception as e:
            continue
    return channels

# txiptv模式获取频道（异步）
async def get_channels_newnew(csv_file):
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        urls = list(set(row.get('link', '').strip() for row in reader if row.get('link')))

    async def modify_urls(url):
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

    async def async_validate_stream(session, url, semaphore):
        async with semaphore:
            url_lower = url.lower()
            try:
                # 先检查基本可用性
                async with session.get(url, timeout=2) as response:
                    if response.status != 200:
                        return False

                # HLS流验证
                if '.m3u8' in url_lower:
                    async with session.get(url, timeout=5) as response:
                        content = await response.text()
                        if not content.startswith('#EXTM3U'):
                            return False
                        
                        base_url = url.rsplit('/', 1)[0] + '/' if '/' in url else ''
                        ts_lines = [line.strip() for line in content.splitlines() 
                                   if line.strip() and not line.startswith('#') and line.endswith('.ts')]
                        
                        if not ts_lines:
                            return False

                        # 验证第一个TS片段
                        ts_url = base_url + ts_lines[0] if not ts_lines[0].startswith('http') else ts_lines[0]
                        async with session.get(ts_url, timeout=3) as ts_response:
                            return ts_response.status == 200

                # FLV流验证
                elif '.flv' in url_lower:
                    async with session.get(url, timeout=5) as response:
                        header = await response.content.read(3)
                        return header == b'FLV'

                return True
            except Exception:
                return False

    async def is_url_accessible(session, url, semaphore):
        async with semaphore:
            try:
                async with session.get(url, timeout=2) as response:
                    return url if response.status == 200 else None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None

    async def check_urls(session, urls, semaphore):
        tasks = []
        for url in urls:
            modified_urls = await modify_urls(url)
            tasks.extend(asyncio.create_task(is_url_accessible(session, modified_url, semaphore)) 
                        for modified_url in modified_urls)
        results = await asyncio.gather(*tasks)
        return [result for result in results if result]

    async def fetch_json(session, url, semaphore):
        async with semaphore:
            try:
                ip_start = url.find("//") + 2
                ip_index = url.find("/", url.find(".") + 1)
                base_url = url[:ip_start]
                ip_address = url[ip_start:ip_index]
                url_x = f"{base_url}{ip_address}"
                async with session.get(url, timeout=2) as response:
                    json_data = await response.json()
                
                channels = []
                for item in json_data.get('data', []):
                    if isinstance(item, dict):
                        name = item.get('name')
                        urlx = item.get('url')
                        if ',' in urlx:
                            continue
                        urld = urlx if 'http' in urlx else f"{url_x}{urlx}"
                        if name and urlx and await async_validate_stream(session, urld, semaphore):
                            channels.append((channel_name_normalize(name), urld))
                return channels
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return []

    x_urls = []
    for url in urls:
        ip_start = url.find("//") + 2
        ip_end = url.find(":", ip_start)
        ip_dot = url.find(".") + 1
        ip_address = url[ip_start:url.find(".", ip_dot, url.find(".", ip_dot + 1)) + 1]
        port = url[ip_end:]
        x_urls.append(f"{url[:ip_start]}{ip_address}1{port}")

    unique_urls = set(x_urls)
    semaphore = asyncio.Semaphore(500)
    async with aiohttp.ClientSession() as session:
        valid_urls = await check_urls(session, unique_urls, semaphore)
        tasks = [asyncio.create_task(fetch_json(session, url, semaphore)) for url in valid_urls]
        results = await asyncio.gather(*tasks)
        return [channel for sublist in results for channel in sublist]

# zhgxtv模式获取频道
def get_channels_hgxtv(csv_file):
    urls = set()
    with open(csv_file, 'r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            host = row['host'].strip()
            if host:
                url = host if host.startswith(('http://', 'https://')) else f"http://{host}{':80' if ':' not in host else ''}"
                urls.add(url)

    ip_range_urls = []
    for url in urls:
        ip_start = url.find("//") + 2
        ip_end = url.find(":", ip_start)
        base_url = url[:ip_start]
        ip_address = url[ip_start:ip_end]
        port = url[ip_end:]
        ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port, "/ZHGXTV/Public/json/live_interface.txt"))

    valid_urls = check_urls_concurrent(set(ip_range_urls))
    channels = []
    for url in valid_urls:
        try:
            response = requests.get(url, timeout=2)
            json_data = response.content.decode('utf-8')
            for line in json_data.split('\n'):
                line = line.strip()
                if line:
                    name, channel_url = line.split(',')
                    urls_parts = channel_url.split('/', 3)
                    url_data_parts = url.split('/', 3)
                    urld = f"{urls_parts[0]}//{url_data_parts[2]}/{urls_parts[3]}" if len(urls_parts) >= 4 else f"{urls_parts[0]}//{url_data_parts[2]}"
                    # 验证频道链接
                    if validate_stream(urld):
                        channels.append((channel_name_normalize(name), urld))
        except Exception:
            continue
    return channels

# 测试频道速度并输出结果
def test_speed_and_output(channels, output_prefix="itvlist"):
    if not channels:
        print("没有有效的频道数据可处理")
        return

    task_queue = Queue()
    speed_results = []
    error_channels = []

    def worker():
        while True:
            channel_name, channel_url = task_queue.get()
            try:
                # 最终验证：下载并检查内容
                start_time = time.time()
                response = requests.get(channel_url, timeout=5, stream=True)
                if response.status_code != 200:
                    raise Exception(f"HTTP状态码错误: {response.status_code}")

                # 读取部分内容验证
                content = response.raw.read(1024 * 10)  # 读取10KB
                if not content:
                    raise Exception("无内容返回")

                # 计算速度
                end_time = time.time()
                response_time = end_time - start_time
                file_size = len(content)
                download_speed = (file_size / 1024) / response_time  # KB/s
                normalized_speed = min(max(download_speed / 1024, 0.001), 100)  # 转换为MB/s并限制范围

                speed_results.append((channel_name, channel_url, f"{normalized_speed:.3f} MB/s"))
            except Exception as e:
                error_channels.append((channel_name, channel_url))
            finally:
                progress = (len(speed_results) + len(error_channels)) / len(channels) * 100
                print(f"可用频道：{len(speed_results)} 个 , 不可用频道：{len(error_channels)} 个 , 总频道：{len(channels)} 个 ,总进度：{progress:.2f} %")
                task_queue.task_done()

    # 启动工作线程
    num_threads = 50
    for _ in range(num_threads):
        threading.Thread(target=worker, daemon=True).start()

    # 加入任务队列
    for channel in channels:
        task_queue.put(channel)
    task_queue.join()

    # 按频道分组并按速度排序
    channel_sources = defaultdict(list)
    for channel_name, channel_url, speed in speed_results:
        channel_sources[channel_name].append((channel_url, speed))

    # 每个频道保留速度最快的8个源
    optimized_sources = []
    for channel_name, sources in channel_sources.items():
        # 按速度降序排序
        sorted_sources = sorted(sources, key=lambda x: float(x[1].split()[0]), reverse=True)[:8]
        for url, speed in sorted_sources:
            optimized_sources.append((channel_name, url, speed))

    # 去重
    unique_channels = []
    seen = set()
    for item in optimized_sources:
        key = (item[0], item[1])
        if key not in seen:
            unique_channels.append(item)
            seen.add(key)

    # 排序：先央视，后其他，按名称排序
    def custom_sort_key(item):
        name = item[0]
        if name.startswith('CCTV'):
            num = re.search(r'\d+', name)
            return (0, int(num.group()) if num else float('inf'))
        elif '卫视' in name:
            return (1, name)
        return (2, name)

    unique_channels.sort(key=custom_sort_key)

    # 写入TXT文件
    with open(f"{output_prefix}.txt", 'w', encoding='utf-8') as txt_file:
        txt_file.write('央视频道,#genre#\n')
        for item in unique_channels:
            if 'CCTV' in item[0]:
                txt_file.write(f"{item[0]},{item[1]}\n")
        
        txt_file.write('卫视频道,#genre#\n')
        for item in unique_channels:
            if '卫视' in item[0] and 'CCTV' not in item[0]:
                txt_file.write(f"{item[0]},{item[1]}\n")
        
        txt_file.write('其他频道,#genre#\n')
        for item in unique_channels:
            if 'CCTV' not in item[0] and '卫视' not in item[0] and '测试' not in item[0]:
                txt_file.write(f"{item[0]},{item[1]}\n")

    # 写入M3U文件
    with open(f"{output_prefix}.m3u", 'w', encoding='utf-8') as m3u_file:
        m3u_file.write('#EXTM3U\n')
        
        for item in unique_channels:
            if 'CCTV' in item[0]:
                m3u_file.write(f"#EXTINF:-1 group-title=\"央视频道\",{item[0]}\n")
                m3u_file.write(f"{item[1]}\n")
        
        for item in unique_channels:
            if '卫视' in item[0] and 'CCTV' not in item[0]:
                m3u_file.write(f"#EXTINF:-1 group-title=\"卫视频道\",{item[0]}\n")
                m3u_file.write(f"{item[1]}\n")
        
        for item in unique_channels:
            if 'CCTV' not in item[0] and '卫视' not in item[0] and '测试' not in item[0]:
                m3u_file.write(f"#EXTINF:-1 group-title=\"其他频道\",{item[0]}\n")
                m3u_file.write(f"{item[1]}\n")

    # 写入速度日志
    with open("speed.txt", 'w', encoding='utf-8') as speed_file:
        for item in unique_channels:
            speed_file.write(f"{item[0]},{item[1]},{item[2]}\n")

    print(f"处理完成，共生成 {len(unique_channels)} 个有效频道")

# 主入口函数
def main():
    parser = argparse.ArgumentParser(description='多模式IPTV频道批量探测与测速工具（增强版）')
    parser.add_argument('--jsmpeg', help='jsmpeg-streamer模式csv文件')
    parser.add_argument('--txiptv', help='txiptv模式csv文件')
    parser.add_argument('--zhgxtv', help='zhgxtv模式csv文件')
    parser.add_argument('--output', default='itvlist', help='输出文件前缀')
    args = parser.parse_args()

    channels = []
    if args.jsmpeg:
        print("正在处理jsmpeg模式频道...")
        channels.extend(get_channels_alltv(args.jsmpeg))
    if args.zhgxtv:
        print("正在处理zhgxtv模式频道...")
        channels.extend(get_channels_hgxtv(args.zhgxtv))
    if args.txiptv:
        print("正在处理txiptv模式频道...")
        channels.extend(asyncio.run(get_channels_newnew(args.txiptv)))

    if not channels:
        print('未找到任何有效频道，请检查输入文件')
        return

    print(f"共发现 {len(channels)} 个初始频道，开始进行最终验证和测速...")
    test_speed_and_output(channels, args.output)

if __name__ == "__main__":
    main()
