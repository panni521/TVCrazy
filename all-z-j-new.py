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

class ChannelNameClassifier:
    def __init__(self):
        self.base_patterns = {
            r'^CCTV\s*(\d+)\s*[+＋]?': r'CCTV\1+',
            r'^CCTV\s*(\d+)': r'CCTV\1',
            r'^(\w+卫视)': r'\1',
            r'^(\w+新闻)': r'\1',
            r'^(\w+电影)': r'\1',
            r'^(\w+体育)': r'\1',
            r'^(\w+少儿)': r'\1',
            r'^(\w+音乐)': r'\1',
        }
        self.interference_words = [
            "高清", "超清", "标清", "HD", "SD", "4K", "测试", "TV", "频道", 
            "卫视", "中央", "电视台", "中国", "网络", "直播", "官网",
            "官方", "CN", "China", "Plus", "＋", "+", "(", ")", "【", "】",
            "-", "_", " ", "/"
        ]
    
    def normalize(self, name):
        for word in self.interference_words:
            name = name.replace(word, "")
        name = name.upper()
        for pattern, repl in self.base_patterns.items():
            if re.match(pattern, name):
                return re.sub(pattern, repl, name)
        return name

def channel_name_normalize(name):
    return ChannelNameClassifier().normalize(name)

def channel_key(channel_name):
    match = re.search(r'\d+', channel_name)
    return int(match.group()) if match else float('inf')

def generate_ip_range_urls(base_url, ip_address, port, suffix=None):
    ip_parts = ip_address.split('.')
    if len(ip_parts) < 3:
        return []
    c_prefix = '.'.join(ip_parts[:3])
    return [f"{base_url}{c_prefix}.{i}{port}{suffix if suffix else ''}" for i in range(1, 256)]

def adjust_concurrency():
    return 100

def validate_channel_url(url, retries=3, timeout=3):
    """验证URL是否有效，尝试获取m3u8文件并解析TS片段"""
    for attempt in range(retries):
        try:
            # 第一步：检查m3u8文件是否可访问
            response = requests.get(url, timeout=timeout)
            if response.status_code != 200:
                raise Exception(f"HTTP状态码: {response.status_code}")
            
            content_type = response.headers.get('Content-Type', '')
            if 'text' not in content_type and 'mpegurl' not in content_type:
                raise Exception(f"无效内容类型: {content_type}")
            
            # 第二步：解析m3u8文件，查找TS片段
            lines = response.text.strip().split('\n')
            ts_lists = [line for line in lines if not line.startswith('#') and line.endswith(('.ts', '.m3u8'))]
            
            if not ts_lists:
                raise Exception("未找到有效的TS或m3u8片段")
            
            # 第三步：验证TS片段是否可访问
            base_url = url.rsplit('/', 1)[0] + '/'
            ts_url = base_url + ts_lists[0].split('/')[-1]
            
            ts_response = requests.head(ts_url, timeout=timeout)
            if ts_response.status_code == 200:
                return True
                
        except Exception as e:
            print(f"验证失败 ({attempt+1}/{retries}): {url} - {str(e)}")
            continue
    
    return False

def check_urls_concurrent(urls, timeout=3, retries=3):
    valid_urls = []
    
    def check_url(url):
        if validate_channel_url(url, retries, timeout):
            valid_urls.append(url)
            print(f"有效URL: {url}")
            return True
        return False
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=adjust_concurrency()) as executor:
        list(executor.map(check_url, urls))
    
    return valid_urls

def replace_with_cdn(url):
    cdn_base = "https://cdn.example.com"
    return url.replace("http://original-server.com", cdn_base)

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
            json_data = requests.get(json_url, timeout=3).json()
            host = url.rstrip('/')
            for item in json_data:
                name = item.get('name', '').strip()
                key = item.get('key', '').strip()
                if name and key:
                    channel_url = f"{host}/hls/{key}/index.m3u8"
                    channel_url = replace_with_cdn(channel_url)
                    # 最终验证
                    if validate_channel_url(channel_url):
                        channels.append((channel_name_normalize(name), channel_url))
                    else:
                        print(f"无效频道URL: {channel_url}")
        except Exception as e:
            print(f"获取频道列表失败: {json_url} - {str(e)}")
            continue
    
    return channels

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

    async def is_url_accessible(session, url, semaphore):
        async with semaphore:
            try:
                async with session.get(url, timeout=3) as response:
                    return url if response.status == 200 else None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None

    async def check_urls(session, urls, semaphore):
        tasks = []
        for url in urls:
            modified_urls = await modify_urls(url)
            tasks.extend(asyncio.create_task(is_url_accessible(session, modified_url, semaphore)) for modified_url in modified_urls)
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
                json_data = await session.get(url, timeout=3).json()
                channels = []
                
                for item in json_data.get('data', []):
                    if isinstance(item, dict):
                        name = item.get('name')
                        urlx = item.get('url')
                        if not name or not urlx:
                            continue
                            
                        if ',' in urlx:
                            urlx = urlx.split(',')[0]  # 尝试使用第一个URL
                            
                        urld = urlx if 'http' in urlx else f"{url_x}{urlx}"
                        urld = replace_with_cdn(urld)
                        
                        # 实时验证
                        if await validate_url_async(session, urld):
                            channels.append((channel_name_normalize(name), urld))
                        else:
                            print(f"无效频道URL: {urld}")
                return channels
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                print(f"获取JSON失败: {url} - {str(e)}")
                return []

    async def validate_url_async(session, url):
        try:
            async with session.get(url, timeout=3) as response:
                if response.status != 200:
                    return False
                
                content = await response.text()
                lines = content.strip().split('\n')
                ts_lists = [line for line in lines if not line.startswith('#') and line.endswith(('.ts', '.m3u8'))]
                
                if not ts_lists:
                    return False
                    
                base_url = url.rsplit('/', 1)[0] + '/'
                ts_url = base_url + ts_lists[0].split('/')[-1]
                
                async with session.head(ts_url, timeout=3) as ts_response:
                    return ts_response.status == 200
        except:
            return False

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
        print(f"找到 {len(valid_urls)} 个有效服务器")
        
        tasks = []
        for url in valid_urls:
            tasks.append(asyncio.create_task(fetch_json(session, url, semaphore)))
        
        results = await asyncio.gather(*tasks)
        return [channel for sublist in results for channel in sublist]

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
            json_data = requests.get(url, timeout=3).content.decode('utf-8')
            for line in json_data.split('\n'):
                line = line.strip()
                if line:
                    try:
                        name, channel_url = line.split(',')
                    except ValueError:
                        continue
                        
                    urls_parts = channel_url.split('/', 3)
                    url_data_parts = url.split('/', 3)
                    urld = f"{urls_parts[0]}//{url_data_parts[2]}/{urls_parts[3]}" if len(urls_parts) >= 4 else f"{urls_parts[0]}//{url_data_parts[2]}"
                    urld = replace_with_cdn(urld)
                    
                    # 最终验证
                    if validate_channel_url(urld):
                        channels.append((channel_name_normalize(name), urld))
                    else:
                        print(f"无效频道URL: {urld}")
        except Exception as e:
            print(f"处理服务器失败: {url} - {str(e)}")
            continue
    
    return channels

def test_speed_and_output(channels, output_prefix="itvlist"):
    task_queue = Queue()
    speed_results = []
    error_channels = []
    total_channels = len(channels)

    def worker():
        while True:
            channel_name, channel_url = task_queue.get()
            speeds = []
            
            try:
                # 获取m3u8文件
                response = requests.get(channel_url, timeout=3)
                if response.status_code != 200:
                    raise Exception(f"HTTP状态码: {response.status_code}")
                
                lines = response.text.strip().split('\n')
                ts_lists = [line for line in lines if not line.startswith('#') and line.endswith(('.ts', '.m3u8'))]
                
                if not ts_lists:
                    raise Exception("未找到有效的TS片段")
                
                base_url = channel_url.rsplit('/', 1)[0] + '/'
                
                # 选择前3个TS片段进行速度测试
                test_ts_urls = [base_url + ts.split('/')[-1] for ts in ts_lists[:3]]
                
                for ts_url in test_ts_urls:
                    try:
                        start_time = time.time()
                        ts_response = requests.get(ts_url, timeout=5)
                        end_time = time.time()
                        
                        if ts_response.status_code == 200 and len(ts_response.content) > 1024:
                            download_speed = len(ts_response.content) / (end_time - start_time) / 1024 / 1024  # MB/s
                            speeds.append(download_speed)
                        else:
                            raise Exception(f"TS下载失败: {ts_response.status_code}")
                    except Exception as e:
                        print(f"测试TS失败: {ts_url} - {str(e)}")
                        continue
                
                if speeds:
                    average_speed = sum(speeds) / len(speeds)
                    speed_results.append((channel_name, channel_url, f"{average_speed:.3f} MB/s"))
                else:
                    error_channels.append((channel_name, channel_url))
                    
            except Exception as e:
                error_channels.append((channel_name, channel_url))
                print(f"速度测试失败: {channel_url} - {str(e)}")
            
            finally:
                completed = len(speed_results) + len(error_channels)
                progress = completed / total_channels * 100
                print(f"进度: {completed}/{total_channels} ({progress:.2f}%) | 有效: {len(speed_results)} | 无效: {len(error_channels)}")
                task_queue.task_done()

    # 启动工作线程
    num_threads = min(50, total_channels)
    for _ in range(num_threads):
        threading.Thread(target=worker, daemon=True).start()

    # 添加所有频道到任务队列
    for channel in channels:
        task_queue.put(channel)
    
    # 等待所有任务完成
    task_queue.join()

    # 按速度排序并筛选每个频道最多10个源
    channel_sources = defaultdict(list)
    for channel_name, channel_url, speed in speed_results:
        channel_sources[channel_name].append((channel_url, speed))

    optimized_sources = []
    for channel_name, sources in channel_sources.items():
        sorted_sources = sorted(sources, key=lambda x: float(x[1].split()[0]), reverse=True)[:10]
        for url, speed in sorted_sources:
            optimized_sources.append((channel_name, url, speed))

    # 去重
    unique_channels = []
    seen = set()
    for channel_name, channel_url, speed in optimized_sources:
        key = (channel_name, channel_url)
        if key not in seen:
            unique_channels.append((channel_name, channel_url, speed))
            seen.add(key)

    # 对频道进行排序
    def custom_sort_key(item):
        name = item[0]
        if name.startswith('CCTV'):
            num = re.search(r'\d+', name)
            if num:
                return (0, int(num.group()))
            return (0, float('inf'))
        return (1, name)

    unique_channels.sort(key=custom_sort_key)

    # 分类规则
    genre_rules = {
        '央视频道': lambda name: 'CCTV' in name,
        '卫视频道': lambda name: any(keyword in name for keyword in ['卫视', 'TV']),
        '国际频道': lambda name: any(keyword in name for keyword in ['BBC', 'CNN', 'NHK', 'FOX', 'DW', 'RT']),
        '体育频道': lambda name: any(keyword in name for keyword in ['体育', '足球', '篮球', 'F1', 'NBA', 'NHL', 'MLB']),
        '电影频道': lambda name: any(keyword in name for keyword in ['电影', 'MOVIE']),
        '少儿频道': lambda name: any(keyword in name for keyword in ['少儿', '动画', 'KIDS']),
        '音乐频道': lambda name: any(keyword in name for keyword in ['音乐', 'MTV']),
        '其他频道': lambda name: not any(rule(name) for rule in genre_rules.values())
    }

    def write_to_file(file, results, genre):
        channel_counters = defaultdict(int)
        for channel_name, channel_url, _ in results:
            if genre_rules[genre](channel_name):
                if channel_counters[channel_name] < 10:
                    file.write(f"{channel_name},{channel_url}\n")
                    channel_counters[channel_name] += 1

    # 创建输出目录
    output_dir = os.path.dirname(output_prefix)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 写入TXT文件
    with open(f"{output_prefix}.txt", 'w', encoding='utf-8') as txt_file:
        for genre in ['央视频道', '卫视频道', '国际频道', '体育频道', '电影频道', '少儿频道', '音乐频道', '其他频道']:
            txt_file.write(f'{genre},#genre#\n')
            write_to_file(txt_file, unique_channels, genre)

    # 写入M3U文件
    with open(f"{output_prefix}.m3u", 'w', encoding='utf-8') as m3u_file:
        m3u_file.write('#EXTM3U\n')
        for genre in ['央视频道', '卫视频道', '国际频道', '体育频道', '电影频道', '少儿频道', '音乐频道', '其他频道']:
            for channel_name, channel_url, speed in unique_channels:
                if genre_rules[genre](channel_name):
                    m3u_file.write(f"#EXTINF:-1 tvg-name=\"{channel_name}\" group-title=\"{genre}\" tvg-logo=\"\",{channel_name}\n")
                    m3u_file.write(f"{channel_url}\n")

    # 写入速度文件
    with open(f"{output_prefix}_speed.txt", 'w', encoding='utf-8') as speed_file:
        for result in unique_channels:
            speed_file.write(f"{','.join(result)}\n")

    # 写入无效频道日志
    with open(f"{output_prefix}_invalid.log", 'w', encoding='utf-8') as log_file:
        for channel_name, channel_url in error_channels:
            log_file.write(f"{channel_name},{channel_url}\n")

    print(f"\n处理完成:")
    print(f"- 总输入频道: {total_channels}")
    print(f"- 有效频道: {len(speed_results)}")
    print(f"- 无效频道: {len(error_channels)}")
    print(f"- 最终输出频道: {len(unique_channels)}")
    print(f"\n结果已保存到:")
    print(f"- {output_prefix}.txt")
    print(f"- {output_prefix}.m3u")
    print(f"- {output_prefix}_speed.txt (包含速度信息)")
    print(f"- {output_prefix}_invalid.log (无效频道日志)")

def main():
    parser = argparse.ArgumentParser(description='IPTV频道批量探测与测速工具')
    parser.add_argument('--jsmpeg', help='jsmpeg-streamer模式CSV文件')
    parser.add_argument('--txiptv', help='txiptv模式CSV文件')
    parser.add_argument('--zhgxtv', help='zhgxtv模式CSV文件')
    parser.add_argument('--output', default='output/itvlist', help='输出文件前缀')
    parser.add_argument('--timeout', type=int, default=3, help='请求超时时间(秒)')
    parser.add_argument('--retries', type=int, default=3, help='重试次数')
    args = parser.parse_args()

    # 检查至少指定了一个CSV文件
    if not any([args.jsmpeg, args.txiptv, args.zhgxtv]):
        print('请至少指定一个CSV文件 (--jsmpeg, --txiptv, --zhgxtv)')
        return

    print(f"开始处理IPTV频道...")
    print(f"- 超时设置: {args.timeout}秒")
    print(f"- 重试次数: {args.retries}次")

    # 获取所有频道
    channels = []
    if args.jsmpeg:
        print(f"\n正在处理JSMPEG模式: {args.jsmpeg}")
        jsmpeg_channels = get_channels_alltv(args.jsmpeg)
        channels.extend(jsmpeg_channels)
        print(f"JSMPEG模式获取到 {len(jsmpeg_channels)} 个有效频道")

    if args.zhgxtv:
        print(f"\n正在处理ZHGXT模式: {args.zhgxtv}")
        zhgxtv_channels = get_channels_hgxtv(args.zhgxtv)
        channels.extend(zhgxtv_channels)
        print(f"ZHGXT模式获取到 {len(zhgxtv_channels)} 个有效频道")

    if args.txiptv:
        print(f"\n正在处理TXIPTV模式: {args.txiptv}")
        txiptv_channels = asyncio.run(get_channels_newnew(args.txiptv))
        channels.extend(txiptv_channels)
        print(f"TXIPTV模式获取到 {len(txiptv_channels)} 个有效频道")

    # 去重
    unique_channels = []
    seen = set()
    for channel in channels:
        key = (channel[0], channel[1])
        if key not in seen:
            unique_channels.append(channel)
            seen.add(key)

    print(f"\n总共有 {len(unique_channels)} 个唯一频道需要测试")
    
    if unique_channels:
        test_speed_and_output(unique_channels, args.output)
    else:
        print("没有找到有效的频道源")

if __name__ == "__main__":
    main()
